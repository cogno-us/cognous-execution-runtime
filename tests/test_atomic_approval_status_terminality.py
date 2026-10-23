"""Component-local tests: approval revocation is terminal per approval_ref on the atomic authority/effect destination.

Parity with grants (test_atomic_grant_status_and_envelope_binding.py). Rows are written by the trusted host directly.
test_default_* rows describe behaviour that must stay unchanged; the rest describe the new rule.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from engine.local_authority_effect import (
    APPROVAL_REVOCATION_TABLE, REVOCATION_TABLE, AtomicAuthorityEffectDestination)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
REF = "urn:cognous:approval:refund-1"
OTHER = "urn:cognous:approval:refund-2"


def destination(tmp_path):
    d = AtomicAuthorityEffectDestination(tmp_path / "atomic", clock=lambda: NOW)
    with sqlite3.connect(d.path) as conn:
        for ref in (REF, OTHER):
            conn.execute(
                "INSERT INTO authority_approvals_v1(approval_ref,role_id,approver,status,grant_id,grant_revision,"
                "proposal_commitment,policy_versions_json,tenant_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (ref, "role", "approver", "active", "g", "1", "sha256:" + "a" * 64, "{}", None))
    return d


def status(d, ref=REF):
    with sqlite3.connect(d.path) as conn:
        return conn.execute("SELECT status FROM authority_approvals_v1 WHERE approval_ref=?", (ref,)).fetchone()[0]


def tables(d):
    with sqlite3.connect(d.path) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_default_unknown_is_reversible_and_leaves_the_schema_unchanged(tmp_path):
    d = destination(tmp_path)
    before = tables(d)
    for s in ("unknown", "active", "unknown", "active"):
        d.set_approval_status(REF, status=s)
        assert status(d) == s
    assert tables(d) == before and APPROVAL_REVOCATION_TABLE not in before


@pytest.mark.parametrize("target", ["active", "unknown"])
def test_revoked_approval_cannot_return_to_another_status(tmp_path, target):
    d = destination(tmp_path)
    d.set_approval_status(REF, status="revoked")
    with pytest.raises(PermissionError, match="approval is revoked"):
        d.set_approval_status(REF, status=target)
    assert status(d) == "revoked"


def test_repeated_revocation_is_idempotent(tmp_path):
    d = destination(tmp_path)
    d.set_approval_status(REF, status="revoked")
    d.set_approval_status(REF, status="revoked")
    assert status(d) == "revoked"


def test_revocation_is_per_approval_ref(tmp_path):
    d = destination(tmp_path)
    d.set_approval_status(REF, status="revoked")
    d.set_approval_status(OTHER, status="unknown")
    d.set_approval_status(OTHER, status="active")
    assert status(d, OTHER) == "active" and status(d) == "revoked"


def test_revocation_written_before_retention_is_still_terminal(tmp_path):
    d = destination(tmp_path)
    with sqlite3.connect(d.path) as conn:
        conn.execute("UPDATE authority_approvals_v1 SET status='revoked' WHERE approval_ref=?", (REF,))
    with pytest.raises(PermissionError, match="approval is revoked"):
        d.set_approval_status(REF, status="active")
    assert status(d) == "revoked"


def test_unknown_approval_and_status_are_still_refused(tmp_path):
    d = destination(tmp_path)
    with pytest.raises(KeyError):
        d.set_approval_status("urn:cognous:approval:missing", status="active")
    with pytest.raises(ValueError):
        d.set_approval_status(REF, status="reinstated")
    assert APPROVAL_REVOCATION_TABLE not in tables(d)


def test_failed_reactivation_leaves_no_partial_write(tmp_path):
    d = destination(tmp_path)
    d.set_approval_status(REF, status="revoked")
    with sqlite3.connect(d.path) as conn:
        before = conn.execute("SELECT * FROM authority_approvals_v1 ORDER BY approval_ref").fetchall()
    with pytest.raises(PermissionError):
        d.set_approval_status(REF, status="active")
    with sqlite3.connect(d.path) as conn:
        assert conn.execute("SELECT * FROM authority_approvals_v1 ORDER BY approval_ref").fetchall() == before


def test_retained_revocation_survives_a_row_rewritten_to_active_outside_the_writer(tmp_path):
    """The effect-time check consults the retained record, so a rewritten row is still refused (defence in depth)."""
    d = destination(tmp_path)
    with d._connect() as conn:
        assert d._approval_revoked(conn, REF) is False
    d.set_approval_status(REF, status="revoked")
    with sqlite3.connect(d.path) as conn:
        conn.execute("UPDATE authority_approvals_v1 SET status='active' WHERE approval_ref=?", (REF,))
    with d._connect() as conn:
        assert d._approval_revoked(conn, REF) is True


# --- persistence, concurrency, effect time, grant behaviour, historical evidence -----------------------------------

import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent


def _spawn(path, code):
    """Start a fresh interpreter (a real process) that runs ``code`` against the destination at ``path``."""
    script = (
        "import sys\nfrom engine.local_authority_effect import AtomicAuthorityEffectDestination\n"
        "d = AtomicAuthorityEffectDestination.__new__(AtomicAuthorityEffectDestination)\n"
        "d.path = __import__('pathlib').Path(sys.argv[1])\n" + code)
    env = {"PYTHONPATH": os.pathsep.join(p for p in sys.path if p), "PATH": os.environ.get("PATH", "")}
    return subprocess.Popen([sys.executable, "-c", script, str(path)], cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env)


def _in_new_process(path, code):
    proc = _spawn(path, code)
    out, err = proc.communicate(timeout=60)
    return SimpleNamespace(returncode=proc.returncode, stdout=out, stderr=err)


def test_revocation_persists_across_a_new_destination_object(tmp_path):
    d = destination(tmp_path)
    d.set_approval_status(REF, status="revoked")
    reopened = AtomicAuthorityEffectDestination(tmp_path / "atomic", clock=lambda: NOW)
    with pytest.raises(PermissionError, match="approval is revoked"):
        reopened.set_approval_status(REF, status="active")
    assert status(d) == "revoked"


def test_revocation_persists_across_a_process_restart(tmp_path):
    d = destination(tmp_path)
    d.set_approval_status(REF, status="revoked")
    code = (
        "try:\n    d.set_approval_status('%s', status='active')\nexcept PermissionError as e:\n    print('REFUSED', e)\n"
        "else:\n    print('ACCEPTED')\n" % REF)
    out = _in_new_process(d.path, code)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "REFUSED approval is revoked; a new approval is required"
    assert status(d) == "revoked"


def test_concurrent_connections_cannot_leave_a_revoked_approval_active(tmp_path):
    """Two destination objects (two connections) race revoke against reactivate; revoked always wins."""
    d = destination(tmp_path)
    refs = [f"urn:cognous:approval:race-{i}" for i in range(20)]
    with sqlite3.connect(d.path) as conn:
        for ref in refs:
            conn.execute(
                "INSERT INTO authority_approvals_v1(approval_ref,role_id,approver,status,grant_id,grant_revision,"
                "proposal_commitment,policy_versions_json,tenant_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (ref, "role", "approver", "unknown", "g", "1", "sha256:" + "a" * 64, "{}", None))
    a = AtomicAuthorityEffectDestination(tmp_path / "atomic", clock=lambda: NOW)
    b = AtomicAuthorityEffectDestination(tmp_path / "atomic", clock=lambda: NOW)
    errors, refused = [], []
    barrier = threading.Barrier(2)

    def revoker():
        barrier.wait()
        for ref in refs:
            try:
                a.set_approval_status(ref, status="revoked")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    def reactivator():
        barrier.wait()
        for ref in refs:
            try:
                b.set_approval_status(ref, status="active")
            except PermissionError:
                refused.append(ref)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    threads = [threading.Thread(target=revoker), threading.Thread(target=reactivator)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == []
    assert {status(d, ref) for ref in refs} == {"revoked"}
    with sqlite3.connect(d.path) as conn:
        retained = {r[0] for r in conn.execute(f"SELECT approval_ref FROM {APPROVAL_REVOCATION_TABLE}")}
    assert retained == set(refs)


def test_concurrent_processes_cannot_leave_a_revoked_approval_active(tmp_path):
    d = destination(tmp_path)
    reactivate = (
        "for _ in range(50):\n    try:\n        d.set_approval_status('%s', status='active')\n"
        "    except PermissionError:\n        pass\n" % REF)
    revoke = "d.set_approval_status('%s', status='revoked')\n" % REF
    procs = [_spawn(d.path, code) for code in (reactivate, revoke, reactivate)]
    results = [p.communicate(timeout=120) + (p.returncode,) for p in procs]
    assert [r[2] for r in results] == [0, 0, 0], results
    assert status(d) == "revoked"


def _effect_time_fixture(tmp_path):
    """Grant row and an active approval row for the producer-contract operation, plus a claim that expects them."""
    import test_atomic_grant_status_and_envelope_binding as g
    d = g.destination(tmp_path)  # inserts the active grant the operation names
    s = g.snap()
    claim = g.claim_for(s)
    op = s.operation
    expected = SimpleNamespace(
        approval_ref=REF, proposal_commitment=op.proposal_commitment, policy_versions=[], tenant_id=None)
    claim.approval_state = [expected]
    with sqlite3.connect(d.path) as conn:
        conn.execute(
            "INSERT INTO authority_approvals_v1(approval_ref,role_id,approver,status,grant_id,grant_revision,"
            "proposal_commitment,policy_versions_json,tenant_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (REF, "role", "approver", "active", op.grant_id, op.grant_revision, op.proposal_commitment, "[]", None))
    return g, d, s, claim


def test_effect_time_check_passes_for_an_active_approval(tmp_path):
    g, d, s, claim = _effect_time_fixture(tmp_path)
    g.check(d, claim, s)


def test_revoke_then_reactivate_then_effect_time_check_still_refuses(tmp_path):
    g, d, s, claim = _effect_time_fixture(tmp_path)
    g.check(d, claim, s)
    d.set_approval_status(REF, status="revoked")
    with pytest.raises(PermissionError, match="authoritative approval state invalid"):
        g.check(d, claim, s)
    with pytest.raises(PermissionError, match="approval is revoked"):
        d.set_approval_status(REF, status="active")
    with pytest.raises(PermissionError, match="authoritative approval state invalid"):
        g.check(d, claim, s)


def test_effect_time_check_refuses_a_row_rewritten_to_active_outside_the_writer(tmp_path):
    g, d, s, claim = _effect_time_fixture(tmp_path)
    d.set_approval_status(REF, status="revoked")
    with sqlite3.connect(d.path) as conn:
        conn.execute("UPDATE authority_approvals_v1 SET status='active' WHERE approval_ref=?", (REF,))
    with pytest.raises(PermissionError, match="authoritative approval state invalid"):
        g.check(d, claim, s)


def test_effect_time_check_for_a_never_revoked_unknown_toggle_is_unchanged(tmp_path):
    g, d, s, claim = _effect_time_fixture(tmp_path)
    d.set_approval_status(REF, status="unknown")
    with pytest.raises(PermissionError, match="authoritative approval state invalid"):
        g.check(d, claim, s)
    d.set_approval_status(REF, status="active")
    g.check(d, claim, s)
    assert APPROVAL_REVOCATION_TABLE not in tables(d)


def test_grant_behaviour_is_unchanged_and_independent_of_approval_revocation(tmp_path):
    g, d, s, claim = _effect_time_fixture(tmp_path)
    d.set_grant_status(g.GRANT, status="suspended")
    d.set_grant_status(g.GRANT, status="active")  # suspension stays reversible
    d.set_approval_status(REF, status="revoked")
    assert g.grant(d) == ("1", "active")  # revoking an approval does not touch the grant
    d.set_grant_status(g.GRANT, status="revoked")
    with pytest.raises(PermissionError, match="grant revision is revoked"):
        d.set_grant_status(g.GRANT, status="active", revision="1")
    d.set_grant_status(g.GRANT, status="active", revision="2")  # successor revision is still accepted
    assert g.grant(d) == ("2", "active")
    assert REVOCATION_TABLE in tables(d) and APPROVAL_REVOCATION_TABLE in tables(d)


def test_grant_lifecycle_without_approval_revocation_creates_no_approval_table(tmp_path):
    g, d, s, claim = _effect_time_fixture(tmp_path)
    before = tables(d)
    d.set_grant_status(g.GRANT, status="revoked")
    d.set_grant_status(g.GRANT, status="active", revision="2")
    assert tables(d) - before == {REVOCATION_TABLE}


def test_historical_evidence_is_retained(tmp_path):
    """Revocation adds a retained record; the approval row and its history are kept, never deleted or rewritten."""
    d = destination(tmp_path)
    with sqlite3.connect(d.path) as conn:
        before = conn.execute("SELECT * FROM authority_approvals_v1 WHERE approval_ref=?", (REF,)).fetchone()
    d.set_approval_status(REF, status="revoked")
    with sqlite3.connect(d.path) as conn:
        after = conn.execute("SELECT * FROM authority_approvals_v1 WHERE approval_ref=?", (REF,)).fetchone()
    assert after[:3] == before[:3] and after[4:] == before[4:] and after[3] == "revoked"
    d.remove_authoritative_state("approval", REF)  # the owner removes the row; the retained record stays
    with sqlite3.connect(d.path) as conn:
        assert conn.execute(f"SELECT approval_ref FROM {APPROVAL_REVOCATION_TABLE}").fetchall() == [(REF,)]
