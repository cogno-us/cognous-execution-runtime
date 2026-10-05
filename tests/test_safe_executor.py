from concurrent.futures import ThreadPoolExecutor

import pytest

from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION,
    DurableRefundDestination,
    ExecutionEnvelope,
    ExecutionOperation,
    InProcessDecisionSource,
    LocalExecutionPolicy,
    SafeExecutor,
    commitment,
)


def make_operation(**overrides):
    payload = overrides.pop("payload", {"refund_reason": "synthetic-test"})
    base = dict(
        actor="urn:cognous:identity:agent-1",
        principal="urn:cognous:principal:service",
        institution_id="urn:cognous:institution:demo",
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
    base.update(overrides)
    return ExecutionOperation(**base)


def make_decision(op, decision_id="decision-1", effect_id="effect-1"):
    binding = {
        "proposal_commitment": op.proposal_commitment,
        "manifest_id": op.manifest_id,
        "manifest_version": op.manifest_version,
        "manifest_digest": op.manifest_digest,
        "actor": op.actor,
        "principal": op.principal,
        "action_id": op.action_id,
        "adapter_id": op.adapter_id,
        "target": op.target,
        "payload_commitment": op.payload_commitment,
        "requested_permissions": list(op.requested_permissions),
        "amount": op.amount,
        "unit": op.unit,
        "effects": op.effects,
        "authority_context_id": op.authority_context_id,
        "requirement_id": op.requirement_id,
        "grant_id": op.grant_id,
        "grant_revision": op.grant_revision,
        "effective_max_effects": op.effective_max_effects,
    }
    return {
        "decision_id": decision_id,
        "effect_id": effect_id,
        "result": "authorized",
        "binding": binding,
    }


def make_executor(tmp_path, op=None, decision=None, policy=None):
    op = op or make_operation()
    decision = decision or make_decision(op)
    decisions = {decision["decision_id"]: decision}
    source = InProcessDecisionSource(lambda decision_id: decisions.get(decision_id))
    destination = DurableRefundDestination(tmp_path / "state")
    policy = policy or LocalExecutionPolicy(
        allowed_institutions=frozenset({op.institution_id}),
        allowed_adapters=frozenset({op.adapter_id}),
        allowed_actions=frozenset({op.action_id}),
        allowed_target_prefixes=("urn:cognous:synthetic-account:",),
        allowed_units=frozenset({"USD"}),
        max_amount=100.0,
        max_effects=1,
    )
    return (
        SafeExecutor(decisions=source, destination=destination, policy=policy),
        destination,
        decisions,
    )


def envelope(
    op=None, decision_id="decision-1", effect_id="effect-1", attempt_id=None
):
    return ExecutionEnvelope(
        EXECUTION_ENVELOPE_VERSION,
        decision_id,
        effect_id,
        op or make_operation(),
        attempt_id,
    )


def test_authorized_effect_changes_destination(tmp_path):
    executor, destination, _ = make_executor(tmp_path)
    result = executor.execute(envelope())
    assert result.status == "executed"
    assert result.executed is True
    observed = destination.observe("effect-1")
    assert observed["state"] == "applied"
    assert observed["destination_state"]["amount"] == 50.0
    assert observed["destination_state"]["payload"] == {
        "refund_reason": "synthetic-test"
    }


@pytest.mark.parametrize(
    "mutation", ["missing", "decision", "adapter", "target", "payload"]
)
def test_tampered_or_missing_decision_produces_no_effect(tmp_path, mutation):
    op = make_operation()
    decision = make_decision(op)
    executor, destination, decisions = make_executor(tmp_path, op, decision)
    request = envelope(op)
    if mutation == "missing":
        decisions.clear()
    elif mutation == "decision":
        decision["result"] = "hold"
    elif mutation == "adapter":
        request = envelope(make_operation(adapter_id="urn:cognous:adapter:other"))
    elif mutation == "target":
        request = envelope(
            make_operation(target="urn:cognous:synthetic-account:attacker")
        )
    else:
        request = envelope(make_operation(payload={"refund_reason": "changed"}))
    result = executor.execute(request)
    assert result.status == "denied"
    assert destination.observe("effect-1")["state"] == "absent"


def test_broader_upstream_grant_cannot_bypass_local_limit(tmp_path):
    op = make_operation(amount=90.0)
    decision = make_decision(op)
    policy = LocalExecutionPolicy(
        allowed_institutions=frozenset({op.institution_id}),
        allowed_adapters=frozenset({op.adapter_id}),
        allowed_actions=frozenset({op.action_id}),
        allowed_target_prefixes=("urn:cognous:synthetic-account:",),
        allowed_units=frozenset({"USD"}),
        max_amount=25.0,
        max_effects=1,
    )
    executor, destination, _ = make_executor(tmp_path, op, decision, policy)
    assert executor.execute(envelope(op)).status == "denied"
    assert destination.observe("effect-1")["state"] == "absent"


def test_same_effect_id_different_content_is_rejected(tmp_path):
    op = make_operation()
    executor, destination, _ = make_executor(tmp_path, op, make_decision(op))
    assert executor.execute(envelope(op)).status == "executed"

    changed = make_operation(payload={"refund_reason": "changed"})
    decision2 = make_decision(changed, decision_id="decision-2", effect_id="effect-1")
    executor2, _, _ = make_executor(tmp_path, changed, decision2)
    result = executor2.execute(
        envelope(changed, decision_id="decision-2", effect_id="effect-1")
    )
    assert result.status == "denied"
    assert destination.observe("effect-1")["destination_state"]["payload"] == {
        "refund_reason": "synthetic-test"
    }


def test_duplicate_and_restart_do_not_duplicate_effect(tmp_path):
    op = make_operation()
    decision = make_decision(op)
    executor, destination, decisions = make_executor(tmp_path, op, decision)
    first = executor.execute(envelope(op, attempt_id="attempt-1"))
    second = executor.execute(envelope(op, attempt_id="attempt-2"))
    assert first.status == "executed"
    assert second.status == "reconciled"

    restarted = SafeExecutor(
        decisions=InProcessDecisionSource(
            lambda decision_id: decisions.get(decision_id)
        ),
        destination=DurableRefundDestination(tmp_path / "state"),
        policy=executor.policy,
    )
    third = restarted.execute(envelope(op, attempt_id="attempt-3"))
    assert third.status == "reconciled"
    assert destination.effect_count(op.grant_id) == 1


def test_lost_ack_is_observed_before_retry(tmp_path):
    op = make_operation()
    executor, destination, _ = make_executor(tmp_path, op, make_decision(op))
    first = executor.execute(
        envelope(op, attempt_id="attempt-1"), simulate="lost_ack"
    )
    assert first.status == "unknown"
    assert first.observation["state"] == "applied"

    reconciled = executor.reconcile(envelope(op, attempt_id="reconcile-1"))
    assert reconciled.status == "reconciled"

    retry = executor.execute(envelope(op, attempt_id="attempt-2"))
    assert retry.status == "reconciled"
    assert destination.effect_count(op.grant_id) == 1


def test_partial_delivery_holds_and_is_not_reapplied(tmp_path):
    op = make_operation()
    executor, destination, _ = make_executor(tmp_path, op, make_decision(op))
    first = executor.execute(envelope(op), simulate="partial")
    assert first.status == "partial"
    assert executor.reconcile(envelope(op)).status == "partial"
    retry = executor.execute(envelope(op))
    assert retry.status == "partial"
    assert destination.effect_count(op.grant_id) == 1


def test_concurrent_attempts_transactionally_respect_limit(tmp_path):
    op1 = make_operation(proposal_commitment="sha256:" + "1" * 64)
    op2 = make_operation(
        proposal_commitment="sha256:" + "2" * 64,
        target="urn:cognous:synthetic-account:customer-2",
    )
    decision1 = make_decision(op1, decision_id="d1", effect_id="e1")
    decision2 = make_decision(op2, decision_id="d2", effect_id="e2")
    decisions = {"d1": decision1, "d2": decision2}
    destination = DurableRefundDestination(tmp_path / "state")
    policy = LocalExecutionPolicy(
        allowed_institutions=frozenset({op1.institution_id}),
        allowed_adapters=frozenset({op1.adapter_id}),
        allowed_actions=frozenset({op1.action_id}),
        allowed_target_prefixes=("urn:cognous:synthetic-account:",),
        allowed_units=frozenset({"USD"}),
        max_amount=100.0,
        max_effects=1,
    )
    executor = SafeExecutor(
        decisions=InProcessDecisionSource(lambda value: decisions.get(value)),
        destination=destination,
        policy=policy,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda request: executor.execute(request),
                [envelope(op1, "d1", "e1"), envelope(op2, "d2", "e2")],
            )
        )
    assert sorted(result.status for result in results) == ["executed", "failed"]
    assert destination.effect_count(op1.grant_id) == 1


def test_database_path_traversal_rejected(tmp_path):
    with pytest.raises(PermissionError):
        DurableRefundDestination(tmp_path / "root", "../outside.sqlite3")


def test_symlinked_database_path_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "real.sqlite3"
    target.touch()
    (root / "refunds.sqlite3").symlink_to(target)
    with pytest.raises(PermissionError):
        DurableRefundDestination(root)
