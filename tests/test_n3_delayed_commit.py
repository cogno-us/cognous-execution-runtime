"""Component-local synthetic tests for the opt-in delayed-commit/0.1 destination mode; no network effects.

Provenance: test_legacy_destinations_have_no_delayed_commit_surface was RUN on main 63a7c0fe before this change (it passes
there and here). Every other row describes new opt-in behaviour and has no "expected today" value.
"""
import dataclasses

import pytest

from engine.delayed_commit_destination import (
    CAPABILITY, DelayedCommitDestination, DelayedEffectiveGrantDestination, DelayedOperationIdentityDestination,
    DelayedOperationIdentityEffectiveGrantDestination, delayed)
from engine.effective_grant import DERIVATION
from engine.operation_identity_destination import OperationIdentityDestination
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION, DurableRefundDestination, ExecutionEnvelope, LocalDestinationExecutor, snapshot_envelope)
from test_producer_contract import operation, policy

OP = "op-order-100/refund-1"


def snap(effect_id, **changes):
    op = dataclasses.replace(operation(), **changes) if changes else operation()
    return op, snapshot_envelope(ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-" + effect_id, effect_id, op))


def test_legacy_destinations_have_no_delayed_commit_surface(tmp_path):
    for cls in (DurableRefundDestination, OperationIdentityDestination):
        dest = cls(tmp_path / cls.__name__)
        assert not hasattr(dest, "delayed_commit_capability") and not hasattr(dest, "pause_at") and not hasattr(dest, "land")
    _, a = snap("effect-a")
    assert DurableRefundDestination(tmp_path / "plain").commit(a)["duplicate"] is False


def test_delayed_destination_declares_the_capability_and_behaves_as_the_base_until_asked(tmp_path):
    op, a = snap("effect-a")
    dest = DelayedCommitDestination(tmp_path / "d")
    assert dest.delayed_commit_capability == CAPABILITY == "delayed-commit/0.1"
    assert isinstance(dest, DurableRefundDestination)
    assert dest.commit(a)["duplicate"] is False and dest.commit(a)["duplicate"] is True
    assert dest.effect_count(op.grant_id) == 1 and dest.in_flight == []


def test_deferred_commit_loses_its_ack_is_absent_until_it_lands_then_is_one_effect(tmp_path):
    op, a = snap("effect-a")
    dest = DelayedCommitDestination(tmp_path / "d")
    dest.defer_next_commit()
    with pytest.raises(TimeoutError):
        dest.commit(a)
    assert dest.observe(a.effect_id)["state"] == "absent" and dest.effect_count(op.grant_id) == 0 and len(dest.in_flight) == 1
    assert dest.commit(a)["duplicate"] is False, "the deferral was one-shot: a second call commits normally"
    landed = dest.land()
    assert landed["duplicate"] is True and dest.effect_count(op.grant_id) == 1 and dest.in_flight == []
    with pytest.raises(LookupError):
        dest.land()


def test_land_runs_the_real_commit_path_so_operation_dedupe_still_refuses_a_late_commit(tmp_path):
    op, a = snap("effect-a")
    _, b = snap("effect-b")
    dest = DelayedOperationIdentityDestination(tmp_path / "d")
    dest.defer_next_commit()
    with pytest.raises(TimeoutError):
        dest.commit(a, operation_id=OP)  # r1: acknowledgement lost, not durable
    dest.commit(b, operation_id=OP)      # r2 (renewed grant, other effect id) commits first
    with pytest.raises(PermissionError, match="operation_already_committed"):
        dest.land()                      # r1 lands late: refused in the same transaction as the write
    assert dest.effect_count(op.grant_id) == 1 and dest.observe(a.effect_id)["state"] == "absent"
    assert dest.operation_effect((op.principal, op.authority_domain, OP)) == b.effect_id


def test_land_all_lands_in_order(tmp_path):
    _, a = snap("effect-a")
    dest = DelayedCommitDestination(tmp_path / "d")
    dest.defer_next_commit()
    with pytest.raises(TimeoutError):
        dest.commit(a)
    assert [x["duplicate"] for x in dest.land_all()] == [False] and dest.land_all() == []


def test_pause_points_fire_once_in_the_named_place(tmp_path):
    op, a = snap("effect-a")
    dest = DelayedCommitDestination(tmp_path / "d")
    seen = []
    dest.pause_at("observe", lambda: seen.append(("observe", dest.effect_count(op.grant_id))))
    dest.pause_at("commit", lambda: seen.append(("commit", dest.effect_count(op.grant_id))))
    dest.observe_bound(a)
    dest.observe_bound(a)
    dest.commit(a)
    dest.commit(a)
    assert seen == [("observe", 0), ("commit", 0)], "each point fires once, before its operation lands"
    with pytest.raises(ValueError):
        dest.pause_at("after-commit", lambda: None)


def test_pause_at_commit_can_run_another_request_inside_a_stalled_one(tmp_path):
    op, a = snap("effect-a")
    _, b = snap("effect-b")
    dest = DelayedOperationIdentityDestination(tmp_path / "d")
    dest.pause_at("commit", lambda: dest.commit(b, operation_id=OP))
    with pytest.raises(PermissionError, match="operation_already_committed"):
        dest.commit(a, operation_id=OP)
    assert dest.observe(b.effect_id)["state"] == "applied" and dest.observe(a.effect_id)["state"] == "absent"


def test_executor_reports_a_deferred_commit_as_unknown_not_applied(tmp_path):
    op, a = snap("effect-a")
    dest = DelayedCommitDestination(tmp_path / "d")
    dest.defer_next_commit()
    result = LocalDestinationExecutor(dest, policy(op)).execute_snapshot(a)
    assert result.status == "unknown" and not result.acknowledged and dest.effect_count(op.grant_id) == 0


def test_effective_grant_precondition_still_applies_to_a_late_commit(tmp_path):
    op, a = snap("effect-a")
    dest = DelayedEffectiveGrantDestination(tmp_path / "d")
    digest = dest.set_effective_grant(principal=op.principal, grant_id=op.grant_id, revision=op.grant_revision,
                                      permissions=list(op.requested_permissions))
    dest.defer_next_commit()
    with pytest.raises(TimeoutError):
        dest.commit(a, expected_effective_grant_digest=digest, effective_grant_derivation_version=DERIVATION)
    dest.set_effective_grant(principal=op.principal, grant_id=op.grant_id, revision="2",
                             permissions=list(op.requested_permissions))  # renewed while r1 was in flight
    with pytest.raises(PermissionError, match="effective_grant_changed_at_commit"):
        dest.land()
    assert dest.effect_count(op.grant_id) == 0


def test_factory_selects_the_class_by_profile_flags(tmp_path):
    assert delayed() is DelayedCommitDestination
    assert delayed(operation_identity=True) is DelayedOperationIdentityDestination
    assert delayed(effective_grant=True) is DelayedEffectiveGrantDestination
    both = delayed(operation_identity=True, effective_grant=True)
    assert both is DelayedOperationIdentityEffectiveGrantDestination
    dest = both(tmp_path / "d")
    assert dest.operation_identity_capability == "operation-identity/0.1" and dest.runtime_authority_capability
    assert dest.delayed_commit_capability == CAPABILITY
