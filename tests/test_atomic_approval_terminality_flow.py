"""Atomic route with real Control Plane claim models: approval revocation is terminal per approval_ref.

Skipped when the Control Plane local-authority-effect contract is not importable (same condition as
test_atomic_route_valid_flow_identity.py, whose helpers it reuses). Covers revoke -> reactivate -> execute, provisioning
for a revoked approval_ref, and a genuinely new approval_ref being a distinct, separately provisioned approval.
"""
from __future__ import annotations

import sqlite3

import pytest

try:
    import agent_control_plane.local_authority_effect  # noqa: F401
except ModuleNotFoundError:
    pytestmark = pytest.mark.skip(reason="needs the Control Plane local-authority-effect contract on PYTHONPATH")

import test_atomic_route_valid_flow_identity as flow
from engine.local_authority_effect import APPROVAL_REVOCATION_TABLE

NEW_APPROVAL = "urn:cognous:approval:atomic-2"


def test_revoke_reactivate_execute_is_refused_and_no_effect_is_committed(tmp_path):
    env, dest, claim, ex = flow.setup(tmp_path)
    dest.set_approval_status(flow.APPROVAL, status="revoked")
    assert ex.execute(envelope=env, claim_id=claim.claim_id).status == "denied"
    with pytest.raises(PermissionError, match="approval is revoked"):
        dest.set_approval_status(flow.APPROVAL, status="active")
    r = ex.execute(envelope=env, claim_id=claim.claim_id)
    assert r.status == "denied" and "authoritative approval state invalid" in r.error
    assert flow.effects(dest) == [] and dest.claim_state(claim.claim_id) == "issued"


def test_provisioning_for_a_revoked_approval_ref_is_refused_even_after_row_removal(tmp_path):
    env, dest, claim, _ = flow.setup(tmp_path)
    dest.set_approval_status(flow.APPROVAL, status="revoked")
    dest.remove_authoritative_state("approval", flow.APPROVAL)
    with pytest.raises(PermissionError, match="existing authoritative approval state rejects claim provisioning"):
        dest.provision_claim(flow.make_claim(env, claim_id="claim-2"))


def test_a_new_approval_ref_is_a_distinct_approval_and_the_revoked_one_stays_revoked(tmp_path, monkeypatch):
    env, dest, claim, ex = flow.setup(tmp_path)
    dest.set_approval_status(flow.APPROVAL, status="revoked")
    monkeypatch.setattr(flow, "APPROVAL", NEW_APPROVAL)
    dest.provision_claim(flow.make_claim(env, claim_id="claim-2"))
    assert ex.execute(envelope=env, claim_id="claim-2").status == "executed"
    with sqlite3.connect(dest.path) as conn:
        statuses = dict(conn.execute("SELECT approval_ref,status FROM authority_approvals_v1"))
    assert statuses == {"urn:cognous:approval:atomic": "revoked", NEW_APPROVAL: "active"}


def test_default_valid_flow_without_revocation_creates_no_approval_revocation_table(tmp_path):
    env, dest, claim, ex = flow.setup(tmp_path)
    assert ex.execute(envelope=env, claim_id=claim.claim_id).status == "executed"
    with sqlite3.connect(dest.path) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert APPROVAL_REVOCATION_TABLE not in names
