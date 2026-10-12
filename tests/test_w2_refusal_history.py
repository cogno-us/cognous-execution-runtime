"""W2 reproducer: pre-dispatch policy denial currently has no durable history."""
import sqlite3

import pytest

try:
    import agent_control_plane.local_authority_effect  # noqa: F401
except ModuleNotFoundError:
    pytestmark = pytest.mark.skip(
        reason="W2 atomic qualification requires Control Plane local-authority-effect contract"
    )

from engine.atomic_local_control_plane_executor import AtomicLocalControlPlaneExecutor
from engine.safe_executor import LocalExecutionPolicy
from test_local_authority_effect import policy, setup_atomic


def test_atomic_policy_denial_has_durable_typed_failure_record(tmp_path):
    _, env, destination, claim = setup_atomic(tmp_path)
    allowed = policy(env.operation)
    denied = LocalExecutionPolicy(
        allowed_institutions=allowed.allowed_institutions,
        allowed_authority_domains=allowed.allowed_authority_domains,
        allowed_adapters=frozenset({"urn:cognous:adapter:not-this-one"}),
        allowed_actions=allowed.allowed_actions,
        allowed_target_prefixes=allowed.allowed_target_prefixes,
        allowed_units=allowed.allowed_units,
        max_amount=allowed.max_amount,
        max_effects=allowed.max_effects,
    )

    result = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=denied
    ).execute(envelope=env, claim_id=claim.claim_id)

    assert result.status == "denied"
    assert result.attempted is False

    history = destination.failure_history(env.effect_id)
    assert len(history) == 1
    failure = history[0]
    assert failure["failure_class"] == "policy_denial"
    assert failure["reason_code"] == "local_policy_rejected"
    assert failure["stage"] == "pre_dispatch"
    assert failure["decision_id"] == env.decision_id
    assert failure["effect_id"] == env.effect_id
    assert failure["attempt_id"] is None
    assert result.control_plane_evidence == {
        "failure_record_id": failure["failure_id"],
        "failure_class": "policy_denial",
    }


def test_authority_hold_retains_failure_without_execution_attempt(tmp_path):
    _, env, destination, claim = setup_atomic(tmp_path)
    destination.set_grant_status(claim.grant_id, status="revoked")

    result = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=policy(env.operation)
    ).execute(envelope=env, claim_id=claim.claim_id)

    assert result.status == "denied"
    assert result.attempted is False
    assert destination.attempt_history("missing") == []
    failure = destination.failure_history(env.effect_id)[0]
    assert failure["failure_class"] == "authority_hold"
    assert failure["reason_code"] == "current_authority_rejected"
    assert failure["stage"] == "pre_dispatch"
    assert failure["attempt_id"] is None


def test_policy_evaluation_error_is_typed_and_durable(tmp_path):
    _, env, destination, claim = setup_atomic(tmp_path)
    allowed = policy(env.operation)
    invalid = LocalExecutionPolicy(
        allowed_institutions=allowed.allowed_institutions,
        allowed_authority_domains=allowed.allowed_authority_domains,
        allowed_adapters=allowed.allowed_adapters,
        allowed_actions=allowed.allowed_actions,
        allowed_target_prefixes=allowed.allowed_target_prefixes,
        allowed_units=allowed.allowed_units,
        max_amount=allowed.max_amount,
        max_effects=0,
    )

    result = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=invalid
    ).execute(envelope=env, claim_id=claim.claim_id)

    assert result.status == "denied"
    failure = destination.failure_history(env.effect_id)[0]
    assert failure["failure_class"] == "evaluation_error"
    assert failure["reason_code"] == "local_policy_evaluation_error"
    assert failure["stage"] == "pre_dispatch"


def test_lost_ack_retains_dispatch_failure_and_does_not_authorize_retry(tmp_path):
    _, env, destination, claim = setup_atomic(tmp_path)
    result = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=policy(env.operation)
    ).execute(envelope=env, claim_id=claim.claim_id, simulate="lost_ack")

    assert result.status == "unknown"
    assert result.attempted is True
    assert result.acknowledged is False
    assert result.observed_state == "applied"
    failure = destination.failure_history(env.effect_id)[0]
    assert failure["failure_class"] == "dispatch_error"
    assert failure["reason_code"] == "acknowledgement_unavailable"
    assert failure["stage"] == "post_dispatch"
    assert failure["attempt_id"] == result.attempt_id
    assert result.observation["retry_eligible"] is False
