"""Atomic route (AtomicLocalControlPlaneExecutor + AtomicAuthorityEffectDestination) with real Control Plane claim models.

Skipped (and so not run by the native CI job or the pinned cross-component job, which lack the contract) when the Control Plane package with the local-authority-effect contract is not importable (the native job); run
it with the control plane on PYTHONPATH. Covers:
- byte identity: the valid flow (default destination, and the opt-in operation key) produces the same retained rows,
  receipt and result as on main eeb4583a (digest recorded there by running this file);
- grant status monotonicity end to end: an issued claim stays refused after revocation, the same revision cannot be set
  back, a successor revision does not revive the old claim, provisioning for a revoked revision is refused, and
  suspension stays reversible;
- envelope binding: proposal_commitment and effective_max_effects changed after the claim was issued are refused with a
  typed refusal, no row is written, and the claim stays usable for the matching envelope.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

try:
    import agent_control_plane.local_authority_effect  # noqa: F401
except ModuleNotFoundError:
    pytestmark = pytest.mark.skip(reason="needs the Control Plane local-authority-effect contract on PYTHONPATH")

from engine.atomic_local_control_plane_executor import AtomicLocalControlPlaneExecutor
from engine.local_authority_effect import AtomicAuthorityEffectDestination
from engine.operation_identity_atomic_destination import OperationIdentityAtomicAuthorityEffectDestination
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION, ExecutionEnvelope, ExecutionOperation, LocalExecutionPolicy, commitment,
    snapshot_envelope)

BASE = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
GRANT = "urn:cognous:grant:atomic"
APPROVAL = "urn:cognous:approval:atomic"
POLICY_REF = "urn:cognous:policy:atomic"
OP_KEY = "op-order-100/refund-1"


def operation():
    payload = {"reason": "duplicate"}
    return ExecutionOperation(
        actor="urn:cognous:identity:agent", principal="urn:cognous:principal:service",
        institution_id="urn:cognous:institution:test", authority_domain="refunds", manifest_id="manifest-1",
        manifest_version="1.1", manifest_digest="sha256:" + "1" * 64, proposal_commitment="sha256:" + "2" * 64,
        action_id="urn:cognous:action:refund", adapter_id="urn:cognous:adapter:refund",
        target="urn:cognous:synthetic-account:1", payload=payload, payload_commitment=commitment(payload),
        requested_permissions=("refund.issue",), amount=50.0, unit="USD", effects=1,
        authority_context_id="urn:cognous:authority-context:test", requirement_id="urn:cognous:requirement:test",
        grant_id=GRANT, grant_revision="1", effective_max_effects=1)


def envelope(*, approvals=True, **changes):
    op = dataclasses.replace(operation(), **changes)
    effect_id = "effect-1" if approvals else commitment(
        {"proposal": op.proposal_commitment, "grant_id": op.grant_id, "grant_revision": op.grant_revision})
    return ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", effect_id, op)


def make_claim(env, *, approvals=True, claim_id="claim-1"):
    from agent_control_plane.bounded import commitment as cp_commitment
    from agent_control_plane.local_authority_effect import LOCAL_AUTHORITY_EFFECT_PROFILE, LocalExecutionClaim

    op = snapshot_envelope(env).operation
    approval_state = [{
        "approval_ref": APPROVAL, "role_id": "urn:cognous:role:reviewer", "approver": "urn:cognous:principal:human",
        "status": "active", "grant_id": op.grant_id, "grant_revision": op.grant_revision,
        "proposal_commitment": op.proposal_commitment, "policy_versions": [{"ref": POLICY_REF, "version": "1"}],
        "observed_at": BASE.isoformat()}] if approvals else []
    policy_state = [{"ref": POLICY_REF, "version": "1", "status": "active", "observed_at": BASE.isoformat()}]
    raw = {
        "profile": LOCAL_AUTHORITY_EFFECT_PROFILE, "claim_id": claim_id, "decision_id": env.decision_id,
        "effect_id": env.effect_id, "institution_id": op.institution_id, "authority_domain": op.authority_domain,
        "actor": op.actor, "principal": op.principal, "grant_id": op.grant_id, "grant_revision": op.grant_revision,
        "grant_status": "active", "manifest_id": op.manifest_id, "manifest_version": op.manifest_version,
        "manifest_digest": op.manifest_digest, "action_id": op.action_id, "adapter_id": op.adapter_id,
        "target": op.target, "payload_commitment": op.payload_commitment,
        "requested_permissions": list(op.requested_permissions), "amount": op.amount, "unit": op.unit,
        "effects": op.effects, "authority_context_id": op.authority_context_id, "requirement_id": op.requirement_id,
        "requirement_commitment": "sha256:" + "3" * 64,
        "operation_commitment": AtomicAuthorityEffectDestination._operation_commitment(snapshot_envelope(env)),
        "approval_state": approval_state, "approval_state_commitment": cp_commitment(approval_state),
        "policy_state": policy_state, "policy_state_commitment": cp_commitment(policy_state),
        "evidence_state": [], "evidence_state_commitment": cp_commitment([]),
        "authority_state_commitment": cp_commitment({
            "grant_id": op.grant_id, "grant_revision": op.grant_revision, "grant_status": "active",
            "requirement_commitment": "sha256:" + "3" * 64, "approvals": approval_state, "policies": policy_state,
            "evidence": []}),
        "budget_id": "budget-1", "max_effects": op.effective_max_effects,
        "not_before": (BASE - timedelta(minutes=1)).isoformat(), "expires_at": (BASE + timedelta(hours=1)).isoformat(),
        "issued_at": BASE.isoformat(), "decision_input_commitment": None, "decision_input_profile_version": None,
        "source": "trusted_control_plane_workflow", "authorizing_by_possession": False,
    }
    raw["claim_commitment"] = cp_commitment(raw)
    return LocalExecutionClaim.model_validate(raw)


def policy(op):
    return LocalExecutionPolicy(
        allowed_institutions=frozenset({op.institution_id}), allowed_authority_domains=frozenset({op.authority_domain}),
        allowed_adapters=frozenset({op.adapter_id}), allowed_actions=frozenset({op.action_id}),
        allowed_target_prefixes=("urn:cognous:synthetic-account:",), allowed_units=frozenset({"USD"}),
        max_amount=1000.0, max_effects=2)  # admits the effective_max_effects mutation so the claim check is reached


def setup(tmp_path, *, approvals=True, cls=AtomicAuthorityEffectDestination):
    env = envelope(approvals=approvals)
    dest = cls(tmp_path / "atomic", clock=lambda: BASE)
    claim = make_claim(env, approvals=approvals)
    dest.provision_claim(claim)
    return env, dest, claim, AtomicLocalControlPlaneExecutor(workflow=None, destination=dest, policy=policy(env.operation))


def rows(dest):
    out = {}
    with sqlite3.connect(dest.path) as conn:
        conn.row_factory = sqlite3.Row
        names = sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        out["tables"] = names
        for name in names:
            if name in ("sqlite_sequence", "authority_effect_profile_v1", "failure_records_v1"):
                continue
            # claim_json/claim_commitment follow the Control Plane claim model revision, not this component
            out[name] = [{k: v for k, v in dict(r).items() if k not in ("committed_at", "claim_json", "claim_commitment")}
                         for r in conn.execute(f"SELECT * FROM {name} ORDER BY 1, 2")]
    return out


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def result_view(r):
    return {k: getattr(r, k) for k in ("status", "decision_id", "effect_id", "attempt_id", "attempted", "acknowledged",
                                       "observed_state", "newly_executed", "observation", "error",
                                       "control_plane_evidence")}


def effects(dest):
    with sqlite3.connect(dest.path) as conn:
        return [r[0] for r in conn.execute("SELECT effect_id FROM effects")]


# Recorded on main eeb4583a (before the change) by running these flows there.
RECORDED = {
    "atomic_approvals": "79bf51452f0b88afcb48ffa11ef3b78bfa750c3007830b874503363b0d2b070e",
    "atomic_no_approvals": "c8ab48b128a039e597195d21007d9b3e45542df5af684ead52fe8845ce83ab7d",
    "atomic_operation_key": "feaae50be49e205c9de2c3ea098bea05eb55aa1472b46a57705e5a2d00c87688",
}


@pytest.mark.parametrize("approvals", [True, False], ids=["approvals", "no_approvals"])
def test_default_atomic_valid_flow_bytes_are_unchanged(tmp_path, approvals):
    env, dest, claim, ex = setup(tmp_path, approvals=approvals)
    r = ex.execute(envelope=env, claim_id=claim.claim_id, attempt_id="attempt-1")
    assert r.status == "executed"
    key = "atomic_approvals" if approvals else "atomic_no_approvals"
    assert digest({"result": result_view(r), "rows": rows(dest)}) == RECORDED[key]


def test_default_atomic_operation_key_valid_flow_bytes_are_unchanged(tmp_path):
    env, dest, claim, _ = setup(tmp_path, cls=OperationIdentityAtomicAuthorityEffectDestination)
    value = dest.execute_claim_atomic(claim.claim_id, snapshot_envelope(env), attempt_id="attempt-1", operation_id=OP_KEY)
    assert value["newly_executed"] is True
    assert digest({"value": value, "rows": rows(dest)}) == RECORDED["atomic_operation_key"]


def test_issued_claim_stays_refused_after_revocation(tmp_path):
    env, dest, claim, ex = setup(tmp_path)
    dest.set_grant_status(GRANT, status="revoked")
    assert ex.execute(envelope=env, claim_id=claim.claim_id).status == "denied"
    with pytest.raises(PermissionError, match="grant revision is revoked"):
        dest.set_grant_status(GRANT, status="active", revision="1")
    r = ex.execute(envelope=env, claim_id=claim.claim_id)
    assert r.status == "denied" and "authoritative grant state invalid" in r.error
    assert effects(dest) == [] and dest.claim_state(claim.claim_id) == "issued"


def test_successor_revision_does_not_revive_the_old_claim(tmp_path):
    env, dest, claim, ex = setup(tmp_path)
    dest.set_grant_status(GRANT, status="revoked")
    dest.set_grant_status(GRANT, status="active", revision="2")
    assert ex.execute(envelope=env, claim_id=claim.claim_id).status == "denied"
    assert effects(dest) == []


def test_provisioning_for_a_revoked_revision_is_refused_even_after_row_removal(tmp_path):
    env, dest, claim, _ = setup(tmp_path)
    dest.set_grant_status(GRANT, status="revoked")
    dest.remove_authoritative_state("grant", GRANT)
    with pytest.raises(PermissionError, match="existing authoritative grant state rejects claim provisioning"):
        dest.provision_claim(make_claim(env, claim_id="claim-2"))


def test_default_suspension_stays_reversible(tmp_path):
    env, dest, claim, ex = setup(tmp_path)
    dest.set_grant_status(GRANT, status="suspended")
    assert ex.execute(envelope=env, claim_id=claim.claim_id).status == "denied"
    dest.set_grant_status(GRANT, status="active")
    assert ex.execute(envelope=env, claim_id=claim.claim_id).status == "executed"
    assert effects(dest) == [env.effect_id]


@pytest.mark.parametrize("approvals", [True, False], ids=["approvals", "no_approvals"])
@pytest.mark.parametrize("member,value", [("proposal_commitment", "sha256:" + "c" * 64), ("effective_max_effects", 2)])
def test_envelope_member_outside_the_operation_commitment_is_bound(tmp_path, approvals, member, value):
    env, dest, claim, ex = setup(tmp_path, approvals=approvals)
    mutated = dataclasses.replace(env, operation=dataclasses.replace(env.operation, **{member: value}))
    r = ex.execute(envelope=mutated, claim_id=claim.claim_id)
    assert r.status == "denied" and r.error == "execution claim operation mismatch: " + member
    assert r.control_plane_evidence["failure_class"] == "authority_hold"
    assert effects(dest) == [] and dest.claim_state(claim.claim_id) == "issued"
    assert ex.execute(envelope=env, claim_id=claim.claim_id).status == "executed"
