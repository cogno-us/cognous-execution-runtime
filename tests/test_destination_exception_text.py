"""Component-local tests: attempt events and results record a destination exception by its type only.

The control plane records a destination exception as its class name. The runtime now does the same wherever it persists
or returns an exception raised by a destination call (attempt_events.error, ExecutionResult.error, failure detail on the
atomic route). PermissionError is the runtime's typed refusal channel and keeps its stable reason text, because callers map
those codes (for example operation_already_committed). Lost-acknowledgement (TimeoutError) paths are unchanged.
The export shape is unchanged: `error` stays a string member; only its content is the type name.

Provenance: test_default_* rows ran on main eeb4583a before this change and pass there and here.
"""
from __future__ import annotations

import pytest

from engine.atomic_local_control_plane_executor import AtomicLocalControlPlaneExecutor
from engine.producer_contract import export_execution_artifacts
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION, DurableRefundDestination, ExecutionEnvelope, LocalDestinationExecutor,
    destination_error_text, snapshot_envelope)
from test_producer_contract import operation, policy

DETAIL = "provider said: request 42 rejected"


class ProviderError(RuntimeError):
    pass


class FailingCommit(DurableRefundDestination):
    def __init__(self, root, exc):
        super().__init__(root)
        self.exc = exc

    def commit(self, snapshot, **kwargs):
        raise self.exc


class FailingObserve(DurableRefundDestination):
    def __init__(self, root, exc):
        super().__init__(root)
        self.exc = exc

    def observe_bound(self, snapshot):
        raise self.exc


def run(dest):
    op = operation()
    env = ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)
    result = LocalDestinationExecutor(dest, policy(op)).execute_snapshot(snapshot_envelope(env))
    return env, result


def raw_bytes(dest):
    return b"".join(p.read_bytes() for p in dest.path.parent.iterdir() if p.is_file())


def test_helper_keeps_typed_refusals_and_reduces_everything_else_to_its_type():
    assert destination_error_text(PermissionError("operation_already_committed")) == "operation_already_committed"
    assert destination_error_text(ProviderError(DETAIL)) == "ProviderError"
    assert destination_error_text(ValueError(DETAIL)) == "ValueError"
    assert destination_error_text(ConnectionError(DETAIL)) == "ConnectionError"


@pytest.mark.parametrize("exc,name", [(ProviderError(DETAIL), "ProviderError"), (OSError(DETAIL), "OSError"),
                                      (ValueError(DETAIL), "ValueError")])
def test_commit_exception_is_recorded_by_type_only(tmp_path, exc, name):
    dest = FailingCommit(tmp_path / "d", exc)
    env, result = run(dest)
    assert (result.status, result.error) == ("failed", name)
    assert [e["error"] for e in dest.attempt_history(result.attempt_id)] == [None, name]
    exported = export_execution_artifacts(env, result, dest, repository_revision="test")
    assert exported["execution_result"]["error"] == name
    assert [e["error"] for e in exported["attempt_events"]] == [None, name]
    assert DETAIL.encode() not in raw_bytes(dest)


def test_observe_exception_is_recorded_by_type_only(tmp_path):
    dest = FailingObserve(tmp_path / "d", ProviderError(DETAIL))
    _, result = run(dest)
    assert (result.status, result.error) == ("denied", "ProviderError")
    assert dest.attempt_history(result.attempt_id)[-1]["error"] == "ProviderError"
    assert DETAIL.encode() not in raw_bytes(dest)


def test_default_typed_refusal_from_commit_keeps_its_reason(tmp_path):
    dest = FailingCommit(tmp_path / "d", PermissionError("operation_already_committed"))
    _, result = run(dest)
    assert (result.status, result.error) == ("failed", "operation_already_committed")
    assert dest.attempt_history(result.attempt_id)[-1]["error"] == "operation_already_committed"


def test_default_typed_refusal_from_observe_keeps_its_reason(tmp_path):
    dest = FailingObserve(tmp_path / "d", PermissionError("effect_id is bound to different operation content"))
    _, result = run(dest)
    assert (result.status, result.error) == ("denied", "effect_id is bound to different operation content")


def test_default_lost_acknowledgement_text_is_unchanged(tmp_path):
    dest = FailingCommit(tmp_path / "d", TimeoutError("synthetic acknowledgement unavailable"))
    _, result = run(dest)
    assert (result.status, result.error) == ("unknown", "synthetic acknowledgement unavailable")


def test_default_successful_flow_records_no_error(tmp_path):
    dest = DurableRefundDestination(tmp_path / "d")
    env, result = run(dest)
    assert (result.status, result.error) == ("executed", None)
    assert [e["error"] for e in dest.attempt_history(result.attempt_id)] == [None, None]


class AtomicRaises(DurableRefundDestination):
    def __init__(self, root, exc):
        super().__init__(root)
        self.exc = exc

    def execute_claim_atomic(self, claim_id, snapshot, *, attempt_id, simulate=None):
        raise self.exc


def run_atomic(tmp_path, exc):
    op = operation()
    dest = AtomicRaises(tmp_path / "a", exc)
    env = ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)
    result = AtomicLocalControlPlaneExecutor(workflow=None, destination=dest, policy=policy(op)).execute(
        envelope=env, claim_id="claim-1")
    return dest, result


def test_atomic_route_dispatch_exception_is_recorded_by_type_only(tmp_path):
    dest, result = run_atomic(tmp_path, ProviderError(DETAIL))
    assert (result.status, result.error) == ("denied", "ProviderError")
    [failure] = dest.failure_history("effect-1")
    assert (failure["reason_code"], failure["detail"]) == ("dispatch_exception", "ProviderError")
    assert DETAIL.encode() not in raw_bytes(dest)


def test_default_atomic_route_typed_refusal_keeps_its_reason(tmp_path):
    dest, result = run_atomic(tmp_path, PermissionError("execution claim is not usable"))
    assert (result.status, result.error) == ("denied", "execution claim is not usable")
    assert dest.failure_history("effect-1")[0]["detail"] == "execution claim is not usable"
