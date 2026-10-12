from dataclasses import replace

import pytest

from engine.operation_admission import (
    AdapterConformance,
    OperationAdmission,
    OperationAdmissionRegistry,
    OperationAttempt,
    OperationDiscriminator,
    qualify_adapter,
)


def op(n: str, *, principal: str = "refund-service", domain: str = "customer-refunds"):
    return OperationDiscriminator(principal, domain, n)


def qualified_adapter(**changes):
    values = dict(
        adapter_id="synthetic-provider/v1",
        id_reuse="idempotent_same_operation",
        client_reference_searchable=True,
        dedupe="client_reference",
        deadlines="provider_deadline",
        observation_coverage=frozenset({"applied", "partial", "absent", "unknown"}),
        watermark="provider-sequence-token",
        provider_acceptance_closure=True,
    )
    values.update(changes)
    return AdapterConformance(**values)


def test_stable_domain_operation_and_attempt_subordination():
    registry = OperationAdmissionRegistry()
    identity = op("order-100/refund-1")
    registry.admit(OperationAdmission(identity, "commitment-v1"))
    registry.register_attempt(OperationAttempt(identity, "attempt-1"))
    registry.register_attempt(OperationAttempt(identity, "attempt-2"))
    assert [a.discriminator for a in registry.attempts.values()] == [identity, identity]


def test_planner_minted_or_unadmitted_operation_cannot_create_attempt():
    registry = OperationAdmissionRegistry()
    with pytest.raises(PermissionError, match="not domain-admitted"):
        registry.register_attempt(OperationAttempt(op("planner-generated"), "attempt-1"))


def test_operation_discriminator_collision_rejected():
    registry = OperationAdmissionRegistry()
    identity = op("stable-1")
    registry.admit(OperationAdmission(identity, "commitment-a"))
    with pytest.raises(PermissionError, match="collision"):
        registry.admit(OperationAdmission(identity, "commitment-b"))


def test_replan_requires_new_domain_issued_discriminator_and_supersedes_old():
    registry = OperationAdmissionRegistry()
    original = op("order-1/refund-v1")
    replacement = op("order-1/refund-v2")
    registry.admit(OperationAdmission(original, "plan-a"))
    with pytest.raises(PermissionError, match="collision"):
        registry.admit(OperationAdmission(original, "plan-b"))
    registry.admit(OperationAdmission(replacement, "plan-b", supersedes=original))
    with pytest.raises(PermissionError, match="superseded"):
        registry.register_attempt(OperationAttempt(original, "late-attempt"))
    registry.register_attempt(OperationAttempt(replacement, "replacement-attempt"))


def test_supersession_cannot_cross_principal_or_domain_and_cannot_fork():
    registry = OperationAdmissionRegistry()
    original = op("op-1")
    registry.admit(OperationAdmission(original, "a"))
    with pytest.raises(PermissionError, match="principal and authority domain"):
        OperationAdmission(op("op-2", principal="other"), "b", supersedes=original)
    successor = op("op-2")
    registry.admit(OperationAdmission(successor, "b", supersedes=original))
    with pytest.raises(PermissionError, match="already has a successor"):
        registry.admit(OperationAdmission(op("op-3"), "c", supersedes=original))


def test_attempt_identity_collision_across_operations_rejected():
    registry = OperationAdmissionRegistry()
    first, second = op("op-a"), op("op-b")
    registry.admit(OperationAdmission(first, "a"))
    registry.admit(OperationAdmission(second, "b"))
    registry.register_attempt(OperationAttempt(first, "attempt-1"))
    with pytest.raises(PermissionError, match="collision"):
        registry.register_attempt(OperationAttempt(second, "attempt-1"))


def test_two_independent_valid_orders_remain_distinct_in_either_admission_order():
    for first, second in [(op("order-a"), op("order-b")), (op("order-b"), op("order-a"))]:
        registry = OperationAdmissionRegistry()
        registry.admit(OperationAdmission(first, f"commit-{first.operation_id}"))
        registry.admit(OperationAdmission(second, f"commit-{second.operation_id}"))
        registry.register_attempt(OperationAttempt(first, f"attempt-{first.operation_id}"))
        registry.register_attempt(OperationAttempt(second, f"attempt-{second.operation_id}"))
        assert len(registry.admissions) == 2
        assert len(registry.attempts) == 2
        assert {a.discriminator.operation_id for a in registry.attempts.values()} == {"order-a", "order-b"}


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"client_reference_searchable": False}, "searchability"),
        ({"dedupe": "none"}, "dedupe"),
        ({"deadlines": "none"}, "deadline"),
        ({"observation_coverage": frozenset({"applied", "absent", "unknown"})}, "partial"),
        ({"watermark": None}, "watermark"),
        ({"provider_acceptance_closure": False}, "provider-acceptance"),
    ],
)
def test_adapter_conformance_is_fail_closed(changes, expected):
    qualified, reasons = qualify_adapter(qualified_adapter(**changes))
    assert not qualified
    assert expected in " ".join(reasons)


def test_adapter_conformance_declares_all_required_provider_capabilities():
    adapter = qualified_adapter()
    qualified, reasons = qualify_adapter(adapter)
    assert qualified and reasons == ()
    assert adapter.id_reuse == "idempotent_same_operation"
    assert adapter.client_reference_searchable
    assert adapter.dedupe == "client_reference"
    assert adapter.deadlines == "provider_deadline"
    assert adapter.observation_coverage == {"applied", "partial", "absent", "unknown"}
    assert adapter.watermark == "provider-sequence-token"
    assert adapter.provider_acceptance_closure


@pytest.mark.parametrize("acceptance", ["accepted", "rejected", "unknown"])
def test_absent_observation_alone_never_permits_retry(acceptance):
    registry = OperationAdmissionRegistry()
    identity = op("op-1")
    attempt = OperationAttempt(identity, "attempt-1")
    registry.admit(OperationAdmission(identity, "a"))
    registry.register_attempt(attempt)
    decision = registry.evaluate_retry(
        attempt=attempt,
        observation="absent",
        provider_acceptance=acceptance,
        adapter=qualified_adapter(),
    )
    assert decision.permitted is False
    assert "absent observation alone" in decision.reason


def test_provider_acceptance_closure_missing_blocks_retry_assessment():
    registry = OperationAdmissionRegistry()
    identity = op("op-1")
    attempt = OperationAttempt(identity, "attempt-1")
    registry.admit(OperationAdmission(identity, "a"))
    registry.register_attempt(attempt)
    decision = registry.evaluate_retry(
        attempt=attempt,
        observation="unknown",
        provider_acceptance="unknown",
        adapter=qualified_adapter(provider_acceptance_closure=False),
    )
    assert not decision.permitted
    assert "not qualified" in decision.reason


def test_provider_rejection_closes_attempt_but_does_not_auto_authorize_retry():
    registry = OperationAdmissionRegistry()
    identity = op("op-1")
    attempt = OperationAttempt(identity, "attempt-1")
    registry.admit(OperationAdmission(identity, "a"))
    registry.register_attempt(attempt)
    decision = registry.evaluate_retry(
        attempt=attempt,
        observation="unknown",
        provider_acceptance="rejected",
        adapter=qualified_adapter(),
    )
    assert not decision.permitted
    assert "does not auto-authorize retry" in decision.reason
