from __future__ import annotations

from engine.producer_contract import (
    EXECUTOR_PRODUCER_PROFILE_VERSION,
    export_executor_evidence,
    policy_for_operation,
)
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION,
    DurableRefundDestination,
    ExecutionEnvelope,
    ExecutionOperation,
    LocalDestinationExecutor,
    commitment,
    snapshot_envelope,
)


def _operation():
    payload={"refund_reason":"producer-contract"}
    return ExecutionOperation(
        actor="urn:cognous:identity:agent-1",
        principal="urn:cognous:principal:service",
        institution_id="urn:cognous:institution:demo",
        authority_domain="customer-refunds",
        manifest_id="refund-integration-v1-1",
        manifest_version="1.1",
        manifest_digest="sha256:"+"a"*64,
        proposal_commitment="sha256:"+"b"*64,
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


def test_versioned_executor_evidence_profile(tmp_path):
    op=_operation()
    env=ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION,"decision-1","effect-1",op)
    destination=DurableRefundDestination(tmp_path/"state")
    result=LocalDestinationExecutor(destination,policy_for_operation(op)).execute_snapshot(snapshot_envelope(env))
    exported=export_executor_evidence(
        envelope=env,
        result=result,
        destination=destination,
        repository_revision="proposed-test-revision",
    )
    assert exported["producer_profile"]["profile_version"]==EXECUTOR_PRODUCER_PROFILE_VERSION
    assert exported["producer_profile"]["repository_revision_provenance"]=="source_asserted"
    assert exported["producer_profile"]["independently_established_provenance"] is False
    assert len(exported["effects"])==1
    assert exported["effects"][0]["effect_id"]=="effect-1"
    assert exported["attempts"][0]["decision_id"]=="decision-1"
    assert exported["attempt_events"]
    assert exported["observation"]["effect_id"]=="effect-1"


def test_export_rejects_contradictory_result_binding(tmp_path):
    op=_operation()
    env=ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION,"decision-1","effect-1",op)
    destination=DurableRefundDestination(tmp_path/"state")
    result=LocalDestinationExecutor(destination,policy_for_operation(op)).execute(env)
    result.effect_id="other-effect"
    try:
        export_executor_evidence(
            envelope=env,result=result,destination=destination,
            repository_revision="proposed-test-revision",
        )
    except ValueError as exc:
        assert "contradicts" in str(exc)
    else:
        raise AssertionError("contradictory result binding was accepted")
