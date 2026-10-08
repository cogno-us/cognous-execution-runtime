"""W2 reproducer: pre-dispatch policy denial currently has no durable history."""
import sqlite3

import pytest

from engine.atomic_local_control_plane_executor import AtomicLocalControlPlaneExecutor
from engine.safe_executor import LocalExecutionPolicy
from test_local_authority_effect import policy, setup_atomic


@pytest.mark.xfail(
    strict=True,
    reason="W2 defect: pre-dispatch policy denial is returned but not durably retained",
)
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

    with sqlite3.connect(destination.path) as conn:
        rows = conn.execute(
            "SELECT failure_class,stage,decision_id,effect_id FROM failure_records_v1"
        ).fetchall()

    assert rows == [("policy_denial", "pre_dispatch", env.decision_id, env.effect_id)]
