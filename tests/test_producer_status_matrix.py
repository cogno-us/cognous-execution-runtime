"""The producer export refuses results whose status, flags and observation contradict one another."""
from __future__ import annotations

from dataclasses import replace

import pytest

from engine.producer_contract import export_execution_artifacts
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION,
    DurableRefundDestination,
    ExecutionEnvelope,
    ExecutionResult,
    LocalDestinationExecutor,
    snapshot_envelope,
)
from tests.test_producer_contract import operation, policy


def _flow(tmp_path):
    op = operation()
    envelope = ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)
    destination = DurableRefundDestination(tmp_path / "state")
    result = LocalDestinationExecutor(destination, policy(op)).execute_snapshot(snapshot_envelope(envelope))
    return envelope, destination, result


def test_genuine_executed_result_still_exports(tmp_path):
    envelope, destination, result = _flow(tmp_path)
    assert result.status == "executed"
    exported = export_execution_artifacts(envelope, result, destination)
    assert exported["provenance"]["independently_established"] == []
    assert "source_asserted" in exported["provenance"]


def test_genuine_duplicate_and_historical_results_still_export(tmp_path):
    envelope, destination, first = _flow(tmp_path)
    op = envelope.operation
    executor = LocalDestinationExecutor(destination, policy(op))
    again = executor.execute_snapshot(snapshot_envelope(envelope))
    export_execution_artifacts(envelope, again, destination)
    observed = executor.observe_historical(envelope)
    export_execution_artifacts(envelope, observed, destination)


CONTRADICTIONS = {
    "executed_not_attempted": dict(attempted=False),
    "executed_observed_unknown": dict(observed_state="unknown"),
    "executed_without_observation": dict(observation=None),
    "executed_not_acknowledged": dict(acknowledged=False),
    "executed_not_fresh": dict(newly_executed=False),
    "reconciled_but_fresh": dict(status="reconciled"),
    "partial_observed_applied": dict(status="partial"),
    "failed_but_acknowledged": dict(status="failed"),
    "denied_with_applied_effect": dict(status="denied"),
    "unknown_but_acknowledged": dict(status="unknown"),
    "observed_with_attempt": dict(status="observed"),
    "unrecognised_status": dict(status="succeeded"),
    "unrecognised_observed_state": dict(observed_state="done"),
    "non_boolean_flag": dict(attempted=1),
}


@pytest.mark.parametrize("name", sorted(CONTRADICTIONS))
def test_contradictory_result_is_not_exported(tmp_path, name):
    envelope, destination, result = _flow(tmp_path)
    bad = replace(result, **CONTRADICTIONS[name])
    with pytest.raises(ValueError):
        export_execution_artifacts(envelope, bad, destination)


def test_acknowledgement_without_attempt_is_refused(tmp_path):
    envelope, destination, result = _flow(tmp_path)
    bad = ExecutionResult("denied", result.decision_id, result.effect_id, None, False, True, "unknown", False)
    with pytest.raises(ValueError):
        export_execution_artifacts(envelope, bad, destination)
