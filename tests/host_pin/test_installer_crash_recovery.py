"""Table-driven crash/recovery cases A to H of Registry r08-v2 Amendment 4 section 7, plus fence, idempotency and log tests."""
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from engine import authorization_pin_format as F
from host.authorization_pin import InstallerError, TrustedAuthorizationInstaller, STAGES
from pin_helpers import (Crash, active, admission, chain_ok, crash_at, make_config, payload, reader_state, terminal)

REPO = Path(__file__).resolve().parents[2]
CHILD = Path(__file__).with_name("child.py")


def child_env():
    return {**os.environ, "PYTHONPATH": f"{REPO}:{REPO / 'src'}"}


def spawn(*args, **kw):
    return subprocess.Popen([sys.executable, str(CHILD), *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=child_env(), cwd=REPO, **kw)


@pytest.fixture
def cfg(tmp_path, fixed_clock):
    return make_config(tmp_path, fixed_clock, exclusive_deadline_seconds=0.3)


@pytest.fixture
def inst(cfg):
    return TrustedAuthorizationInstaller(cfg)


def log_lines(cfg):
    return cfg.log_path.read_text().splitlines() if cfg.log_path.exists() else []


def seed(inst):
    assert inst.apply_assessment(active("dec-1")).outcome == "installed"
    assert inst.apply_assessment(active("dec-2")).outcome == "installed"


# ---------------------------------------------------------------- genesis and basic lifecycle
def test_genesis_creates_store_anchor_fence_dir(cfg, inst):
    anchor = F.anchor_decode(cfg.anchor_path.read_bytes())
    assert (anchor["committed_epoch"], anchor["committed_head_digest"], anchor["intent"]) == (0, F.ZERO_DIGEST, None)
    conn = sqlite3.connect(cfg.pin_store_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {F.PIN_TABLE, F.META_TABLE}
    assert cfg.fence_dir.is_dir() and inst.pending() is None
    assert oct(cfg.pin_store_path.stat().st_mode & 0o777) == "0o644"


def test_store_is_append_only_by_trigger_defense_in_depth(cfg, inst):
    seed(inst)
    conn = sqlite3.connect(cfg.pin_store_path)
    for sql in (f"UPDATE {F.PIN_TABLE} SET status='active'", f"DELETE FROM {F.PIN_TABLE}"):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql)


def test_install_epochs_strictly_monotone_and_chain_verifies(cfg, inst):
    results = [inst.apply_assessment(active(f"dec-{i}")) for i in (1, 2, 3)]
    results.append(inst.apply_assessment(terminal("dec-1")))
    assert [r.outcome for r in results] == ["installed", "installed", "installed", "complete"]
    assert [r.pin_epoch for r in results] == [1, 2, 3, 4]
    anchor, head, rows = reader_state(cfg)
    assert [r["pin_epoch"] for r in rows] == [1, 2, 3, 4] and anchor["committed_epoch"] == 4 and anchor["intent"] is None
    assert chain_ok(cfg) and F.chain_defect(rows, {**anchor, "committed_epoch": 0, "committed_head_digest": F.ZERO_DIGEST}) is None
    assert (rows[3]["status"], rows[3]["payload"]) == ("revoked", rows[0]["payload"])       # terminal rows repeat the full payload
    assert F.fence_path(cfg.fence_dir, "dec-1").exists() and F.fence_path(cfg.fence_dir, "dec-3").exists()


def test_idempotent_install_is_noop_and_conflicts_are_refused(cfg, inst):
    first = inst.apply_assessment(active("dec-1"))
    again = inst.apply_assessment(active("dec-1"))
    assert (again.outcome, again.pin_epoch) == ("noop", first.pin_epoch)
    conflict = inst.apply_assessment(active("dec-1", tenant_id="other-tenant"))
    assert conflict.outcome == "refused" and "conflicting" in conflict.detail
    assert len(reader_state(cfg)[2]) == 1 and inst.pending() is None


def test_reactivation_after_terminal_is_refused_and_terminal_is_irreversible(cfg, inst):
    inst.apply_assessment(active("dec-1"))
    assert inst.apply_assessment(terminal("dec-1", "superseded")).outcome == "complete"
    assert inst.apply_assessment(active("dec-1")).outcome == "refused"
    assert inst.apply_assessment(terminal("dec-1", "superseded")).outcome == "noop"
    assert inst.apply_assessment(terminal("dec-1", "revoked")).outcome == "refused"
    assert [r["status"] for r in reader_state(cfg)[2]] == ["active", "superseded"]


def test_unknown_or_malformed_assessments_are_refused(cfg, inst):
    assert inst.apply_assessment(terminal("never-installed")).outcome == "refused"
    bad = active("dec-1"); bad["state"] = "unknown"
    assert inst.apply_assessment(bad).outcome == "refused"
    bad = active("dec-1"); bad["profile"] = "x"
    assert inst.apply_assessment(bad).outcome == "refused"
    bad = active("dec-1"); bad["payload"]["extra"] = 1
    assert inst.apply_assessment(bad).outcome == "refused"
    assert inst.apply_assessment("nope").outcome == "refused"
    assert reader_state(cfg)[2] == []


# ---------------------------------------------------------------- Amendment 4 section 7, cases A to H (in-process crash seams)
# stage -> (admission verdict for the covered decision right after the crash, whether the terminal row is committed at that point)
TERMINAL_CASES = {
    "before_intent": ("A", "accept", False),
    "after_intent_durable": ("B", "hold_decision", False),
    "after_exclusive_fence": ("C", "hold_decision", False),
    "after_verify": ("D", "hold_decision", False),
    "row_inserted_uncommitted": ("D", "hold_decision", False),
    "before_row_commit": ("D", "hold_decision", False),
    "after_row_commit": ("E", "accept", True),
    "after_log_committed": ("E", "accept", True),
    "after_anchor_complete": ("E+", "accept", True),
}


@pytest.mark.parametrize("stage", sorted(TERMINAL_CASES, key=STAGES.index))
def test_terminal_crash_cases_a_to_e_and_restart_reconcile(cfg, inst, stage):
    case, verdict, committed = TERMINAL_CASES[stage]
    seed(inst)
    before_log = log_lines(cfg)
    inst.stage_hook = crash_at(stage)
    with pytest.raises(Crash):
        inst.apply_assessment(terminal("dec-1"))
    anchor, head, rows = reader_state(cfg)
    adm = admission(cfg, "dec-1")
    assert adm.verdict == verdict, (case, adm)
    assert admission(cfg, "dec-2").verdict == "accept"                        # other decisions are never held by a pending terminal
    assert (head.epoch == 3) == committed and (head.epoch == 2) == (not committed)
    if not committed:
        assert anchor["committed_epoch"] == 2 and head.epoch == 2             # committed epoch did not advance, nothing was reported effective
        assert (anchor["intent"] is None) == (stage == "before_intent")
    assert log_lines(cfg)[:len(before_log)] == before_log                      # append-only
    # restart: a fresh installer (the crashed process is gone) must reconcile before new work
    fresh = TrustedAuthorizationInstaller(cfg)
    results = fresh.reconcile()
    if stage in ("before_intent", "after_anchor_complete"):
        assert results == []
        if stage == "before_intent":                                           # case A: the transition was never recorded; re-derive and apply
            assert fresh.apply_assessment(terminal("dec-1")).outcome == "complete"
    else:
        assert len(results) == 1 and results[0].outcome in ("complete", "reconciled") and results[0].pin_epoch == 3
    anchor, head, rows = reader_state(cfg)
    assert (anchor["committed_epoch"], anchor["intent"], head.epoch) == (3, None, 3)
    assert [r["status"] for r in rows] == ["active", "active", "revoked"] and chain_ok(cfg)
    assert admission(cfg, "dec-1").verdict == "accept" and admission(cfg, "dec-2").verdict == "accept"
    assert any(json.loads(l).get("pin_digest") == rows[2]["pin_digest"] and json.loads(l)["outcome"] in ("committed", "complete", "reconciled")
               for l in log_lines(cfg)), "log must be reconstructable from the row"
    assert log_lines(cfg)[:len(before_log)] == before_log
    assert fresh.apply_assessment(terminal("dec-1")).outcome == "noop"


@pytest.mark.parametrize("stage", sorted(set(STAGES) - {"after_exclusive_fence", "after_verify"}, key=STAGES.index))
def test_install_crash_points_recover_without_unsafe_state(cfg, inst, stage):
    inst.apply_assessment(active("dec-1"))
    inst.stage_hook = crash_at(stage)
    with pytest.raises(Crash):
        inst.apply_assessment(active("dec-2"))
    adm = admission(cfg, "dec-2")
    assert adm.verdict == "accept" and admission(cfg, "dec-1").verdict == "accept"      # an install intent never creates a barrier
    fresh = TrustedAuthorizationInstaller(cfg)
    fresh.reconcile()
    anchor, head, rows = reader_state(cfg)
    assert anchor["intent"] is None and anchor["committed_epoch"] == head.epoch and chain_ok(cfg)
    if head.epoch == 1:                       # row never committed: intent cleared with evidence, assessment is resubmitted
        assert fresh.apply_assessment(active("dec-2")).outcome == "installed"
    else:
        assert fresh.apply_assessment(active("dec-2")).outcome == "noop"
    assert [r["decision_id"] for r in reader_state(cfg)[2]] == ["dec-1", "dec-2"]


def test_case_b_pending_intent_survives_without_installer_and_constructor_does_not_clear_it(cfg, inst):   # also case H
    seed(inst)
    inst.stage_hook = crash_at("after_intent_durable")
    with pytest.raises(Crash):
        inst.apply_assessment(terminal("dec-1"))
    snapshot = cfg.anchor_path.read_bytes()
    assert admission(cfg, "dec-1").verdict == "hold_decision"                  # barrier is in the anchor, not in the installer process
    TrustedAuthorizationInstaller(cfg)                                         # constructing never clears an intent
    assert cfg.anchor_path.read_bytes() == snapshot
    assert admission(cfg, "dec-1").verdict == "hold_decision" and admission(cfg, "dec-1").cause == F.CAUSE_PENDING
    assert reader_state(cfg)[1].epoch == 2


def test_case_f_restart_with_pending_intent_reconciles_before_new_work(cfg, inst):
    seed(inst)
    inst.stage_hook = crash_at("after_intent_durable")
    with pytest.raises(Crash):
        inst.apply_assessment(terminal("dec-1"))
    fresh = TrustedAuthorizationInstaller(cfg)
    out = fresh.apply_assessment(active("dec-3"))                              # new work: the pending terminal is completed first
    assert out.outcome == "installed" and out.pin_epoch == 4
    assert [(r["decision_id"], r["status"]) for r in reader_state(cfg)[2]] == [("dec-1", "active"), ("dec-2", "active"), ("dec-1", "revoked"), ("dec-3", "active")]


# ---------------------------------------------------------------- case C with real processes
def spec(cfg, assessment, stage, hot=False):
    return json.dumps({"store": str(cfg.pin_store_path), "fences": str(cfg.fence_dir), "anchor": str(cfg.anchor_path), "log": str(cfg.log_path),
                       "store_id": cfg.pin_store_id, "stage": stage, "assessment": assessment, "hot_journal": hot})


def test_case_c_killed_while_holding_exclusive_fence(cfg, inst):
    seed(inst)
    child = spawn("kill", spec(cfg, terminal("dec-1"), "after_exclusive_fence"))
    assert child.wait(timeout=20) == -signal.SIGKILL
    fence = os.open(F.fence_path(cfg.fence_dir, "dec-1"), os.O_RDONLY)
    import fcntl
    fcntl.flock(fence, fcntl.LOCK_EX | fcntl.LOCK_NB)                          # the OS released the dead holder's lock
    fcntl.flock(fence, fcntl.LOCK_UN); os.close(fence)
    assert admission(cfg, "dec-1").verdict == "hold_decision" and reader_state(cfg)[1].epoch == 2
    fresh = TrustedAuthorizationInstaller(cfg)
    assert [r.outcome for r in fresh.reconcile()] == ["complete"]
    assert reader_state(cfg)[1].epoch == 3 and chain_ok(cfg)


def test_case_c_hot_journal_left_by_killed_child_is_recovered_by_installer(cfg, inst):
    seed(inst)
    child = spawn("kill", spec(cfg, terminal("dec-1"), "row_inserted_uncommitted", hot=True))
    assert child.wait(timeout=30) == -signal.SIGKILL
    journal = Path(str(cfg.pin_store_path) + "-journal")
    assert journal.exists() and journal.stat().st_size > 0, "the killed child must leave a hot journal"
    ro = sqlite3.connect(f"file:{cfg.pin_store_path}?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.Error):                                      # a read-only executor cannot recover it: it fails closed
            ro.execute(f"SELECT COUNT(*) FROM {F.PIN_TABLE}").fetchone()
    finally:
        ro.close()
    fresh = TrustedAuthorizationInstaller(cfg)                                  # opening read-write recovers (rolls back the uncommitted row)
    assert [r.outcome for r in fresh.reconcile()] == ["complete"]
    assert not journal.exists()
    rows = reader_state(cfg)[2]
    assert [r["status"] for r in rows] == ["active", "active", "revoked"] and chain_ok(cfg)


def test_case_d_killed_before_row_commit_leaves_no_row_and_intent(cfg, inst):
    seed(inst)
    child = spawn("kill", spec(cfg, terminal("dec-2"), "before_row_commit"))
    assert child.wait(timeout=20) == -signal.SIGKILL
    fresh = TrustedAuthorizationInstaller(cfg)
    assert fresh.pending()["decision_id"] == "dec-2"
    assert [r.outcome for r in fresh.reconcile()] == ["complete"] and fresh.pending() is None


def test_case_e_killed_after_row_commit_before_anchor(cfg, inst):
    seed(inst)
    child = spawn("kill", spec(cfg, terminal("dec-1"), "after_row_commit"))
    assert child.wait(timeout=20) == -signal.SIGKILL
    anchor, head, rows = reader_state(cfg)
    assert head.epoch == 3 and anchor["committed_epoch"] == 2 and anchor["intent"]["epoch"] == 3
    assert admission(cfg, "dec-1").verdict == "accept" and rows[2]["status"] == "revoked"   # the terminal row refuses the decision
    out = TrustedAuthorizationInstaller(cfg).reconcile()
    assert [r.outcome for r in out] == ["reconciled"]
    assert reader_state(cfg)[0]["committed_epoch"] == 3


# ---------------------------------------------------------------- case G: anchor missing or inconsistent
def test_case_g_missing_anchor_refuses_construction_and_no_auto_repair(cfg, inst):
    seed(inst)
    cfg.anchor_path.unlink()
    with pytest.raises(InstallerError, match=F.CAUSE_ANCHOR_UNAVAILABLE):
        TrustedAuthorizationInstaller(cfg)
    assert not cfg.anchor_path.exists()                                         # never recreated as genesis
    assert not any(cfg.anchor_path.parent.iterdir())


def test_case_g_running_installer_refuses_when_anchor_vanishes_or_is_corrupt_or_ahead(cfg, inst):
    seed(inst)
    good = cfg.anchor_path.read_bytes()
    cfg.anchor_path.unlink()
    out = inst.apply_assessment(terminal("dec-1"))
    assert out.outcome == "refused" and out.detail == F.CAUSE_ANCHOR_UNAVAILABLE and not cfg.anchor_path.exists()
    cfg.anchor_path.write_bytes(b"garbage")
    assert inst.apply_assessment(active("dec-9")).outcome == "refused" and cfg.anchor_path.read_bytes() == b"garbage"
    with pytest.raises(InstallerError):
        TrustedAuthorizationInstaller(cfg)
    ahead = F.make_anchor(pin_store_id="store-1", committed_epoch=5, committed_head_digest="sha256:" + "a" * 64, intent=None, anchored_at="2026-10-27T12:00:00Z")
    cfg.anchor_path.write_bytes(F.anchor_encode(ahead))                         # store behind the anchor: rollback shape
    out = inst.apply_assessment(active("dec-9"))
    assert out.outcome == "refused" and out.detail == F.CAUSE_ANCHOR_INCONSISTENT
    assert F.anchor_decode(cfg.anchor_path.read_bytes()) == ahead and len(reader_state(cfg)[2]) == 2
    cfg.anchor_path.write_bytes(good)
    assert inst.apply_assessment(active("dec-9")).outcome == "installed"        # operator restored the true anchor


def test_case_g_wrong_store_id_and_stale_anchor_behind_store_are_refused(cfg, inst):
    seed(inst)
    stale = F.make_anchor(pin_store_id="store-1", committed_epoch=0, committed_head_digest=F.ZERO_DIGEST, intent=None, anchored_at="2026-10-27T12:00:00Z")
    cfg.anchor_path.write_bytes(F.anchor_encode(stale))
    assert inst.apply_assessment(active("dec-9")).outcome == "refused"          # Runtime would ACCEPT (ADR 3.6); the installer is stricter
    other = F.make_anchor(pin_store_id="other", committed_epoch=0, committed_head_digest=F.ZERO_DIGEST, intent=None, anchored_at="2026-10-27T12:00:00Z")
    cfg.anchor_path.write_bytes(F.anchor_encode(other))
    with pytest.raises(InstallerError):
        TrustedAuthorizationInstaller(cfg)


def test_tampered_row_breaks_chain_and_installer_refuses(cfg, inst):
    seed(inst)
    conn = sqlite3.connect(cfg.pin_store_path, isolation_level=None)
    conn.execute(f"DROP TRIGGER {F.PIN_TABLE}_no_update")
    row = json.loads(conn.execute(f"SELECT row_json FROM {F.PIN_TABLE} WHERE pin_epoch=2").fetchone()[0])
    row["payload"]["tenant_id"] = "evil"
    conn.execute(f"UPDATE {F.PIN_TABLE} SET row_json=? WHERE pin_epoch=2", (json.dumps(row),))
    conn.close()
    assert inst.apply_assessment(active("dec-3")).outcome == "refused"
    assert len(log_lines(cfg)) > 0


# ---------------------------------------------------------------- fence behavior with real lock holders in child processes
def holder(cfg, decision_id, kind="sh"):
    child = spawn("hold", kind, str(F.fence_path(cfg.fence_dir, decision_id)))
    assert child.stdout.readline().strip() == "ready"
    return child


def release(child):
    child.stdin.close()
    assert child.wait(timeout=10) == 0


def test_terminal_never_commits_while_a_shared_executor_lock_is_held(cfg, inst):
    seed(inst)
    committed_before = reader_state(cfg)[0]["committed_epoch"]
    child = holder(cfg, "dec-1")
    try:
        t0 = time.monotonic()
        out = inst.apply_assessment(terminal("dec-1"))
        assert out.outcome == "pending_blocked" and time.monotonic() - t0 >= 0.25
        anchor, head, rows = reader_state(cfg)
        assert head.epoch == 2 and [r["status"] for r in rows] == ["active", "active"]          # no terminal row
        assert anchor["committed_epoch"] == committed_before                                      # committed epoch unchanged
        assert anchor["intent"]["decision_id"] == "dec-1" and anchor["intent"]["action"] == "terminal"   # intent preserved
        assert admission(cfg, "dec-1").verdict == "hold_decision" and admission(cfg, "dec-2").verdict == "accept"
        assert json.loads(log_lines(cfg)[-1])["outcome"] == "pending_blocked"
        again = inst.apply_assessment(terminal("dec-1"))                                          # still blocked: still nothing committed
        assert again.outcome == "refused" and reader_state(cfg)[1].epoch == 2
        other = inst.apply_assessment(active("dec-3"))                                            # one intent at a time
        assert other.outcome == "refused" and "pending" in other.detail
        assert inst.reconcile()[0].outcome == "pending_blocked" and inst.pending() is not None
    finally:
        release(child)
    done = inst.reconcile()
    assert [r.outcome for r in done] == ["complete"] and inst.pending() is None
    anchor, head, rows = reader_state(cfg)
    assert head.epoch == 3 and rows[2]["status"] == "revoked" and anchor["committed_epoch"] == 3 and chain_ok(cfg)


def test_exclusive_holder_also_blocks_terminal(cfg, inst):
    seed(inst)
    child = holder(cfg, "dec-1", "ex")
    try:
        assert inst.apply_assessment(terminal("dec-1")).outcome == "pending_blocked"
        assert reader_state(cfg)[1].epoch == 2
    finally:
        release(child)
    assert inst.reconcile()[0].outcome == "complete"


def test_starvation_shape_unrelated_decision_lock_does_not_delay(tmp_path, fixed_clock):
    cfg = make_config(tmp_path, fixed_clock, exclusive_deadline_seconds=30.0)
    inst = TrustedAuthorizationInstaller(cfg)
    seed(inst)
    child = holder(cfg, "dec-2")
    try:
        t0 = time.monotonic()
        out = inst.apply_assessment(terminal("dec-1"))
        assert out.outcome == "complete" and time.monotonic() - t0 < 5.0
    finally:
        release(child)


def test_blocked_attempt_commits_after_holder_exits_within_deadline(tmp_path, fixed_clock):
    cfg = make_config(tmp_path, fixed_clock, exclusive_deadline_seconds=15.0)
    inst = TrustedAuthorizationInstaller(cfg)
    seed(inst)
    child = holder(cfg, "dec-1")
    import threading
    timer = threading.Timer(0.5, lambda: child.stdin.close())
    timer.start()
    try:
        assert inst.apply_assessment(terminal("dec-1")).outcome == "complete"      # retried with backoff until the holder ended
    finally:
        timer.join(); child.wait(timeout=10)


def test_no_code_path_commits_terminal_without_exclusive_fence(cfg, inst):
    seed(inst)
    anchor, head, rows = reader_state(cfg)
    bad = F.make_row(pin_epoch=3, decision_id="dec-1", status="revoked", payload=rows[0]["payload"],
                     provenance={"intent_digest": "sha256:" + "2" * 64, "reason": "x"}, prev_pin_digest=head.digest)
    with pytest.raises(AssertionError):
        inst._commit_terminal_row(None, bad)
    with pytest.raises(AssertionError):
        inst._insert_row(bad)                                                      # the install path refuses terminal rows
    assert reader_state(cfg)[1].epoch == 2


# ---------------------------------------------------------------- log and custody
def test_log_is_append_only_records_every_action_and_reconstructable(cfg, inst):
    seen = []
    for a in (active("dec-1"), active("dec-1"), active("dec-1", tenant_id="t2"), terminal("dec-1"), active("dec-1")):
        inst.apply_assessment(a)
        now = log_lines(cfg)
        assert now[:len(seen)] == seen and len(now) > len(seen)
        seen = now
    records = [json.loads(l) for l in seen]
    for r in records:
        assert set(r) >= {"recorded_at", "action", "decision_id", "pin_epoch", "pin_digest", "intent_digest", "outcome", "anchor_before", "anchor_after"}
    assert [r["outcome"] for r in records if r["outcome"] in ("noop", "refused")] == ["noop", "refused", "refused"]
    rows = reader_state(cfg)[2]
    committed = {r["pin_digest"] for r in records if r["outcome"] == "complete"}
    assert committed == {r["pin_digest"] for r in rows}
    # lose the log entirely, then crash after commit: the log record is rebuilt from the pin row
    cfg.log_path.unlink()
    inst.apply_assessment(active("dec-7"))
    inst.stage_hook = crash_at("after_row_commit")
    with pytest.raises(Crash):
        inst.apply_assessment(active("dec-8"))
    TrustedAuthorizationInstaller(cfg).reconcile()
    row8 = reader_state(cfg)[2][-1]
    assert any(json.loads(l)["pin_digest"] == row8["pin_digest"] for l in log_lines(cfg))


def test_assert_custody_best_effort(cfg, inst):
    inst.apply_assessment(active("dec-1"))
    inst.assert_custody()
    os.chmod(cfg.pin_store_path, 0o666)
    from host.authorization_pin import CustodyError
    with pytest.raises(CustodyError):
        inst.assert_custody()
    os.chmod(cfg.pin_store_path, 0o644)
    os.chmod(F.fence_path(cfg.fence_dir, "dec-1"), 0o664)
    with pytest.raises(CustodyError):
        inst.assert_custody()


def test_admission_agrees_with_installer_state_at_every_step(cfg, inst):
    for a in (active("dec-1"), active("dec-2"), terminal("dec-1"), terminal("dec-2", "withdrawn")):
        inst.apply_assessment(a)
        anchor, head, rows = reader_state(cfg)
        assert inst.pending() is None and anchor["committed_epoch"] == head.epoch
        for d in ("dec-1", "dec-2", "dec-unknown"):
            assert F.anchor_admission(anchor, head, d) == F.ACCEPT
        assert chain_ok(cfg)
