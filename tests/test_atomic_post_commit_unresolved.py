"""AtomicLocalControlPlaneExecutor reports a post-commit observation failure as unresolved, never denied (issue #96 / F-08).

Component-local and synthetic: the destination is a DurableRefundDestination whose atomic claim path is replaced,
so no Control Plane package is needed. The real destination raising the same exception after COMMIT is covered by the
cross-component scenario test_scn_c06_route_b_post_commit_observation_failure_is_reported_unresolved_not_denied.
"""
from __future__ import annotations

import pytest

from engine.atomic_local_control_plane_executor import AtomicLocalControlPlaneExecutor
from engine.local_authority_effect import PostCommitObservationUnavailable
from engine.safe_executor import EXECUTION_ENVELOPE_VERSION, DurableRefundDestination, ExecutionEnvelope
from test_producer_contract import operation, policy


class _Destination(DurableRefundDestination):
    def __init__(self, root, *, raises, reconcile=None):
        super().__init__(root)
        self.raises, self._reconcile, self.calls = raises, reconcile, 0

    def execute_claim_atomic(self, claim_id, snapshot, *, attempt_id, simulate=None):
        self.calls += 1
        raise self.raises

    def reconcile_claim(self, claim_id, snapshot):
        if isinstance(self._reconcile, Exception):
            raise self._reconcile
        return self._reconcile or {"status": "applied", "claim_state": "consumed", "retry_eligible": False}


def _run(tmp_path, **kwargs):
    op = operation()
    dest = _Destination(tmp_path / "state", **kwargs)
    executor = AtomicLocalControlPlaneExecutor(workflow=None, destination=dest, policy=policy(op))
    envelope = ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)
    result = executor.execute(envelope=envelope, claim_id="claim-1")
    return dest, result


def test_post_commit_observation_failure_is_unresolved_not_denied(tmp_path):
    dest, result = _run(tmp_path, raises=PostCommitObservationUnavailable("post-commit observation unavailable"))
    assert (result.status, result.attempted, result.acknowledged) == ("unknown", True, False)
    assert result.attempt_id and result.newly_executed is False
    assert result.observed_state == "applied"  # exact claim reconciliation sees the durable effect
    [failure] = dest.failure_history("effect-1")
    assert (failure["failure_class"], failure["stage"], failure["reason_code"]) == (
        "dispatch_error", "post_dispatch", "post_commit_observation_unavailable")
    assert failure["attempt_id"] == result.attempt_id


def test_unresolved_stays_unresolved_when_reconciliation_also_fails(tmp_path):
    dest, result = _run(tmp_path, raises=PostCommitObservationUnavailable("down"), reconcile=ConnectionError("down"))
    assert (result.status, result.attempted, result.observed_state) == ("unknown", True, "unknown")
    assert dest.failure_history("effect-1")[0]["stage"] == "post_dispatch"


def test_lost_ack_keeps_its_reason_code(tmp_path):
    dest, result = _run(tmp_path, raises=TimeoutError("synthetic acknowledgement unavailable"))
    assert (result.status, result.attempted) == ("unknown", True)
    assert dest.failure_history("effect-1")[0]["reason_code"] == "acknowledgement_unavailable"


@pytest.mark.parametrize("exc,reason", [(PermissionError("execution claim is not usable"), "current_authority_rejected"),
                                        (RuntimeError("boom before commit"), "dispatch_exception")])
def test_pre_commit_failures_remain_denied(tmp_path, exc, reason):
    dest, result = _run(tmp_path, raises=exc)
    assert (result.status, result.attempted) == ("denied", False)
    assert dest.failure_history("effect-1")[0]["reason_code"] == reason
