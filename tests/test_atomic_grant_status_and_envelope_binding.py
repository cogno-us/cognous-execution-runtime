"""Component-local tests: grant status monotonicity and envelope binding on the atomic authority/effect destination.

No Control Plane package is needed: grant rows are written by the trusted host directly, and the claim-state check is
called with a plain claim object. When the Control Plane package is absent, ``agent_control_plane.bounded.commitment``
is supplied by the runtime's byte-identical ``commitment`` (the same canonical JSON and SHA-256). The end-to-end claim
flows (provisioning, executor, operation key) are in test_atomic_route_valid_flow_identity.py, which needs the package.

Provenance: test_default_* rows ran on main eeb4583a before this change and pass there and here; the rest describe the
new rule (revoked is terminal per (grant_id, revision) on this destination; the envelope's proposal_commitment and
effective_max_effects are bound to the claim) and have no expected-today value.
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

from engine.local_authority_effect import REVOCATION_TABLE, AtomicAuthorityEffectDestination
from engine.safe_executor import EXECUTION_ENVELOPE_VERSION, ExecutionEnvelope, commitment, snapshot_envelope
from test_producer_contract import operation

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
GRANT = "urn:cognous:grant:refund-1"


@pytest.fixture
def cp_commitment(monkeypatch):
    if importlib.util.find_spec("agent_control_plane") is None:
        stub = types.ModuleType("agent_control_plane.bounded")
        stub.commitment = commitment
        monkeypatch.setitem(sys.modules, "agent_control_plane", types.ModuleType("agent_control_plane"))
        monkeypatch.setitem(sys.modules, "agent_control_plane.bounded", stub)
    from agent_control_plane.bounded import commitment as value
    return value


def destination(tmp_path):
    d = AtomicAuthorityEffectDestination(tmp_path / "atomic", clock=lambda: NOW)
    with sqlite3.connect(d.path) as conn:  # trusted host write; provisioning itself needs the Control Plane claim model
        conn.execute(
            "INSERT INTO authority_grants_v1(grant_id,revision,status,institution_id,authority_domain,tenant_id,"
            "not_before,expires_at) VALUES(?,?,?,?,?,?,?,?)",
            (GRANT, "1", "active", "urn:cognous:institution:demo", "customer-refunds", None,
             (NOW - timedelta(hours=1)).isoformat(), (NOW + timedelta(hours=1)).isoformat()))
    return d


def grant(d):
    with sqlite3.connect(d.path) as conn:
        return conn.execute("SELECT revision,status FROM authority_grants_v1 WHERE grant_id=?", (GRANT,)).fetchone()


def tables(d):
    with sqlite3.connect(d.path) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


# --- grant status monotonicity -------------------------------------------------------------------------------------

def test_default_suspension_is_reversible_and_leaves_the_schema_unchanged(tmp_path):
    d = destination(tmp_path)
    before = tables(d)
    for status in ("suspended", "active", "unknown", "active"):
        d.set_grant_status(GRANT, status=status)
        assert grant(d) == ("1", status)
    assert tables(d) == before and REVOCATION_TABLE not in before


def test_default_successor_revision_after_revocation_is_accepted(tmp_path):
    d = destination(tmp_path)
    d.set_grant_status(GRANT, status="revoked")
    d.set_grant_status(GRANT, status="active", revision="2")
    assert grant(d) == ("2", "active")
    d.set_grant_status(GRANT, status="suspended")
    d.set_grant_status(GRANT, status="active")
    assert grant(d) == ("2", "active")


@pytest.mark.parametrize("status", ["active", "suspended", "unknown"])
def test_revoked_revision_cannot_return_to_another_status(tmp_path, status):
    d = destination(tmp_path)
    d.set_grant_status(GRANT, status="revoked")
    with pytest.raises(PermissionError, match="grant revision is revoked"):
        d.set_grant_status(GRANT, status=status)
    with pytest.raises(PermissionError, match="grant revision is revoked"):
        d.set_grant_status(GRANT, status=status, revision="1")
    assert grant(d) == ("1", "revoked")


def test_repeated_revocation_is_idempotent(tmp_path):
    d = destination(tmp_path)
    d.set_grant_status(GRANT, status="revoked")
    d.set_grant_status(GRANT, status="revoked")
    assert grant(d) == ("1", "revoked")


def test_revoked_revision_stays_revoked_after_a_successor(tmp_path):
    d = destination(tmp_path)
    d.set_grant_status(GRANT, status="revoked")
    d.set_grant_status(GRANT, status="active", revision="2")
    with pytest.raises(PermissionError, match="grant revision is revoked"):
        d.set_grant_status(GRANT, status="active", revision="1")
    assert grant(d) == ("2", "active")


def test_revocation_written_before_retention_is_still_terminal(tmp_path):
    """A row already revoked by an earlier revision of this code (no retained record) is treated the same way."""
    d = destination(tmp_path)
    with sqlite3.connect(d.path) as conn:
        conn.execute("UPDATE authority_grants_v1 SET status='revoked' WHERE grant_id=?", (GRANT,))
    with pytest.raises(PermissionError, match="grant revision is revoked"):
        d.set_grant_status(GRANT, status="active")
    d.set_grant_status(GRANT, status="active", revision="2")
    with pytest.raises(PermissionError, match="grant revision is revoked"):
        d.set_grant_status(GRANT, status="active", revision="1")


def test_unknown_grant_and_status_are_still_refused(tmp_path):
    d = destination(tmp_path)
    with pytest.raises(KeyError):
        d.set_grant_status("urn:cognous:grant:missing", status="active")
    with pytest.raises(ValueError):
        d.set_grant_status(GRANT, status="reinstated")


# --- claim-state check: envelope binding and retained revocation ---------------------------------------------------

def snap(**changes):
    op = dataclasses.replace(operation(), **changes)
    return snapshot_envelope(ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op))


def claim_for(s, *, approvals=True, effect_id=None):
    op = s.operation
    return SimpleNamespace(
        effect_id=effect_id or s.effect_id, decision_id=s.decision_id, institution_id=op.institution_id,
        authority_domain=op.authority_domain, actor=op.actor, principal=op.principal, grant_id=op.grant_id,
        grant_revision=op.grant_revision, tenant_id=None, manifest_id=op.manifest_id,
        manifest_version=op.manifest_version, manifest_digest=op.manifest_digest, action_id=op.action_id,
        adapter_id=op.adapter_id, target=op.target, payload_commitment=op.payload_commitment,
        requested_permissions=list(op.requested_permissions), amount=op.amount, unit=op.unit, effects=op.effects,
        operation_commitment=AtomicAuthorityEffectDestination._operation_commitment(s),
        max_effects=op.effective_max_effects,
        approval_state=[SimpleNamespace(proposal_commitment=op.proposal_commitment)] if approvals else [],
        policy_state=[], evidence_state=[],
        not_before=(NOW - timedelta(minutes=1)).isoformat(), expires_at=(NOW + timedelta(minutes=5)).isoformat())


def check(d, claim, s):
    with d._connect() as conn:
        d._validate_claim_state(conn, claim, s, NOW)


def test_matching_envelope_passes_the_claim_check(tmp_path, cp_commitment):
    d = destination(tmp_path)
    s = snap()
    assert d._proposal_bound(claim_for(s), s) is True  # bound through the approval projection
    s = _with_effect_id(s, _derived_effect_id(s, cp_commitment))
    check(d, claim_for(s, approvals=False), s)  # bound through the effect id; approval rows are not provisioned here


def _derived_effect_id(s, cp_commitment):
    op = s.operation
    return cp_commitment({"proposal": op.proposal_commitment, "grant_id": op.grant_id, "grant_revision": op.grant_revision})


def _with_effect_id(s, effect_id):
    return dataclasses.replace(s, effect_id=effect_id)


@pytest.mark.parametrize("approvals", [True, False])
def test_mutated_proposal_commitment_is_refused(tmp_path, cp_commitment, approvals):
    d = destination(tmp_path)
    s = snap()
    s = _with_effect_id(s, _derived_effect_id(s, cp_commitment))
    claim = claim_for(s, approvals=approvals)
    mutated = dataclasses.replace(s, operation=dataclasses.replace(s.operation, proposal_commitment="sha256:" + "c" * 64))
    with pytest.raises(PermissionError, match=r"^execution claim operation mismatch: proposal_commitment$"):
        check(d, claim, mutated)


def test_mutated_effective_max_effects_is_refused(tmp_path, cp_commitment):
    d = destination(tmp_path)
    s = snap()
    s = _with_effect_id(s, _derived_effect_id(s, cp_commitment))
    claim = claim_for(s, approvals=False)
    mutated = dataclasses.replace(s, operation=dataclasses.replace(s.operation, effective_max_effects=2))
    with pytest.raises(PermissionError, match=r"^execution claim operation mismatch: effective_max_effects$"):
        check(d, claim, mutated)


def test_claim_without_approvals_is_bound_through_the_effect_id(tmp_path, cp_commitment):
    d = destination(tmp_path)
    s = snap()  # effect id "effect-1" is not derived from the proposal commitment
    with pytest.raises(PermissionError, match="proposal_commitment"):
        check(d, claim_for(s, approvals=False), s)


def test_claim_check_refuses_a_retained_revocation(tmp_path, cp_commitment):
    d = destination(tmp_path)
    s = snap()
    s = _with_effect_id(s, _derived_effect_id(s, cp_commitment))
    claim = claim_for(s, approvals=False)
    check(d, claim, s)
    d.set_grant_status(GRANT, status="revoked")
    with sqlite3.connect(d.path) as conn:  # even if the row itself were rewritten to active outside the writer
        conn.execute("UPDATE authority_grants_v1 SET status='active' WHERE grant_id=?", (GRANT,))
    with pytest.raises(PermissionError, match="authoritative grant state invalid"):
        check(d, claim, s)


# --- instant parsing (ODES time semantics, section 1) --------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "2026-10-18T12:00:00Z", "2026-10-18T12:00:00+00:00", "2026-10-18T14:00:00+02:00", "2026-10-18T12:00:00-00:00",
    "2026-10-18T12:00:00.5Z", "2026-10-18T12:00:00.123456+00:00", "2026-10-18T23:59:59.999999-23:59",
])
def test_default_valid_instants_parse_as_before(value):
    from engine.local_authority_effect import _parse_time
    expected = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    assert _parse_time(value) == expected and _parse_time(value).tzinfo == timezone.utc


@pytest.mark.parametrize("value", [
    "2026-10-18T12:00:00.1234567Z",   # seventh fraction digit: refused, not truncated
    "2026-10-18T12:00:00+00:60", "2026-10-18T12:00:00+24:00",
    "2026-10-18T23:59:60Z", "2026-10-18T24:00:00Z", "2026-02-30T12:00:00Z", "0000-01-01T00:00:00Z",
    "2026-10-18T12:00:00", "2026-10-18T12:00Z", "2026-10-18 12:00:00Z", "2026-10-18t12:00:00z",
    "2026-10-18T12:00:00Z\n", " 2026-10-18T12:00:00Z", "2026-10-18T12:00:00.Z", "٢026-10-18T12:00:00Z",
    "20261018T120000Z",
])
def test_instants_outside_the_semantics_are_refused(value):
    from engine.local_authority_effect import _parse_time
    with pytest.raises(ValueError):
        _parse_time(value)


def test_claim_check_refuses_a_seven_digit_window_bound(tmp_path, cp_commitment):
    d = destination(tmp_path)
    s = snap()
    s = _with_effect_id(s, _derived_effect_id(s, cp_commitment))
    claim = claim_for(s, approvals=False)
    claim.expires_at = (NOW + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S") + ".0000001+00:00"
    with pytest.raises(ValueError):
        check(d, claim, s)
