"""Post-commit faults on the atomic route report an unresolved outcome, never a pre-dispatch refusal.

Fault points: after COMMIT (hook and connection level), during the post-commit observation, during reconciliation, and
during failure-record persistence. In every case the effect is committed exactly once and the result is ``unknown`` with
the attempt retained, ``acknowledged`` false, ``retry_eligible`` false and no fresh effect after a restart.

Needs the Control Plane local-authority-effect contract on PYTHONPATH for real claims (same guard as
test_atomic_route_valid_flow_identity.py); the executor-level cases at the end use a stub destination and always run.
"""
from __future__ import annotations

import sqlite3

import pytest

from engine.atomic_local_control_plane_executor import AtomicLocalControlPlaneExecutor
from engine.local_authority_effect import AtomicAuthorityEffectDestination
from engine.producer_contract import _validate_result_state
from engine.safe_executor import EXECUTION_ENVELOPE_VERSION, DurableRefundDestination, ExecutionEnvelope
from test_producer_contract import operation as stub_operation, policy as stub_policy

try:
    import agent_control_plane.local_authority_effect  # noqa: F401
    HAVE_CP = True
except ModuleNotFoundError:
    HAVE_CP = False

needs_cp = pytest.mark.skipif(not HAVE_CP, reason="needs the Control Plane local-authority-effect contract on PYTHONPATH")


def _helpers():
    import test_atomic_route_valid_flow_identity as flow
    return flow


class _CommitProxy:
    """Connection wrapper that completes the real COMMIT and then raises, like a failure after the durable write."""

    def __init__(self, conn, armed):
        self._conn, self._armed = conn, armed

    def execute(self, sql, *args):
        result = self._conn.execute(sql, *args)
        if sql == "COMMIT" and self._armed["on"]:
            raise sqlite3.OperationalError("disk I/O error after commit")
        return result

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class HookFault(AtomicAuthorityEffectDestination):
    def _transaction_stage(self, stage):
        if stage == "after_commit":
            raise RuntimeError("hook failed after commit")


class CommitFault(AtomicAuthorityEffectDestination):
    armed = {"on": False}

    def _connect(self):
        return _CommitProxy(super()._connect(), self.armed)


class ObserveFault(AtomicAuthorityEffectDestination):
    def observe_bound(self, snapshot):
        raise ConnectionError("observation backend unavailable")


class ObserveAndReconcileFault(ObserveFault):
    def reconcile_claim(self, claim_id, snapshot):
        raise ConnectionError("reconciliation backend unavailable")


class FailureRecordFault(HookFault):
    def record_failure(self, *args, **kwargs):
        raise sqlite3.OperationalError("failure record store unavailable")


def _effects(dest):
    with sqlite3.connect(dest.path) as conn:
        return [r[0] for r in conn.execute("SELECT effect_id FROM effects")]


def _run(tmp_path, cls):
    flow = _helpers()
    env, dest, claim, ex = flow.setup(tmp_path, cls=cls)
    if cls is CommitFault:
        CommitFault.armed["on"] = True
    try:
        result = ex.execute(envelope=env, claim_id=claim.claim_id, attempt_id="attempt-1")
    finally:
        CommitFault.armed["on"] = False
    return env, dest, claim, ex, result


def _assert_unresolved(dest, env, claim, result):
    assert (result.status, result.attempted, result.acknowledged, result.newly_executed) == ("unknown", True, False, False)
    assert result.attempt_id == "attempt-1"
    assert result.control_plane_evidence["failure_class"] == "dispatch_error"
    assert _effects(dest) == [env.effect_id]  # committed exactly once
    assert dest.claim_state(claim.claim_id) == "consumed"
    assert dest.budget_state("budget-1")["used_effects"] == 1
    _validate_result_state(result)


@needs_cp
def test_valid_flow_control_is_unchanged(tmp_path):
    flow = _helpers()
    env, dest, claim, ex = flow.setup(tmp_path)
    result = ex.execute(envelope=env, claim_id=claim.claim_id, attempt_id="attempt-1")
    assert (result.status, result.acknowledged, result.newly_executed, result.observed_state) == ("executed", True, True, "applied")
    assert _effects(dest) == [env.effect_id]


@needs_cp
def test_fault_in_the_after_commit_hook_is_unresolved_not_denied(tmp_path):
    env, dest, claim, ex, result = _run(tmp_path, HookFault)
    _assert_unresolved(dest, env, claim, result)
    assert result.observed_state == "applied"  # exact claim reconciliation sees the durable effect
    assert result.observation["retry_eligible"] is False
    [failure] = dest.failure_history(env.effect_id)
    assert (failure["stage"], failure["attempt_id"]) == ("post_dispatch", "attempt-1")


@needs_cp
def test_fault_after_the_connection_level_commit_is_unresolved_not_denied(tmp_path):
    env, dest, claim, ex, result = _run(tmp_path, CommitFault)
    _assert_unresolved(dest, env, claim, result)
    assert result.observed_state == "applied"
    assert result.observation["retry_eligible"] is False


@needs_cp
def test_fault_during_observation_is_unresolved(tmp_path):
    env, dest, claim, ex, result = _run(tmp_path, ObserveFault)
    _assert_unresolved(dest, env, claim, result)
    assert dest.failure_history(env.effect_id)[0]["reason_code"] == "post_commit_observation_unavailable"


@needs_cp
def test_fault_during_observation_and_reconciliation_stays_unknown(tmp_path):
    env, dest, claim, ex, result = _run(tmp_path, ObserveAndReconcileFault)
    _assert_unresolved(dest, env, claim, result)
    assert result.observed_state == "unknown"
    assert result.observation == {"status": "unknown", "retry_eligible": False, "reason": "reconciliation_unavailable"}


@needs_cp
def test_fault_while_persisting_the_failure_record_still_reports_unresolved(tmp_path):
    env, dest, claim, ex, result = _run(tmp_path, FailureRecordFault)
    _assert_unresolved(dest, env, claim, result)
    assert result.control_plane_evidence["failure_record_id"] is None
    assert result.control_plane_evidence["failure_record_unavailable"] is True


@needs_cp
@pytest.mark.parametrize("cls", [HookFault, CommitFault, ObserveFault, ObserveAndReconcileFault, FailureRecordFault])
def test_restart_after_a_post_commit_fault_creates_no_fresh_effect(tmp_path, cls):
    env, dest, claim, ex, result = _run(tmp_path, cls)
    restarted = AtomicAuthorityEffectDestination(tmp_path / "atomic", clock=lambda: _helpers().BASE)
    again = AtomicLocalControlPlaneExecutor(workflow=None, destination=restarted, policy=ex.policy)
    second = again.execute(envelope=env, claim_id=claim.claim_id, attempt_id="attempt-2")
    assert second.status == "denied" and second.newly_executed is False
    assert _effects(restarted) == [env.effect_id]
    assert restarted.budget_state("budget-1")["used_effects"] == 1
    assert restarted.reconcile_claim(claim.claim_id, _helpers().snapshot_envelope(env))["status"] == "applied"


# --- executor level, stub destination (always runs) -------------------------------------------------------------------

class _Stub(DurableRefundDestination):
    def __init__(self, root, *, raises, reconcile):
        super().__init__(root)
        self.raises, self._reconcile = raises, reconcile

    def execute_claim_atomic(self, claim_id, snapshot, *, attempt_id, simulate=None):
        raise self.raises

    def reconcile_claim(self, claim_id, snapshot):
        if isinstance(self._reconcile, Exception):
            raise self._reconcile
        return self._reconcile


NO_EFFECT = {"status": "hold", "claim_state": "issued", "retry_eligible": False, "reason": "no_durable_effect_for_claim"}


def _stub_run(tmp_path, raises, reconcile):
    op = stub_operation()
    dest = _Stub(tmp_path / "state", raises=raises, reconcile=reconcile)
    ex = AtomicLocalControlPlaneExecutor(workflow=None, destination=dest, policy=stub_policy(op))
    return dest, ex.execute(envelope=ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op), claim_id="claim-1")


def test_non_timeout_error_with_a_durable_effect_is_unresolved(tmp_path):
    dest, result = _stub_run(tmp_path, RuntimeError("late failure"),
                             {"status": "applied", "claim_state": "consumed", "retry_eligible": False})
    assert (result.status, result.attempted, result.observed_state) == ("unknown", True, "applied")
    assert result.observation["retry_eligible"] is False
    assert dest.failure_history("effect-1")[0]["stage"] == "post_dispatch"
    assert result.error == "RuntimeError" and dest.failure_history("effect-1")[0]["detail"] == "RuntimeError"  # type name only


@pytest.mark.parametrize("reconcile", [ConnectionError("down"),
                                       {"status": "hold", "claim_state": "consumed", "retry_eligible": False,
                                        "reason": "retained_effect_operation_binding_mismatch"}])
def test_non_timeout_error_with_unestablished_state_is_unresolved(tmp_path, reconcile):
    dest, result = _stub_run(tmp_path, RuntimeError("late failure"), reconcile)
    assert (result.status, result.attempted, result.observed_state) == ("unknown", True, "unknown")
    assert dest.failure_history("effect-1")[0]["stage"] == "post_dispatch"


def test_non_timeout_error_with_established_no_effect_stays_denied(tmp_path):
    dest, result = _stub_run(tmp_path, RuntimeError("boom before commit"), NO_EFFECT)
    assert (result.status, result.attempted) == ("denied", False)
    assert dest.failure_history("effect-1")[0]["reason_code"] == "dispatch_exception"
