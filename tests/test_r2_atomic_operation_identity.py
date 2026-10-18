"""Component-local synthetic tests for the opt-in atomic-route operation key (finding F-5 of the orchestrator gap report).

No Control Plane package is needed here: the derivation from a persisted decision and the end-to-end renewal case (cap 2,
profile off => two effects, key on => one effect and `operation_already_committed`) are covered cross-component in
cogno-us/cognous-stack-qualification suites/operation-identity/test_atomic_route_operation_identity.py.

Provenance: test_default_path_* ran on main d90a2cae before this change and pass there and here; the rest describe new opt-in
behaviour and have no expected-today value.
"""
import dataclasses
import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from engine.atomic_local_control_plane_executor import AtomicLocalControlPlaneExecutor
from engine.local_authority_effect import AtomicAuthorityEffectDestination
from engine.operation_identity_atomic_destination import OperationIdentityAtomicAuthorityEffectDestination
from engine.operation_identity_destination import CAPABILITY
from engine.safe_executor import EXECUTION_ENVELOPE_VERSION, DurableRefundDestination, ExecutionEnvelope, snapshot_envelope
from test_producer_contract import operation, policy

OP = "op-order-100/refund-1"


def _clock():
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


class _Destination(DurableRefundDestination):
    """Strict stand-in: a destination that does not know operation_id (the default-path call shape)."""

    def __init__(self, root):
        super().__init__(root)
        self.calls = []

    def execute_claim_atomic(self, claim_id, snapshot, *, attempt_id, simulate=None):
        self.calls.append((claim_id, attempt_id, simulate))
        return {"observation": {"state": "applied"}, "linearized_at": "t", "authority_receipt": {}}


class _CapableDestination(_Destination):
    operation_identity_capability = CAPABILITY


def _envelope(op=None):
    op = op or operation()
    return ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)


def test_default_path_calls_the_destination_without_an_operation_id(tmp_path):
    dest = _Destination(tmp_path / "state")
    executor = AtomicLocalControlPlaneExecutor(workflow=None, destination=dest, policy=policy(operation()))
    assert executor.operation_identity is False
    assert executor.execute(envelope=_envelope(), claim_id="claim-1").status == "executed"
    assert len(dest.calls) == 1


def test_default_path_signature_gains_only_an_optional_operation_id():
    params = inspect.signature(AtomicAuthorityEffectDestination.execute_claim_atomic).parameters
    assert params["operation_id"].default is None and params["operation_id"].kind is inspect.Parameter.KEYWORD_ONLY
    init = inspect.signature(AtomicLocalControlPlaneExecutor.__init__).parameters
    assert init["operation_identity"].default is False


def test_base_atomic_destination_has_no_operation_table_or_capability(tmp_path):
    dest = AtomicAuthorityEffectDestination(tmp_path / "state", clock=_clock)
    assert not hasattr(dest, "operation_identity_capability")
    assert "operation_effects_v1" not in {r[0] for r in dest._connect().execute("SELECT name FROM sqlite_master")}


def test_key_on_with_a_destination_that_cannot_store_it_is_refused_and_recorded(tmp_path):
    dest = _Destination(tmp_path / "state")
    executor = AtomicLocalControlPlaneExecutor(workflow=None, destination=dest, policy=policy(operation()), operation_identity=True)
    result = executor.execute(envelope=_envelope(), claim_id="claim-1")
    assert (result.status, result.attempted, result.error) == ("denied", False, "operation_identity_capability_unavailable")
    assert dest.calls == []
    [failure] = dest.failure_history("effect-1")
    assert (failure["failure_class"], failure["stage"]) == ("authority_hold", "pre_dispatch")
    assert "operation_identity_capability_unavailable" in failure["detail"]


@pytest.mark.parametrize("workflow", [None, SimpleNamespace(runtime_authority_profile=None),
                                      SimpleNamespace(runtime_authority_profile=SimpleNamespace(operation_identity=None))])
def test_key_on_without_the_control_plane_identity_profile_is_refused_not_guessed(tmp_path, workflow):
    dest = _CapableDestination(tmp_path / "state")
    executor = AtomicLocalControlPlaneExecutor(workflow=workflow, destination=dest, policy=policy(operation()), operation_identity=True)
    result = executor.execute(envelope=_envelope(), claim_id="claim-1")
    assert (result.status, result.error) == ("denied", "operation_identity_profile_unavailable")
    assert dest.calls == [] and dest.failure_history("effect-1")


def _snap(effect_id, **changes):
    op = dataclasses.replace(operation(), **changes) if changes else operation()
    return op, snapshot_envelope(ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-" + effect_id, effect_id, op))


def test_atomic_guard_refuses_a_second_effect_for_the_same_operation_and_scopes_by_principal(tmp_path):
    dest = OperationIdentityAtomicAuthorityEffectDestination(tmp_path / "state", clock=_clock)
    assert dest.operation_identity_capability == CAPABILITY
    op, a = _snap("effect-a")
    _, b = _snap("effect-b")
    _, other = _snap("effect-c", principal="urn:cognous:principal:other-service")

    def attempt(snapshot, record=True):
        with dest._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            dest._operation_guard(conn, snapshot, OP)
            if record:
                dest._operation_record(conn, snapshot, OP)
            conn.execute("COMMIT")
    attempt(a)
    with pytest.raises(PermissionError, match="operation_already_committed"):
        attempt(b)
    attempt(other)  # same id, different principal: a different operation
    attempt(a, record=False)  # the same effect id is not an operation conflict
    assert dest.operation_effect((op.principal, op.authority_domain, OP)) == a.effect_id
