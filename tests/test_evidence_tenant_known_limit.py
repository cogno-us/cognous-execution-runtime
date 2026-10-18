"""Known limit: authority evidence rows are keyed by obligation_id alone.

``authority_evidence_v1`` has no tenant column and the obligation id is a caller-supplied string, so two tenants that
share one engine database and one obligation id share one evidence row. These tests pin that behaviour so a later
change of the key is a deliberate, reviewed decision (see docs/bounded_authorization_effect.md, deployment rule). They do
not describe a desired behaviour. The deployment rule is one engine database per tenant, or tenant-unique obligation ids.

No Control Plane package is needed: claims are plain objects and the trusted-host writers are called directly.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from engine.local_authority_effect import AtomicAuthorityEffectDestination as Destination
from engine.safe_executor import EXECUTION_ENVELOPE_VERSION, ExecutionEnvelope, commitment, snapshot_envelope
from test_producer_contract import operation

T0 = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
OBLIGATION = "urn:cognous:evidence:refund-entitlement"


@pytest.fixture(autouse=True)
def _commitment_available(monkeypatch):
    if importlib.util.find_spec("agent_control_plane") is None:
        stub = types.ModuleType("agent_control_plane.bounded")
        stub.commitment = commitment
        monkeypatch.setitem(sys.modules, "agent_control_plane", types.ModuleType("agent_control_plane"))
        monkeypatch.setitem(sys.modules, "agent_control_plane.bounded", stub)


class Clock:
    now = T0


def _snapshot(tenant):
    op = dataclasses.replace(operation(), tenant_id=tenant, grant_id=f"grant-{tenant}")
    snap = snapshot_envelope(ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, f"dec-{tenant}", f"eff-{tenant}", op))
    effect_id = commitment({"proposal": op.proposal_commitment, "grant_id": op.grant_id, "grant_revision": op.grant_revision})
    return dataclasses.replace(snap, effect_id=effect_id)


class _Claim(SimpleNamespace):
    def model_dump(self, **kwargs):
        return {"claim_id": self.claim_id}


def _claim(tenant, observed_at):
    snap = _snapshot(tenant)
    op = snap.operation
    evidence = SimpleNamespace(obligation_id=OBLIGATION, source_ref="src:entitlements", required=True,
                               kind="authorization", max_age_seconds=3600, unknown_behavior="hold_effect",
                               state="current", observed_at=observed_at.isoformat())
    return snap, _Claim(
        claim_id=f"claim-{tenant}", claim_commitment="c", effect_id=snap.effect_id, decision_id=snap.decision_id,
        institution_id=op.institution_id, authority_domain=op.authority_domain, actor=op.actor, principal=op.principal,
        grant_id=op.grant_id, grant_revision=op.grant_revision, grant_status="active", tenant_id=tenant,
        manifest_id=op.manifest_id, manifest_version=op.manifest_version, manifest_digest=op.manifest_digest,
        action_id=op.action_id, adapter_id=op.adapter_id, target=op.target, payload_commitment=op.payload_commitment,
        requested_permissions=list(op.requested_permissions), amount=op.amount, unit=op.unit, effects=op.effects,
        operation_commitment=Destination._operation_commitment(snap), operation_digest=None,
        max_effects=op.effective_max_effects, budget_id=f"budget-{tenant}",
        not_before=(T0 - timedelta(minutes=1)).isoformat(), expires_at=(T0 + timedelta(hours=5)).isoformat(),
        approval_state=[], policy_state=[], evidence_state=[evidence])


@pytest.fixture
def shared(tmp_path):
    Clock.now = T0
    dest = Destination(tmp_path / "shared", clock=lambda: Clock.now)
    dest._claim_model = lambda claim: claim  # trusted-host provisioning with plain objects; the evidence path is the subject
    sa, ca = _claim("tenant-A", T0)
    sb, cb = _claim("tenant-B", T0)
    with sqlite3.connect(dest.path) as conn:
        for tenant in ("tenant-A", "tenant-B"):
            conn.execute("INSERT INTO authority_grants_v1 VALUES(?,?,?,?,?,?,?,?)",
                         (f"grant-{tenant}", ca.grant_revision, "active", ca.institution_id, ca.authority_domain, tenant,
                          (T0 - timedelta(hours=1)).isoformat(), (T0 + timedelta(hours=9)).isoformat()))
    return dest, (sa, ca), (sb, cb)


def _rows(dest):
    with sqlite3.connect(dest.path) as conn:
        return [tuple(r) for r in conn.execute("SELECT obligation_id,state,observed_at FROM authority_evidence_v1")]


def _check(dest, snap, claim):
    try:
        with dest._connect() as conn:
            dest._validate_claim_state(conn, claim, snap, Clock.now)
        return "accept"
    except PermissionError as exc:
        return f"refuse: {exc}"


def test_evidence_table_has_no_tenant_column_known_limit(shared):
    """A later schema change that scopes evidence by tenant must be deliberate."""
    dest, _, _ = shared
    with sqlite3.connect(dest.path) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(authority_evidence_v1)")]
    assert "tenant_id" not in columns and columns[0] == "obligation_id"
    for sibling in ("authority_grants_v1", "authority_approvals_v1", "authority_policies_v1"):
        with sqlite3.connect(dest.path) as conn:
            assert "tenant_id" in [r[1] for r in conn.execute(f"PRAGMA table_info({sibling})")]


def test_two_tenants_with_one_obligation_id_share_one_row_known_limit(shared):
    """An identical evidence tuple from a second tenant is reused, not duplicated; a differing tuple is refused."""
    dest, (sa, ca), (sb, cb) = shared
    dest.provision_claim(ca)
    dest.provision_claim(cb)
    assert _rows(dest) == [(OBLIGATION, "current", T0.isoformat())]
    _, other = _claim("tenant-B", T0 - timedelta(minutes=30))
    other.claim_id = "claim-B2"
    with pytest.raises(PermissionError, match="existing authoritative evidence state rejects claim provisioning"):
        dest.provision_claim(other)


def test_shared_evidence_row_couples_tenants_known_limit(shared):
    """A refresh, a stale mark or a removal on the shared row applies to every tenant that names the obligation id."""
    dest, (sa, ca), (sb, cb) = shared
    dest.provision_claim(ca)
    dest.provision_claim(cb)
    assert _check(dest, sb, cb) == "accept"
    Clock.now = T0 + timedelta(seconds=4000)  # tenant-B's own evidence is older than its max age
    assert _check(dest, sb, cb) == "refuse: authoritative evidence expired"
    refreshed = (T0 + timedelta(seconds=3900)).isoformat()
    dest.set_evidence_state(OBLIGATION, state="current", observed_at=refreshed)  # done on behalf of tenant-A
    assert _check(dest, sb, cb) == "accept"
    dest.set_evidence_state(OBLIGATION, state="stale")
    assert _check(dest, sa, ca) == _check(dest, sb, cb) == "refuse: authoritative evidence state invalid"
    dest.set_evidence_state(OBLIGATION, state="current", observed_at=refreshed)
    dest.remove_authoritative_state("evidence", OBLIGATION)
    assert _check(dest, sb, cb) == "refuse: authoritative evidence state unavailable"
