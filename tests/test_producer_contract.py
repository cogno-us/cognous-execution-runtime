from __future__ import annotations

import copy

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
from engine.safe_executor import EXECUTION_ENVELOPE_VERSION, LocalDestinationExecutor


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
    result = LocalDestinationExecutor(destination, policy(op)).execute(envelope)

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
    result = LocalDestinationExecutor(destination, policy(op)).execute(envelope)
    changed = copy.copy(result)
    object.__setattr__(changed, "effect_id", "other-effect")
    with pytest.raises(ValueError, match="identity contradicts"):
        export_execution_artifacts(envelope, changed, destination)


def test_export_does_not_construct_authority_or_policy(tmp_path):
    op = operation()
    envelope = ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)
    destination = DurableRefundDestination(tmp_path / "state")
    result = LocalDestinationExecutor(destination, policy(op)).execute(envelope)
    exported = export_execution_artifacts(envelope, result, destination)
    assert "resolver" not in exported
    assert "authority_context" not in exported
    assert "policy" not in exported
