"""Result consistency on the direct destination route: an error after the durable commit reports unknown.

A non-timeout exception raised after the effect row is committed must not be reported as ``failed``: the effect may
exist. The atomic route already reports such errors as ``unknown``. A failure before the commit (no durable effect)
keeps its ``failed`` result, and a valid flow is unchanged.
"""
from __future__ import annotations

import pytest

from engine.producer_contract import export_execution_artifacts
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION, DurableRefundDestination, ExecutionEnvelope, LocalDestinationExecutor,
    snapshot_envelope)
from test_producer_contract import operation, policy


class ProviderError(RuntimeError):
    pass


class RaisesAfterCommit(DurableRefundDestination):
    def __init__(self, root, exc):
        super().__init__(root)
        self.exc = exc

    def commit(self, snapshot, **kwargs):
        super().commit(snapshot, **kwargs)
        raise self.exc


class RaisesAfterCommitThenObserveFails(RaisesAfterCommit):
    def __init__(self, root, exc):
        super().__init__(root, exc)
        self.armed = False

    def commit(self, snapshot, **kwargs):
        try:
            super().commit(snapshot, **kwargs)
        finally:
            self.armed = True

    def observe_bound(self, snapshot):
        if self.armed:
            raise ProviderError("observation unavailable")
        return super().observe_bound(snapshot)


class RaisesBeforeCommit(DurableRefundDestination):
    def commit(self, snapshot, **kwargs):
        raise ProviderError("rejected before any write")


def run(dest):
    op = operation()
    env = ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)
    return env, LocalDestinationExecutor(dest, policy(op)).execute_snapshot(snapshot_envelope(env))


@pytest.mark.parametrize("exc", [ProviderError("x"), OSError("x"), ValueError("x")])
def test_non_timeout_error_after_commit_is_unknown_not_failed(tmp_path, exc):
    dest = RaisesAfterCommit(tmp_path / "d", exc)
    env, result = run(dest)
    assert dest.observe("effect-1")["state"] == "applied"
    assert result.status == "unknown"
    assert (result.attempted, result.acknowledged, result.newly_executed) == (True, False, False)
    assert result.error == type(exc).__name__
    assert dest.attempt_history(result.attempt_id)[-1]["status"] == "unknown"
    exported = export_execution_artifacts(env, result, dest, repository_revision="test")
    assert exported["execution_result"]["status"] == "unknown"


def test_non_timeout_error_after_commit_with_failing_observation_is_unknown(tmp_path):
    dest = RaisesAfterCommitThenObserveFails(tmp_path / "d", ProviderError("x"))
    _, result = run(dest)
    assert result.status == "unknown"
    assert result.observed_state == "unknown" and result.observation is None
    assert (result.acknowledged, result.newly_executed) == (False, False)


def test_control_error_before_any_write_stays_failed(tmp_path):
    dest = RaisesBeforeCommit(tmp_path / "d")
    _, result = run(dest)
    assert result.status == "failed"
    assert dest.observe("effect-1")["state"] == "absent"


def test_control_valid_flow_is_unchanged(tmp_path):
    dest = DurableRefundDestination(tmp_path / "d")
    _, result = run(dest)
    assert (result.status, result.acknowledged, result.newly_executed) == ("executed", True, True)
