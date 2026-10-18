"""Component-local synthetic tests for the opt-in dedupe-ttl/0.1 destination capability; no network effects, no real provider.

Provenance: test_legacy_destinations_declare_no_dedupe_capability was RUN on main 6bfb77ee before this change (it passes there and
here). Every other row describes new opt-in behaviour and has no "expected today" value.
"""
import dataclasses

import pytest

from engine.dedupe_destination import CAPABILITY, IN_PROGRESS, DedupeTtlOperationDestination, InProgress
from engine.delayed_commit_destination import DelayedOperationIdentityDestination
from engine.operation_identity_destination import OperationIdentityDestination
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION, DurableRefundDestination, ExecutionEnvelope, LocalDestinationExecutor, snapshot_envelope)
from test_producer_contract import operation, policy

OP = "op-order-100/refund-1"


class Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


def snap(effect_id):
    op = dataclasses.replace(operation(), effective_max_effects=2)
    return op, snapshot_envelope(ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-" + effect_id, effect_id, op))


def dest(tmp_path, clock=None, ttl=600):
    clock = clock or Clock()
    return DedupeTtlOperationDestination(tmp_path / "d", dedupe_ttl_seconds=ttl, clock=clock), clock


def test_legacy_destinations_declare_no_dedupe_capability(tmp_path):
    for cls in (DurableRefundDestination, OperationIdentityDestination, DelayedOperationIdentityDestination):
        d = cls(tmp_path / cls.__name__)
        assert not hasattr(d, "dedupe_capability") and not hasattr(d, "dedupe_ttl_seconds")


def test_destination_declares_the_capability_and_its_ttl(tmp_path):
    d, _ = dest(tmp_path, ttl=300)
    assert d.dedupe_capability == CAPABILITY == "dedupe-ttl/0.1" and d.dedupe_ttl_seconds == 300
    for bad in (0, -1, "600", 1.5):
        with pytest.raises(ValueError):
            DedupeTtlOperationDestination(tmp_path / "bad", dedupe_ttl_seconds=bad)


def test_it_still_dedupes_on_the_operation_id_inside_the_ttl(tmp_path):
    op, a = snap("effect-a")
    _, b = snap("effect-b")
    d, clock = dest(tmp_path)
    assert d.commit(a, operation_id=OP)["duplicate"] is False
    clock.now += 599
    with pytest.raises(PermissionError, match="operation_already_committed"):
        d.commit(b, operation_id=OP)
    assert d.commit(a, operation_id=OP)["duplicate"] is True, "the same effect id is the same effect"
    assert d.effect_count(op.grant_id) == 1


def test_the_key_is_forgotten_after_its_ttl_and_a_second_effect_lands(tmp_path):
    """What the control plane's escalation deadline (inside the key TTL) has to beat."""
    op, a = snap("effect-a")
    _, b = snap("effect-b")
    d, clock = dest(tmp_path)
    d.commit(a, operation_id=OP)
    clock.now += 600
    assert d.commit(b, operation_id=OP)["duplicate"] is False
    assert d.effect_count(op.grant_id) == 2
    assert d.operation_effect((op.principal, op.authority_domain, OP)) == b.effect_id


def test_deferred_commit_marks_in_progress_then_land_completes_it(tmp_path):
    op, a = snap("effect-a")
    _, b = snap("effect-b")
    d, clock = dest(tmp_path)
    d.defer_next_commit()
    with pytest.raises(TimeoutError) as lost:
        d.commit(a, operation_id=OP)
    assert not isinstance(lost.value, InProgress), "the first request is accepted; only its acknowledgement is lost"
    assert d.observe(a.effect_id)["state"] == "absent" and d.effect_count(op.grant_id) == 0
    for effect in (b, a):  # another key, and the same key again: the destination says in progress
        with pytest.raises(InProgress) as held:
            d.commit(effect, operation_id=OP)
        assert str(held.value) == IN_PROGRESS and isinstance(held.value, TimeoutError)
    assert d.effect_count(op.grant_id) == 0, "in progress created no effect"
    assert d.land()["duplicate"] is False and d.effect_count(op.grant_id) == 1
    with pytest.raises(PermissionError, match="operation_already_committed"):
        d.commit(b, operation_id=OP)
    assert d.operation_effect((op.principal, op.authority_domain, OP)) == a.effect_id
    assert d.commit(a, operation_id=OP)["duplicate"] is True, "landing cleared the hold: a resubmission is a duplicate, not in progress"
    with d._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM dedupe_in_progress_v1").fetchone()[0] == 0, "the hold row is removed once landed"


def test_an_in_progress_hold_expires_with_the_ttl(tmp_path):
    op, a = snap("effect-a")
    _, b = snap("effect-b")
    d, clock = dest(tmp_path)
    d.defer_next_commit()
    with pytest.raises(TimeoutError):
        d.commit(a, operation_id=OP)
    clock.now += 600
    assert d.commit(b, operation_id=OP)["duplicate"] is False, "the hold is older than the TTL and is ignored"


def test_executor_reports_unknown_with_the_in_progress_error(tmp_path):
    op, a = snap("effect-a")
    _, b = snap("effect-b")
    d, clock = dest(tmp_path)
    executor = LocalDestinationExecutor(d, dataclasses.replace(policy(op), max_effects=2))
    d.defer_next_commit()
    first = executor.execute_snapshot(a, operation_id=OP)
    assert first.status == "unknown" and first.observation["state"] == "absent"
    second = executor.execute_snapshot(b, operation_id=OP)
    assert second.status == "unknown" and second.error == IN_PROGRESS and second.observation["state"] == "absent"
    assert d.effect_count(op.grant_id) == 0


# ---------------------------------------------------------------- the control plane adapter (opt-in forwarding)
def _adapter_for(snapshot, destination):
    from test_adapter_forwarding import _adapter
    return _adapter(snapshot, destination)


def test_adapter_forwards_the_declared_capability_and_ttl_and_nothing_for_a_legacy_destination(tmp_path):
    _, a = snap("effect-a")
    capable, _ = dest(tmp_path / "t", ttl=300)
    adapter = _adapter_for(a, capable)
    assert adapter.dedupe_capability == CAPABILITY and adapter.dedupe_ttl_seconds == 300
    legacy = _adapter_for(a, OperationIdentityDestination(tmp_path / "legacy"))
    assert legacy.dedupe_capability is None and legacy.dedupe_ttl_seconds is None


def test_adapter_raises_the_control_plane_in_progress_signal_and_not_a_failure(tmp_path, monkeypatch):
    """The destination's in-progress answer reaches the control plane as DestinationInProgress (an unknown outcome that mints no new
    key), not as a refusal or an ordinary lost acknowledgement. Without the control plane profile module it stays an unknown."""
    import sys
    import types

    class DefinitiveDestinationRejection(Exception):
        pass

    class DestinationInProgress(Exception):
        pass
    bounded = types.ModuleType("agent_control_plane.bounded")
    bounded.DefinitiveDestinationRejection = DefinitiveDestinationRejection
    dedupe = types.ModuleType("agent_control_plane.dedupe")
    dedupe.DestinationInProgress = DestinationInProgress
    monkeypatch.setitem(sys.modules, "agent_control_plane", types.ModuleType("agent_control_plane"))
    monkeypatch.setitem(sys.modules, "agent_control_plane.bounded", bounded)
    monkeypatch.setitem(sys.modules, "agent_control_plane.dedupe", dedupe)
    from test_adapter_forwarding import _apply
    _, a = snap("effect-a")
    _, b = snap("effect-b")
    d, _ = dest(tmp_path)
    d.defer_next_commit()
    with pytest.raises(TimeoutError):
        _apply(_adapter_for(a, d), operation_id=OP)
    with pytest.raises(DestinationInProgress):
        _apply(_adapter_for(b, d), operation_id=OP)
