from __future__ import annotations

import copy
from dataclasses import replace

import engine.producer_contract as producer_contract

import pytest

from engine.producer_contract import (
    EXECUTOR_PRODUCER_PROFILE_ID,
    EXECUTOR_PRODUCER_PROFILE_VERSION,
    DurableRefundDestination,
    ExecutionEnvelope,
    ExecutionOperation,
    LocalExecutionPolicy,
    commitment,
    export_execution_artifacts,
)
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION,
    ExecutionResult,
    LocalDestinationExecutor,
    snapshot_envelope,
)


def operation() -> ExecutionOperation:
    payload = {"refund_reason": "producer-contract-test"}
    return ExecutionOperation(
        actor="urn:cognous:identity:agent-1",
        principal="urn:cognous:principal:service",
        institution_id="urn:cognous:institution:demo",
        authority_domain="customer-refunds",
        manifest_id="refund-integration-v1-1",
        manifest_version="1.1",
        manifest_digest="sha256:" + "a" * 64,
        proposal_commitment="sha256:" + "b" * 64,
        action_id="refund.issue",
        adapter_id="urn:cognous:adapter:synthetic-refund",
        target="urn:cognous:synthetic-account:customer-1",
        payload=payload,
        payload_commitment=commitment(payload),
        requested_permissions=("refund.issue",),
        amount=50.0,
        unit="USD",
        effects=1,
        authority_context_id="urn:cognous:authority-context:refund-demo",
        requirement_id="urn:cognous:requirement:refund-t1",
        grant_id="urn:cognous:grant:refund-1",
        grant_revision="1",
        effective_max_effects=1,
    )


def policy(op: ExecutionOperation) -> LocalExecutionPolicy:
    return LocalExecutionPolicy(
        allowed_institutions=frozenset({op.institution_id}),
        allowed_authority_domains=frozenset({op.authority_domain}),
        allowed_adapters=frozenset({op.adapter_id}),
        allowed_actions=frozenset({op.action_id}),
        allowed_target_prefixes=("urn:cognous:synthetic-account:",),
        allowed_units=frozenset({"USD"}),
        max_amount=1000.0,
        max_effects=1,
    )


def test_exported_profile_separates_interface_revision_and_provenance(tmp_path):
    op = operation()
    envelope = ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)
    destination = DurableRefundDestination(tmp_path / "state")
    result = LocalDestinationExecutor(destination, policy(op)).execute_snapshot(snapshot_envelope(envelope))

    exported = export_execution_artifacts(
        envelope,
        result,
        destination,
        repository_revision="proposed-revision",
        source_asserted_provenance={"test_run_id": "unit-test"},
    )

    assert exported["producer_profile"] == {
        "profile_id": EXECUTOR_PRODUCER_PROFILE_ID,
        "profile_version": EXECUTOR_PRODUCER_PROFILE_VERSION,
        "execution_envelope_version": EXECUTION_ENVELOPE_VERSION,
    }
    assert exported["repository"]["revision"] == "proposed-revision"
    assert exported["repository"]["revision_status"] == "source_asserted"
    assert exported["provenance"]["source_asserted"]["test_run_id"] == "unit-test"
    assert exported["provenance"]["independently_established"] == []
    assert len(exported["effects"]) == 1
    assert exported["effects"][0]["effect_id"] == "effect-1"
    assert exported["attempts"]
    assert exported["attempt_events"]
    assert exported["observations"][0]["effect_id"] == "effect-1"


def test_export_rejects_contradictory_result_binding(tmp_path):
    op = operation()
    envelope = ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)
    destination = DurableRefundDestination(tmp_path / "state")
    result = LocalDestinationExecutor(destination, policy(op)).execute_snapshot(snapshot_envelope(envelope))
    changed = copy.copy(result)
    object.__setattr__(changed, "effect_id", "other-effect")
    with pytest.raises(ValueError, match="identity contradicts"):
        export_execution_artifacts(envelope, changed, destination)


def test_export_does_not_construct_authority_or_policy(tmp_path):
    op = operation()
    envelope = ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)
    destination = DurableRefundDestination(tmp_path / "state")
    result = LocalDestinationExecutor(destination, policy(op)).execute_snapshot(snapshot_envelope(envelope))
    exported = export_execution_artifacts(envelope, result, destination)
    assert "resolver" not in exported
    assert "authority_context" not in exported
    assert "policy" not in exported



@pytest.mark.parametrize(
    "replacement",
    [
        {"amount": 999.0},
        {"target": "urn:cognous:synthetic-account:customer-999"},
    ],
)
def test_export_rejects_changed_operation_under_same_decision_and_effect(tmp_path, replacement):
    original_op = operation()
    original = ExecutionEnvelope(
        EXECUTION_ENVELOPE_VERSION, "decision-binding", "effect-binding", original_op
    )
    destination = DurableRefundDestination(tmp_path / "state")
    original_result = LocalDestinationExecutor(
        destination, policy(original_op)
    ).execute_snapshot(snapshot_envelope(original))
    changed_op = replace(original_op, **replacement)
    replacement_envelope = ExecutionEnvelope(
        original.version, original.decision_id, original.effect_id, changed_op
    )

    with pytest.raises(ValueError, match="operation binding|contradicts supplied envelope"):
        export_execution_artifacts(
            replacement_envelope,
            original_result,
            destination,
        )


def test_export_rejects_changed_payload_even_with_recomputed_payload_commitment(tmp_path):
    original_op = operation()
    original = ExecutionEnvelope(
        EXECUTION_ENVELOPE_VERSION, "decision-payload", "effect-payload", original_op
    )
    destination = DurableRefundDestination(tmp_path / "state")
    original_result = LocalDestinationExecutor(
        destination, policy(original_op)
    ).execute_snapshot(snapshot_envelope(original))

    changed_payload = {"refund_reason": "substituted-after-effect"}
    changed_op = replace(
        original_op,
        payload=changed_payload,
        payload_commitment=commitment(changed_payload),
    )
    replacement_envelope = ExecutionEnvelope(
        original.version, original.decision_id, original.effect_id, changed_op
    )

    with pytest.raises(ValueError, match="operation binding|payload contradicts"):
        export_execution_artifacts(
            replacement_envelope,
            original_result,
            destination,
        )


def test_export_serializes_validated_snapshot_not_mutated_caller_data(tmp_path, monkeypatch):
    payload = {"nested": {"reason": "approved"}}
    original_op = replace(
        operation(),
        payload=payload,
        payload_commitment=commitment(payload),
    )
    envelope = ExecutionEnvelope(
        EXECUTION_ENVELOPE_VERSION, "decision-mutation", "effect-mutation", original_op
    )
    destination = DurableRefundDestination(tmp_path / "state")
    result = LocalDestinationExecutor(
        destination, policy(original_op)
    ).execute_snapshot(snapshot_envelope(envelope))

    real_rows = producer_contract._rows
    mutated = False

    def mutate_after_snapshot(path, table):
        nonlocal mutated
        if not mutated:
            envelope.operation.payload["nested"]["reason"] = "mutated-during-export"
            mutated = True
        return real_rows(path, table)

    monkeypatch.setattr(producer_contract, "_rows", mutate_after_snapshot)
    exported = export_execution_artifacts(envelope, result, destination)

    assert mutated is True
    assert envelope.operation.payload["nested"]["reason"] == "mutated-during-export"
    assert (
        exported["execution_envelope"]["operation"]["payload"]["nested"]["reason"]
        == "approved"
    )
    assert exported["effects"][0]["payload_json"] == '{"nested":{"reason":"approved"}}'


def test_export_valid_success_preserves_bound_snapshot(tmp_path):
    op = operation()
    envelope = ExecutionEnvelope(
        EXECUTION_ENVELOPE_VERSION, "decision-success", "effect-success", op
    )
    destination = DurableRefundDestination(tmp_path / "state")
    result = LocalDestinationExecutor(
        destination, policy(op)
    ).execute_snapshot(snapshot_envelope(envelope))

    exported = export_execution_artifacts(envelope, result, destination)

    assert exported["execution_result"]["status"] == "executed"
    assert exported["execution_envelope"]["operation"]["amount"] == 50.0
    assert exported["effects"][0]["operation_digest"] == snapshot_envelope(envelope).operation.digest
    assert exported["attempts"][0]["operation_digest"] == snapshot_envelope(envelope).operation.digest


@pytest.mark.parametrize("simulate,expected_status,expected_state", [
    ("lost_ack", "unknown", "applied"),
    ("partial", "partial", "partial"),
])
def test_export_preserves_lost_ack_and_partial_evidence(
    tmp_path, simulate, expected_status, expected_state
):
    op = operation()
    envelope = ExecutionEnvelope(
        EXECUTION_ENVELOPE_VERSION,
        f"decision-{simulate}",
        f"effect-{simulate}",
        op,
        f"attempt-{simulate}",
    )
    destination = DurableRefundDestination(tmp_path / simulate)
    executor = LocalDestinationExecutor(destination, policy(op))
    result = executor.execute_snapshot(
        snapshot_envelope(envelope),
        simulate=simulate,
    )
    assert result.status == expected_status

    exported = export_execution_artifacts(envelope, result, destination)

    assert exported["execution_result"]["status"] == expected_status
    assert exported["effects"][0]["state"] == expected_state
    assert exported["attempts"][0]["attempt_id"] == f"attempt-{simulate}"
    assert exported["observations"][0]["effect_id"] == f"effect-{simulate}"


def test_export_preserves_restart_historical_observation_without_fabricating_attempt(tmp_path):
    op = operation()
    envelope = ExecutionEnvelope(
        EXECUTION_ENVELOPE_VERSION,
        "decision-restart",
        "effect-restart",
        op,
        "attempt-restart",
    )
    state = tmp_path / "state"
    destination = DurableRefundDestination(state)
    first = LocalDestinationExecutor(
        destination, policy(op)
    ).execute_snapshot(
        snapshot_envelope(envelope),
        simulate="crash_after_commit",
    )
    assert first.status == "unknown"

    restarted = DurableRefundDestination(state)
    historical_envelope = ExecutionEnvelope(
        envelope.version,
        envelope.decision_id,
        envelope.effect_id,
        envelope.operation,
        None,
    )
    observed = LocalDestinationExecutor(
        restarted, policy(op)
    ).observe_historical(historical_envelope)

    exported = export_execution_artifacts(
        historical_envelope,
        observed,
        restarted,
    )

    assert exported["execution_result"]["status"] == "observed"
    assert exported["execution_result"]["attempt_id"] is None
    assert len(exported["effects"]) == 1
    # The original durable attempt is retained as historical evidence; the
    # exporter does not fabricate a new attempt for observation.
    assert {row["attempt_id"] for row in exported["attempts"]} == {"attempt-restart"}


def test_export_preserves_legitimate_denied_result_without_fabricating_evidence(tmp_path):
    op = operation()
    envelope = ExecutionEnvelope(
        EXECUTION_ENVELOPE_VERSION, "decision-denied", "effect-denied", op
    )
    destination = DurableRefundDestination(tmp_path / "state")
    denied = ExecutionResult(
        status="denied",
        decision_id=envelope.decision_id,
        effect_id=envelope.effect_id,
        attempt_id=None,
        attempted=False,
        acknowledged=False,
        observed_state="unknown",
        newly_executed=False,
        observation={},
        error="authority denied before destination attempt",
    )

    exported = export_execution_artifacts(envelope, denied, destination)

    assert exported["execution_result"]["status"] == "denied"
    assert exported["effects"] == []
    assert exported["attempts"] == []
    assert exported["attempt_events"] == []
    assert exported["observations"] == []
