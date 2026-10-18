"""Component-local synthetic tests for the opt-in operation-identity/0.1 profile; no network effects.

Provenance: test_legacy_destination_without_operation_id_is_unchanged_* was RUN on main 10a11d03 before this change
(it passes there and here). Every other row here describes new opt-in behaviour and has no "expected today" value.
"""
import dataclasses

import pytest

from engine.effective_grant import DERIVATION
from engine.operation_admission import (
    AttemptJoin, OperationAdmission, OperationAdmissionRegistry, OperationAttempt,
    OperationDiscriminator, OperationRenewal)
from engine.operation_identity_destination import (
    CAPABILITY, OperationIdentityDestination, OperationIdentityEffectiveGrantDestination)
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION, DurableRefundDestination, ExecutionEnvelope, LocalDestinationExecutor,
    snapshot_envelope)
from test_producer_contract import operation, policy

OP = "op-order-100/refund-1"


def snap(effect_id, **changes):
    op = dataclasses.replace(operation(), **changes) if changes else operation()
    return op, snapshot_envelope(ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-" + effect_id, effect_id, op))


def test_legacy_destination_without_operation_id_is_unchanged_and_allows_two_effects_per_grant_cap(tmp_path):
    _, a = snap("effect-a")
    dest = DurableRefundDestination(tmp_path / "legacy")
    assert dest.commit(a)["duplicate"] is False
    assert dest.effect_count(a.operation.grant_id) == 1
    assert "operation_effects_v1" not in {r[0] for r in dest._connect().execute("SELECT name FROM sqlite_master")}


def test_base_destination_refuses_an_operation_id_instead_of_dropping_it(tmp_path):
    op, a = snap("effect-a")
    dest = DurableRefundDestination(tmp_path / "legacy")
    with pytest.raises(PermissionError, match="operation_identity_capability_unavailable"):
        dest.commit(a, operation_id=OP)
    assert dest.effect_count(op.grant_id) == 0 and dest.observe(a.effect_id)["state"] == "absent"


def test_identity_destination_refuses_a_second_effect_for_the_same_operation(tmp_path):
    op, a = snap("effect-a")
    _, b = snap("effect-b")
    dest = OperationIdentityDestination(tmp_path / "state")
    assert dest.operation_identity_capability == CAPABILITY
    dest.commit(a, operation_id=OP)
    with pytest.raises(PermissionError, match="operation_already_committed"):
        dest.commit(b, operation_id=OP)
    assert dest.observe(b.effect_id)["state"] == "absent" and dest.effect_count(op.grant_id) == 1
    assert dest.operation_effect((op.principal, op.authority_domain, OP)) == a.effect_id
    # the same effect id is still the legacy duplicate, not an operation conflict
    assert dest.commit(a, operation_id=OP)["duplicate"] is True


def test_without_an_operation_id_the_identity_destination_behaves_like_the_legacy_one(tmp_path):
    op, a = snap("effect-a")
    _, b = snap("effect-b")
    dest = OperationIdentityDestination(tmp_path / "state")
    dest.commit(a)
    # (cap of one per grant still applies: legacy semantics, no operation key involved)
    with pytest.raises(PermissionError, match="local cumulative max_effects exhausted"):
        dest.commit(b)


def test_operation_key_is_scoped_by_principal_and_authority_domain(tmp_path):
    op, a = snap("effect-a")
    other, b = snap("effect-b", principal="urn:cognous:principal:other-service", grant_id="grant-2")
    dest = OperationIdentityDestination(tmp_path / "state")
    dest.commit(a, operation_id=OP)
    dest.commit(b, operation_id=OP)  # same id, different principal: a different operation
    assert dest.observe(a.effect_id)["state"] == dest.observe(b.effect_id)["state"] == "applied"


def test_executor_reports_the_refusal_as_failed_with_the_operation_code(tmp_path):
    op, a = snap("effect-a")
    _, b = snap("effect-b")
    dest = OperationIdentityDestination(tmp_path / "state")
    executor = LocalDestinationExecutor(dest, policy(op))
    assert executor.execute_snapshot(a, operation_id=OP).status == "executed"
    second = executor.execute_snapshot(b, operation_id=OP)
    assert second.status == "failed" and second.error == "operation_already_committed"
    assert dest.effect_count(op.grant_id) == 1


def test_operation_dedupe_commits_in_the_effect_transaction(tmp_path, monkeypatch):
    op, a = snap("effect-a")
    dest = OperationIdentityDestination(tmp_path / "state")
    trace, connect = [], dest._connect

    def traced():
        conn = connect()
        conn.set_trace_callback(trace.append)
        return conn
    monkeypatch.setattr(dest, "_connect", traced)
    dest.commit(a, operation_id=OP)
    begin = next(i for i, x in enumerate(trace) if "BEGIN IMMEDIATE" in x)
    guard = next(i for i, x in enumerate(trace) if "SELECT effect_id FROM operation_effects_v1" in x)
    effect = next(i for i, x in enumerate(trace) if "INSERT INTO effects" in x)
    record = next(i for i, x in enumerate(trace) if "INSERT INTO operation_effects_v1" in x)
    commit = max(i for i, x in enumerate(trace) if x.strip() == "COMMIT")
    assert begin < guard < effect < record < commit


def test_combined_profile_keeps_the_effective_grant_precondition(tmp_path):
    op, a = snap("effect-a")
    dest = OperationIdentityEffectiveGrantDestination(tmp_path / "state")
    digest = dest.set_effective_grant(principal=op.principal, grant_id=op.grant_id, revision=op.grant_revision,
                                      permissions=list(op.requested_permissions))
    with pytest.raises(PermissionError, match="precondition_required"):
        dest.commit(a, operation_id=OP)
    dest.commit(a, operation_id=OP, expected_effective_grant_digest=digest, effective_grant_derivation_version=DERIVATION)
    assert dest.effect_count(op.grant_id) == 1


# ---------------------------------------------------------------- sidecar: renewal, join, merged_into
def disc(n=OP):
    return OperationDiscriminator("refund-service", "customer-refunds", n)


def admitted():
    registry = OperationAdmissionRegistry()
    registry.admit(OperationAdmission(disc(), "commitment-v1", issuer="synthetic-governor"))
    registry.register_attempt(OperationAttempt(disc(), "attempt-1"))
    return registry


def join(**changes):
    values = dict(discriminator=disc(), attempt_id="attempt-1", check_id="check-1", resolver_id="synthetic-resolver",
                  resolver_version="1", class_max_age=60)
    values.update(changes)
    return AttemptJoin(**values)


def test_only_the_recorded_issuer_writes_a_renewal_and_other_writers_are_refused_and_recorded():
    registry = admitted()
    with pytest.raises(PermissionError, match="renewal_not_written_by_grant_issuer"):
        registry.record_renewal(OperationRenewal(disc(), "2", "worker-1"))
    assert [(r.written_by, r.reason) for r in registry.renewal_refusals] == [("worker-1", "renewal_not_written_by_grant_issuer")]
    assert registry.renewals == []
    registry.record_renewal(OperationRenewal(disc(), "2", "synthetic-governor"))
    assert [r.to_revision for r in registry.renewals] == ["2"]


def test_renewal_without_a_recorded_issuer_or_unknown_operation_is_refused():
    registry = OperationAdmissionRegistry()
    registry.admit(OperationAdmission(disc(), "commitment-v1"))  # legacy admission, no issuer
    with pytest.raises(PermissionError, match="renewal_not_written_by_grant_issuer"):
        registry.record_renewal(OperationRenewal(disc(), "2", "anyone"))
    with pytest.raises(PermissionError, match="operation_not_admitted"):
        registry.record_renewal(OperationRenewal(disc("unknown"), "2", "anyone"))
    assert len(registry.renewal_refusals) == 2


def test_join_is_one_row_per_registered_attempt_by_id():
    registry = admitted()
    registry.join_attempt(join())
    with pytest.raises(PermissionError, match="already joined"):
        registry.join_attempt(join())
    with pytest.raises(PermissionError, match="registered under the same operation"):
        registry.join_attempt(join(attempt_id="never-registered"))
    assert registry.joins["attempt-1"].check_id == "check-1" and registry.joins["attempt-1"].merged_into is None


def test_merge_field_names_an_admitted_operation_never_an_attempt():
    registry = admitted()
    with pytest.raises(PermissionError, match="admitted operation id"):
        registry.join_attempt(join(merged_into=disc("not-admitted")))
    with pytest.raises(ValueError, match="itself"):
        join(merged_into=disc())
    registry.admit(OperationAdmission(disc("survivor"), "commitment-v2"))
    registry.join_attempt(join(merged_into=disc("survivor")))
    assert registry.joins["attempt-1"].merged_into == disc("survivor")
    with pytest.raises(TypeError, match="never an attempt id"):
        join(merged_into="attempt-1")


def test_legacy_admission_equality_and_defaults_are_unchanged():
    assert OperationAdmission(disc(), "c") == OperationAdmission(disc(), "c")
    assert OperationAdmission(disc(), "c").issuer is None
