"""Amount substitution after claim issue, numeric equivalence, and known-limit characterisation (atomic route).

Amount: a claim is issued for one amount; an envelope that changes only the amount is refused with no row written, and
the claim stays usable for the matching envelope. ``50`` and ``50.0`` are the same quantity: the amount comparison
accepts both forms, but the operation commitment is computed over the JSON form, so the integer form is refused against
a claim issued for the float form (fail closed, pinned below as a known limit).

Known limits (``*_known_limit``): each test pins current behaviour only. A later change must be deliberate and is a
freeze item; none of these describes a desired behaviour.

Needs the Control Plane local-authority-effect contract on PYTHONPATH (same guard as
test_atomic_route_valid_flow_identity.py); the operation-key limit at the end runs without it.
"""
from __future__ import annotations

import dataclasses
import sqlite3
from datetime import timedelta

import pytest

from engine.operation_identity_destination import OperationIdentityDestination
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION, ExecutionEnvelope, commitment, snapshot_envelope)
from test_producer_contract import operation as plain_operation

try:
    import agent_control_plane.local_authority_effect  # noqa: F401
    HAVE_CP = True
except ModuleNotFoundError:
    HAVE_CP = False

needs_cp = pytest.mark.skipif(not HAVE_CP, reason="needs the Control Plane local-authority-effect contract on PYTHONPATH")


def _flow():
    import test_atomic_route_valid_flow_identity as flow
    return flow


def _effects(dest):
    with sqlite3.connect(dest.path) as conn:
        return [r[0] for r in conn.execute("SELECT effect_id FROM effects")]


def _with_amount(env, amount):
    return dataclasses.replace(env, operation=dataclasses.replace(env.operation, amount=amount))


# --- amount substitution ---------------------------------------------------------------------------------------------

@needs_cp
@pytest.mark.parametrize("amount", [51.0, 5000.0, 0.0, 49.99999999999999])
def test_amount_changed_after_issue_is_refused_and_leaves_no_row(tmp_path, amount):
    flow = _flow()
    env, dest, claim, ex = flow.setup(tmp_path)
    result = ex.execute(envelope=_with_amount(env, amount), claim_id=claim.claim_id)
    assert result.status == "denied" and result.attempted is False
    assert "amount" in result.error
    assert _effects(dest) == [] and dest.claim_state(claim.claim_id) == "issued"
    # valid control: the matching envelope still executes on the same claim
    assert ex.execute(envelope=env, claim_id=claim.claim_id).status == "executed"
    assert _effects(dest) == [env.effect_id]


@needs_cp
def test_integer_form_of_the_issued_amount_is_refused_by_the_operation_commitment_known_limit(tmp_path):
    """50 == 50.0 numerically, but the commitment is over the JSON form ("50" versus "50.0"): fail closed, no effect.

    The claim model issues amounts as floats, so the integer form of the same quantity never matches an issued claim.
    Canonicalising amounts in the commitment would change existing commitments and digests; that is a freeze decision
    and a later change must be deliberate."""
    flow = _flow()
    env, dest, claim, ex = flow.setup(tmp_path)
    result = ex.execute(envelope=_with_amount(env, 50), claim_id=claim.claim_id)
    assert result.status == "denied"
    assert result.error == "execution claim operation mismatch: operation_commitment"
    assert _effects(dest) == [] and dest.claim_state(claim.claim_id) == "issued"
    assert ex.execute(envelope=env, claim_id=claim.claim_id).status == "executed"


# --- known limits ----------------------------------------------------------------------------------------------------

def _claim_variant(flow, env, *, claim_id, budget_id=None, issued_at=None, not_before=None):
    """Same construction as flow.make_claim with the budget and the issue/validity times as parameters."""
    from agent_control_plane.bounded import commitment as cp_commitment
    from agent_control_plane.local_authority_effect import LocalExecutionClaim

    claim = flow.make_claim(env, claim_id=claim_id)
    raw = claim.model_dump(mode="json")
    if budget_id is not None:
        raw["budget_id"] = budget_id
    if issued_at is not None:
        raw["issued_at"] = issued_at.isoformat()
    if not_before is not None:
        raw["not_before"] = not_before.isoformat()
    raw.pop("claim_commitment")
    raw["claim_commitment"] = cp_commitment(raw)
    return LocalExecutionClaim.model_validate(raw)


@needs_cp
def test_budgets_are_per_budget_id_with_no_parent_ceiling_known_limit(tmp_path):
    """Effect budgets count per budget_id. The destination knows no parent grant: two claims of one grant that carry
    different budget ids each reach their own cap. A later change (a root-keyed or parent-keyed cap) must be deliberate."""
    flow = _flow()
    env1 = flow.envelope()
    env2 = dataclasses.replace(env1, decision_id="decision-2", effect_id="effect-2")
    dest = flow.AtomicAuthorityEffectDestination(tmp_path / "atomic", clock=lambda: flow.BASE)
    claims = [_claim_variant(flow, env1, claim_id="claim-1", budget_id="budget-a"),
              _claim_variant(flow, env2, claim_id="claim-2", budget_id="budget-b")]
    for claim in claims:
        dest.provision_claim(claim)
    ex = flow.AtomicLocalControlPlaneExecutor(workflow=None, destination=dest, policy=flow.policy(env1.operation))
    assert ex.execute(envelope=env1, claim_id="claim-1").status == "executed"
    assert ex.execute(envelope=env2, claim_id="claim-2").status == "executed"
    assert dest.budget_state("budget-a") == {"max_effects": 1, "used_effects": 1}
    assert dest.budget_state("budget-b") == {"max_effects": 1, "used_effects": 1}


@needs_cp
def test_issue_time_is_not_consulted_at_execution_known_limit(tmp_path):
    """Only not_before and expires_at gate execution. A claim issued a week earlier, with the validity interval still
    open, executes; the destination does not apply a maximum age to issued_at. A later change must be deliberate."""
    flow = _flow()
    env = flow.envelope()
    dest = flow.AtomicAuthorityEffectDestination(tmp_path / "atomic", clock=lambda: flow.BASE)
    claim = _claim_variant(flow, env, claim_id="claim-1", issued_at=flow.BASE - timedelta(days=7),
                           not_before=flow.BASE - timedelta(days=7))
    dest.provision_claim(claim)
    ex = flow.AtomicLocalControlPlaneExecutor(workflow=None, destination=dest, policy=flow.policy(env.operation))
    assert ex.execute(envelope=env, claim_id="claim-1").status == "executed"
    assert _effects(dest) == [env.effect_id]


def _snapshot(effect_id, amount):
    payload = {"refund_reason": f"known-limit-{effect_id}"}
    op = dataclasses.replace(plain_operation(), payload=payload, payload_commitment=commitment(payload), amount=amount)
    return snapshot_envelope(ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-" + effect_id, effect_id, op))


def test_same_operation_id_gets_the_same_refusal_for_a_replay_and_for_other_content_known_limit(tmp_path):
    """The operation key is (principal, authority domain, operation id). A second effect under the same key is refused
    with one reason, whether it carries the same content or different content; the destination does not distinguish a
    replay from a substitution. A later change (a payload commitment on admission) must be deliberate."""
    dest = OperationIdentityDestination(tmp_path / "state")
    dest.commit(_snapshot("effect-1", 50.0), operation_id="op-1")
    with pytest.raises(PermissionError) as same_content:
        dest.commit(dataclasses.replace(_snapshot("effect-1", 50.0), effect_id="effect-2"), operation_id="op-1")
    with pytest.raises(PermissionError) as other_content:
        dest.commit(_snapshot("effect-3", 5000.0), operation_id="op-1")
    assert str(same_content.value) == str(other_content.value) == "operation_already_committed"
    assert dest.operation_effect(("urn:cognous:principal:service", "customer-refunds", "op-1")) == "effect-1"
