"""F-A1 lead integration oracles (Registry r08-v2 Amendment 4). BUILDER_TESTED, non-attesting; NOT independently qualified.

Scope: the REAL trusted installer (``host/authorization_pin``) writes the pin store, anchor, fences and log; the REAL Runtime guard
(``engine/authorization_pin``) reads them. The guard test module's test-only writer is NOT used here. Same-identity, in-process: these tests
prove that the two halves agree on formats, ordering and fail-closed behaviour; they do NOT prove OS-identity isolation (see
``tests/host_pin/test_installer_os_isolation.py`` for the two-identity checks, which need root).

Requires the same environment as the guard suite (OCS_REGISTRY_PIN_DIR, PyNaCl, Control Plane V02, pydantic >= 2.12). The Control Plane export
test needs the unmerged CP export module importable (PYTHONPATH); otherwise it is SKIPPED (INCONCLUSIVE, never a pass).
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import threading
from pathlib import Path

import pytest

import test_authorization_pin_guard as pg
import test_registry_selection_effect_guard as g

from engine import authorization_pin_format as fmt
from engine.authorization_pin import AuthorizationPinConfig, PinReader
from engine.local_authority_effect import AtomicAuthorityEffectDestination
from host.authorization_pin.installer import InstallerConfig, TrustedAuthorizationInstaller

v4, sa = g.v4, g.sa
EXPORT_PROFILE = "ocs-s4/authorization-pin-export/0.1"


class Rig:
    """A governed destination whose pin material is written ONLY by the real installer."""

    def __init__(self, tmp_path, *, installer_deadline=0.3, guard_deadline=1.0, dest_cls=AtomicAuthorityEffectDestination):
        self.case = pg.Case(tmp_path)          # governance fixtures (Registry, trust, claims); its test-only PinWriter is NOT used
        base = Path(tmp_path) / "real-pins"
        base.mkdir()
        self.base = base
        self.cfg_i = InstallerConfig(pin_store_path=base / "pins.sqlite3", fence_dir=base / "fences", anchor_path=base / "pins.anchor.json",
                                     log_path=base / "installer.log", pin_store_id=pg.STORE_ID, exclusive_deadline_seconds=installer_deadline)
        self.installer = TrustedAuthorizationInstaller(self.cfg_i)
        self.cfg_g = AuthorizationPinConfig(pin_store_path=self.cfg_i.pin_store_path, fence_dir=self.cfg_i.fence_dir,
                                            anchor_path=self.cfg_i.anchor_path, pin_store_id=pg.STORE_ID, fence_deadline_seconds=guard_deadline)
        self.dest = dest_cls(self.case.gov.e.root, clock=lambda: self.case.gov.e.now, authorization_pin=self.cfg_g)
        self.dest.configure_selection_authority(self.case.gov.e.anchor)

    def assess(self, env=None, binding=None, **over):
        env = env or pg.mk_env()
        return {"profile": EXPORT_PROFILE, "decision_id": env.decision_id, "state": "active",
                "payload": self.case.payload(env, binding, **over), "terminal_status": None, "reason": "integration"}

    @staticmethod
    def terminal(decision_id, status="revoked"):
        return {"profile": EXPORT_PROFILE, "decision_id": decision_id, "state": "terminal", "payload": None, "terminal_status": status,
                "reason": "integration"}

    def claim(self, env, binding="current", claim_id=None):
        return self.case.claim(env, binding, claim_id)

    def run(self, env, cid, policy=None):
        return self.case.executor(policy, dest=self.dest).execute(envelope=env, claim_id=cid)

    def counts(self):
        return self.case.counts()


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


# ================================================================================================================ installer -> guard
def test_installer_pin_is_read_by_the_guard_and_the_honest_claim_executes(rig):
    env = pg.mk_env()
    result = rig.installer.apply_assessment(rig.assess(env))
    assert result.outcome == "installed" and result.pin_epoch == 1
    env, cid = rig.claim(env)
    out = rig.run(env, cid)
    assert out.status == "executed" and rig.counts()[0] == 1
    receipt = out.control_plane_evidence["authority_receipt"]["authorization_pin"]
    row = PinReader(rig.cfg_g).rows_for("decision-1")[0]
    assert receipt["pin_epoch"] == 1 and receipt["pin_digest"] == row["pin_digest"] == fmt.row_digest({k: v for k, v in row.items() if k != "pin_digest"})
    assert receipt["payload_digest"] == fmt.payload_digest(row["payload"]) and receipt["anchor_committed_epoch"] == 1


def test_installer_store_matches_the_guard_reader_columns_and_is_opened_read_only(rig):
    rig.installer.apply_assessment(rig.assess())
    conn = sqlite3.connect(rig.cfg_i.pin_store_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({fmt.PIN_TABLE})")]
        assert {"pin_epoch", "decision_id", "row_json"} <= set(cols)
        assert dict(conn.execute(f"SELECT key, value FROM {fmt.META_TABLE}").fetchall()) == {"profile": fmt.PIN_PROFILE, "pin_store_id": pg.STORE_ID}
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(f"INSERT INTO {fmt.PIN_TABLE}(pin_epoch, decision_id, status, row_json, pin_digest) VALUES(9,'x','active','{{}}','d')")
    finally:
        conn.close()


def test_original_fa1_substitution_refuses_against_an_installer_written_pin(tmp_path):
    rig = Rig(tmp_path)
    case = rig.case
    wide_payload = {k: sorted(set(v4.PAYLOAD[k]) | {pg.EXTRA[k]}) for k in pg.FIELDS}
    wide_rec = v4.reg_record(payload=wide_payload)
    other = v4.select_stmt(wide_rec, sid="sel-other", operation="op-other")
    ctx = v4.c.ExpectedContext("consumer-test", "tenant-a", "inst-a", "domain-a", "op-other")
    both = (v4.s.GRANT, v4.s.v.AuthorityGrant("consumer-test", "tenant-a", "inst-a", "domain-a", "op-other"))
    t2 = v4.s.v.TrustSnapshot("snap-test-1", 2, "2026-10-27T00:00:00Z", (v4.s.v.IssuerEntry(v4.s.KEY_ID, "issuer-test-1", "ed25519", v4.PK, both),))
    case.gov.dest.sync_selection("snapshot", trust=t2)
    case.gov.e.trust = t2
    case.gov.dest.activate_selection(record_text=v4.r.dumps(wide_rec), selection_text=v4.text(other), context=ctx, trust=case.gov.e.trust,
                                     verifier=v4.ev.Ed25519Verifier())
    wide_policy = g.effect_policy(**{k: frozenset(wide_payload[k]) for k in pg.FIELDS})
    attack = g.binding_of(case.gov.e.state(v4.GS_OTHER), operation="op-other", selection_id="sel-other",
                          effective_policy_digest=sa.policy_digest_of(wide_policy))
    env = pg.mk_env()
    assert rig.installer.apply_assessment(rig.assess(env, case.binding())).outcome == "installed"
    env, cid = rig.claim(env, attack, "attack")
    pg.refused(rig.run(env, cid, wide_policy), "policy_identity_mismatch", "REFUSE", "pin_binding_mismatch:operation")
    assert rig.counts() == (0, 0, 0, 0)


def test_f01_widening_still_refuses_under_a_valid_installer_pin(rig):
    env = pg.mk_env()
    rig.installer.apply_assessment(rig.assess(env))
    env, cid = rig.claim(env)
    wide = g.effect_policy(**{k: frozenset(v4.PAYLOAD[k]) | {pg.EXTRA[k]} for k in pg.FIELDS})
    out = rig.run(env, cid, wide)
    assert out.status == "denied" and rig.counts() == (0, 0, 0, 0)
    assert out.control_plane_evidence.get("selection_refusal") is not None


def test_terminal_transition_refuses_the_decision_and_is_irreversible(rig):
    e1, e2 = pg.mk_env(), pg.mk_env("decision-2", "effect-2")
    rig.installer.apply_assessment(rig.assess(e1))
    rig.installer.apply_assessment(rig.assess(e2))
    assert rig.installer.apply_assessment(rig.terminal("decision-1")).outcome == "complete"
    e1, c1 = rig.claim(e1)
    pg.refused(rig.run(e1, c1), "policy_identity_mismatch", "REFUSE", "pin_status:revoked")
    assert rig.counts() == (0, 0, 0, 0)
    # unrelated decision still executes; the terminal decision cannot be re-installed as active
    e2, c2 = rig.claim(e2)
    assert rig.run(e2, c2).status == "executed"
    assert rig.installer.apply_assessment(rig.assess(pg.mk_env())).outcome == "refused"


def test_missing_pin_holds_with_no_governed_fallback(rig):
    # No pin was installed for the decision, so no fence exists either (the installer creates it with the pin): HOLD, never a governed execution.
    env, cid = rig.claim(pg.mk_env())
    pg.refused(rig.run(env, cid), "verifier_unavailable", "HOLD", "pin_fence_unavailable")
    assert rig.counts() == (0, 0, 0, 0)


def test_provision_claim_cannot_create_or_modify_pins(rig):
    before = (rig.cfg_i.pin_store_path.read_bytes(), rig.cfg_i.anchor_path.read_bytes() if rig.cfg_i.anchor_path.exists() else None)
    env, cid = rig.claim(pg.mk_env())
    after = (rig.cfg_i.pin_store_path.read_bytes(), rig.cfg_i.anchor_path.read_bytes() if rig.cfg_i.anchor_path.exists() else None)
    assert before == after
    assert PinReader(rig.cfg_g).rows_for("decision-1") == []
    public = [n for n in dir(PinReader) if not n.startswith("_")]
    assert not any(n.startswith(("install", "write", "append", "insert", "create", "commit")) for n in public)


# ================================================================================================================ revocation / pending / recovery
class Hold(AtomicAuthorityEffectDestination):
    """Parks the guarded transaction (shared fence held) so a terminal transition can be attempted against a real holder."""
    entered = None
    release = None
    park_at = "after_selection_guard"

    def _transaction_stage(self, stage):
        if stage == type(self).park_at:
            type(self).entered.set()
            assert type(self).release.wait(10)


@pytest.mark.parametrize("park_at,expected", [("after_selection_guard", "executed"), ("after_begin", "denied")])
def test_terminal_cannot_commit_while_an_executor_holds_the_shared_fence_then_completes_after_it_leaves(tmp_path, park_at, expected):
    """Parked AFTER the pin step the in-flight effect commits (it was admitted before the revocation was effective); parked BEFORE the pin step
    the guard re-reads the anchor, sees the pending terminal intent and HOLDs. Both are fail-closed; neither lets the terminal row commit early."""
    Hold.park_at = park_at
    rig = Rig(tmp_path, installer_deadline=0.2, dest_cls=Hold)
    Hold.entered, Hold.release = threading.Event(), threading.Event()
    env = pg.mk_env()
    rig.installer.apply_assessment(rig.assess(env))
    env, cid = rig.claim(env)
    results = {}
    t = threading.Thread(target=lambda: results.setdefault("run", rig.run(env, cid)))
    t.start()
    try:
        assert Hold.entered.wait(10), "guarded transaction never reached its parked stage"
        blocked = rig.installer.apply_assessment(rig.terminal("decision-1"))
        assert blocked.outcome == "pending_blocked"
        rows = PinReader(rig.cfg_g).rows_for("decision-1")
        assert [r["status"] for r in rows] == ["active"]                        # no unfenced terminal commit
        assert rig.installer.pending() is not None                              # barrier preserved
    finally:
        Hold.release.set()
        t.join(15)
    assert results["run"].status == expected
    assert rig.counts()[0] == (1 if expected == "executed" else 0)
    done = rig.installer.reconcile()
    assert any(r.outcome in ("complete", "reconciled") for r in done)
    rows = PinReader(rig.cfg_g).rows_for("decision-1")
    assert [r["status"] for r in rows] == ["active", "revoked"] and rig.installer.pending() is None
    env2, cid2 = rig.claim(pg.mk_env(), claim_id="after")
    pg.refused(rig.run(env2, cid2), "policy_identity_mismatch", "REFUSE", "pin_status:revoked")


def test_a_pending_terminal_intent_holds_new_governed_effects_for_that_decision(rig):
    env = pg.mk_env()
    rig.installer.apply_assessment(rig.assess(env))
    rig.installer.stage_hook = lambda stage: (_ for _ in ()).throw(RuntimeError("crash")) if stage == "after_intent_durable" else None
    with pytest.raises(RuntimeError):
        rig.installer.apply_assessment(rig.terminal("decision-1"))
    env, cid = rig.claim(env)
    pg.refused(rig.run(env, cid), "store_unknown", "HOLD", "pin_transition_pending")
    assert rig.counts() == (0, 0, 0, 0)


@pytest.mark.parametrize("crash_stage", ["after_intent_durable", "after_exclusive_fence", "after_verify", "row_inserted_uncommitted",
                                         "after_row_commit"])
def test_crash_recovery_preserves_the_barrier_and_then_completes(tmp_path, crash_stage):
    rig = Rig(tmp_path)
    env = pg.mk_env()
    rig.installer.apply_assessment(rig.assess(env))

    def boom(stage):
        if stage == crash_stage:
            raise RuntimeError("simulated crash")
    rig.installer.stage_hook = boom
    with pytest.raises(RuntimeError):
        rig.installer.apply_assessment(rig.terminal("decision-1"))
    # a restarted installer: the barrier is still there before reconciliation, and the guard never executes in the gap
    restarted = TrustedAuthorizationInstaller(rig.cfg_i)
    claimed_env, cid = rig.claim(env)
    typed = rig.run(claimed_env, cid)
    assert typed.status == "denied" and rig.counts() == (0, 0, 0, 0)
    restarted.reconcile()
    assert restarted.pending() is None
    assert [r["status"] for r in PinReader(rig.cfg_g).rows_for("decision-1")][-1] == "revoked"
    claimed_env2, cid2 = rig.claim(pg.mk_env(), claim_id="post-recovery")
    pg.refused(rig.run(claimed_env2, cid2), "policy_identity_mismatch", "REFUSE", "pin_status:revoked")


def test_anchor_inconsistency_holds_every_decision_and_the_installer_refuses_to_proceed(rig):
    env = pg.mk_env()
    rig.installer.apply_assessment(rig.assess(env))
    anchor = json.loads(rig.cfg_i.anchor_path.read_text())
    anchor["committed_epoch"] = 7
    rig.cfg_i.anchor_path.write_text(json.dumps(anchor))
    env, cid = rig.claim(env)
    out = rig.run(env, cid)
    assert out.status == "denied" and rig.counts() == (0, 0, 0, 0)
    assert rig.installer.apply_assessment(rig.assess(pg.mk_env("decision-9", "effect-9"))).outcome == "refused"


def test_stale_old_pin_is_fenced_by_the_registry_selection_guard_after_reactivation(rig):
    """Worker A defect B: a profile rotation leaves the old pin active (the export raises instead of returning terminal).
    The pin alone must not keep authority alive: the existing Registry selection guard refuses the old activation as stale."""
    env = pg.mk_env()
    old_binding = rig.case.binding()
    rig.installer.apply_assessment(rig.assess(env, old_binding))
    env, cid = rig.claim(env, old_binding)
    gov = rig.case.gov
    gov.e.withdraw(v4.withdraw_stmt(gov.stmt, 2))
    gov.e.activate(v4.select_stmt(v4.REC, epoch=3, supersedes=v4.sup(v4.withdraw_stmt(gov.stmt, 2))))
    out = rig.run(env, cid)
    assert out.status == "denied" and rig.counts() == (0, 0, 0, 0)
    typed = out.control_plane_evidence["selection_refusal"]
    assert typed["code"] in ("selection_epoch_stale", "policy_identity_mismatch", "selection_withdrawn")


# ================================================================================================================ Control Plane export -> installer
CP_WORKTREE = Path("/home/claude/wt/cp-export")
_CP_SCRIPT = """
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/src"); sys.path.insert(0, sys.argv[1])
from tests import v02_support as v02
from tests.test_bounded_authorization import NOW
import agent_control_plane.authorization_pin_export as export
binding = v02.canonical_binding()
p, r, f, d, cfg = v02.tenant_flow(Path(tempfile.mkdtemp()), binding)
print(json.dumps(export.assess_authorization_pin(f, p, d, now=NOW, host_policy_binding=binding)))
"""


def test_control_plane_export_output_is_accepted_by_the_installer_and_the_format_module(tmp_path):
    """The assessment is produced by the real Control Plane export from a persisted decision in a separate process (the CP and Runtime test
    trees both use a package named ``tests``), then consumed by the real installer and read back by the guard's reader."""
    import os
    import subprocess
    import sys
    if not (CP_WORKTREE / "src/agent_control_plane/authorization_pin_export.py").exists():
        pytest.skip("Control Plane export worktree absent (INCONCLUSIVE, not a pass)")
    proc = subprocess.run([sys.executable, "-I", "-c", _CP_SCRIPT, str(CP_WORKTREE)], capture_output=True, text=True, timeout=120,
                          cwd=str(CP_WORKTREE), env={**os.environ, "PYTHONPATH": ""})
    if proc.returncode != 0:
        pytest.fail(f"Control Plane export subprocess failed: {proc.stderr[-1500:]}")
    assessment = json.loads(proc.stdout.strip().splitlines()[-1])
    assert assessment["profile"] == EXPORT_PROFILE and assessment["state"] == "active" and assessment["terminal_status"] is None
    assert fmt.payload_defect(assessment["payload"]) is None and tuple(assessment["payload"]) == fmt.PAYLOAD_MEMBERS
    base = Path(tmp_path) / "cp-pins"
    base.mkdir()
    ins = TrustedAuthorizationInstaller(InstallerConfig(pin_store_path=base / "p.sqlite3", fence_dir=base / "f", anchor_path=base / "a.json",
                                                        log_path=base / "i.log", pin_store_id="cp-int"))
    assert ins.apply_assessment(assessment).outcome == "installed"
    cfg_g = AuthorizationPinConfig(pin_store_path=base / "p.sqlite3", fence_dir=base / "f", anchor_path=base / "a.json", pin_store_id="cp-int")
    stored = PinReader(cfg_g).rows_for(assessment["decision_id"])[0]
    assert stored["payload"] == assessment["payload"]
