"""V04 acceptance oracles: durable, scope-bound Registry selection authority inside the existing Runtime destination database.

BUILDER_TESTED developer evidence. NOT independently qualified. NOT an effect path: nothing here executes an effect, and the effect-time guard in
`execute_claim_atomic` is V05 (not implemented, not authorized).

PREDECLARED INVENTORY (written and committed BEFORE `engine/registry_selection_authority.py` exists; the assertions below are the expected outcomes):

  ACTIVATION / DURABLE COMMIT (6)
    A-01 baseline, snapshot sync, activation: row `active`, activation_ref == entry_hash of the committed `activate` entry, generation 1
    A-02 activation on an un-baselined store refuses `store_unknown` (HOLD) and writes nothing
    A-03 forged signature refuses, no log entry, no row
    A-04 injected fault inside the transaction rolls state AND log back together; the next activation succeeds
    A-05 the state survives a new PROCESS (subprocess reads it back and verifies the store)
    A-06 no caller can supply activation_ref; the read interface is immutable and carries no writer
  WITHDRAWAL / REVOCATION (6)
    W-01 withdraw: state `withdrawn`, transition_ref == withdraw entry hash, generation bumps, high-water rises
    W-02 an older/equal epoch cannot restore authority after withdrawal (REFUSE_STALE)
    W-03 withdraw needs the exact current row and an active row
    W-04 snapshot revocation of a pair: state `revoked`, pair retained, generation bumps; a revoked pair cannot be re-activated and a fresh higher epoch is new authority
    W-05 issuer-key revocation (cause issuer_key_revoked) revokes the row; the key stays unusable
    W-06 saturated epoch: activation at the maximum succeeds once, nothing can advance, revocation still works; generation saturation rule
  HIGH-WATER / LINEAGE / SNAPSHOT (6)
    H-01 stale epoch (<= high-water) and broken supersession refuse
    H-02 lineage replacement refused with `selection_conflict` / lineage_replacement_prohibited; ratchet, high-water, generation, revoked unchanged
    H-03 snapshot version rollback and same-version/different-fingerprint refuse (REFUSE_STALE)
    H-04 a later snapshot cannot omit an earlier revocation
    H-05 the min_epochs floor cannot be lowered and floors stale epochs
    H-06 activation requires the synced snapshot (an unsynced newer snapshot refuses)
  RATCHET / GENERATION (5)
    R-01 a required activation sets the durable ratchet; an optional presentation then refuses participation_mismatch
    R-02 withdraw, revoke, restart and direct deletion never clear the ratchet; withdrawal and revocation stay binding after restart
    R-03 authenticated ratchet provisioning sets it without any activation
    R-04 non_participation preconditions (refused over a row, admitted for a fresh scope)
    R-05 policy_generation is monotone and distinct from selection_epoch
  AUDIT LOG (5)
    L-01 exactly 24 members, domain-separated hash recomputes independently, prev chain, gap-free, byte-equal to Registry canonical_bytes
    L-02 (x2) closed entry-type/cause/nullability matrix refuses invalid combinations
    L-03 a reasons-registry label/digest outside the allow-list refuses `field_invalid` and writes nothing
    L-04 log, revoked, evidence, lineage and ratchet rows are insert-only
  RESTART / RESTORE (6)
    S-01 restoring an older database copy (anchor ahead) makes the store `unknown`: required scope HOLD, writers HOLD
    S-02 missing anchor on a store with history is `unknown`
    S-03 anchored entry hash mismatch is `unknown`
    S-04 a store ahead of a stale anchor with an unbroken chain is accepted; the next write advances the anchor
    S-05 genesis baseline is refused when an anchor exists (an emptied database is not a new store)
    S-06 anchored digests that replay does not reproduce are `unknown`
  CORRUPT / UNKNOWN (5)
    C-01 an edited log entry breaks the chain: `unknown`, reads HOLD
    C-02 a sequence gap: `unknown`
    C-03 a state row with no matching log entry: `unknown`
    C-04 a deleted ratchet row: `unknown` (never "optional")
    C-05 an entry carrying an unknown reasons label (valid hash) is rejected by replay

Environment: `OCS_REGISTRY_PIN_DIR` names a Registry checkout at the accepted pin (943ab183...) and PyNaCl is installed (Registry
requirements-ed25519.lock.txt); without them this module is reported NOT RUN (skipped with a reason), never passed with a double.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import inspect
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PIN_DIR = os.environ.get("OCS_REGISTRY_PIN_DIR")
if not PIN_DIR or not (Path(PIN_DIR) / "registry_successor").is_dir():
    pytest.skip("NOT RUN: OCS_REGISTRY_PIN_DIR is not set to a Registry checkout at the pin", allow_module_level=True)
PIN_DIR = str(Path(PIN_DIR).resolve())
sys.path.insert(0, PIN_DIR)
sys.path.insert(0, str(Path(PIN_DIR) / "tests"))
try:
    import nacl.bindings as nb
except ImportError:
    pytest.skip("NOT RUN: PyNaCl (Registry requirements-ed25519.lock.txt) is not installed", allow_module_level=True)

import r08_support as r  # noqa: E402  (Registry test builders, reused)
import s01_support as s  # noqa: E402
from registry_adapters import ed25519_verifier as ev  # noqa: E402
from registry_successor import common as c  # noqa: E402
from registry_successor import v2_f1ab as f1ab  # noqa: E402
from registry_successor import v2_policy_identity as ident  # noqa: E402

from engine import registry_selection_authority as sa  # noqa: E402
from engine.local_authority_effect import AtomicAuthorityEffectDestination  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PK, SK = nb.crypto_sign_seed_keypair(bytes(range(32)))
OTHER_PK, OTHER_SK = nb.crypto_sign_seed_keypair(bytes(range(1, 33)))
DET = "sha256:" + "d" * 64
DET2 = "sha256:" + "e" * 64
INT_MAX = 2147483647
GS = dict(consumer_id="consumer-test", tenant="tenant-a", institution="inst-a", authority_domain="domain-a", operation="op-test",
          family=f1ab.FAMILY_F1AB)
GS_OTHER = dict(GS, operation="op-other")
FIELDS = ("allowed_actions", "allowed_adapters", "allowed_units", "allowed_institutions", "allowed_authority_domains")
PAYLOAD = {"allowed_actions": ["act-list"], "allowed_adapters": ["a-local"], "allowed_units": ["u-read"],
           "allowed_institutions": ["inst-a"], "allowed_authority_domains": ["domain-a"]}


# ------------------------------------------------------------------------------------------------------------------ builders
def reg_record(**over) -> dict:
    body = dict(r.make_record_v2(), family=f1ab.FAMILY_F1AB, record_id="f1ab-v04-record", payload=dict(PAYLOAD))
    body.update(over)
    return r.seal_record(body)


def sign(statement, sk=SK) -> str:
    import base64
    return base64.b64encode(nb.crypto_sign(s.V2_DOMAIN + c.canonical_bytes(statement), sk)[:64]).decode("ascii")


def select_stmt(rec, *, sid="sel-test-1", epoch=1, participation="required", supersedes=None, **over) -> dict:
    return r.make_statement_v2(rec, family=f1ab.FAMILY_F1AB, participation=participation, selection_id=sid, epoch=epoch, supersedes=supersedes, **over)


def sup(prev: dict) -> dict:
    return {"selection_id": prev["selection_id"], "epoch": prev["epoch"], "statement_digest": c.digest(prev), "decision_ref": "decision-test-9"}


def withdraw_stmt(prev: dict, epoch: int, **over) -> dict:
    stmt = copy.deepcopy(prev)
    stmt.update({"disposition": "withdraw", "epoch": epoch, "record": None, "evaluator": None, "supersedes": sup(prev)})
    stmt.update(over)
    return stmt


def text(stmt, sk=SK) -> str:
    return r.dumps(s.envelope(stmt, signature=sign(stmt, sk), pub=PK))


def trust(version=1, **kw):
    return s.trust(version=version, pub=PK, **kw)


def fingerprint(t) -> str:
    return ident.trust_snapshot_fingerprint_from_verified(t)


def effective_digest() -> str:
    return ident.effective_policy_digest(f1ab.FAMILY_F1AB, {k: sorted(PAYLOAD[k]) for k in FIELDS})


class Env:
    def __init__(self, tmp_path, *, reasons=None):
        self.root = Path(tmp_path) / "dest"
        self.anchor = Path(tmp_path) / "anchor.json"
        self.now = r.NOW
        self.reasons = reasons
        self.open()

    def open(self):
        self.dest = AtomicAuthorityEffectDestination(self.root, clock=lambda: self.now)
        kw = {} if self.reasons is None else {"reasons_registry": self.reasons}
        self.store = self.dest.configure_selection_authority(self.anchor, **kw)
        return self

    def boot(self, version=1, **tkw):
        self.dest.sync_selection("baseline", determination_ref=DET)
        self.trust = trust(version, **tkw)
        self.dest.sync_selection("snapshot", trust=self.trust)
        return self

    def sync(self, version, **tkw):
        t = trust(version, **tkw)
        res = self.dest.sync_selection("snapshot", trust=t)
        self.trust = t  # only a successfully synced snapshot becomes the current one
        return res

    def activate(self, stmt, rec=None, *, trust_=None, sk=SK):
        rec = rec if rec is not None else REC
        return self.dest.activate_selection(record_text=r.dumps(rec), selection_text=text(stmt, sk), context=s.CTX,
                                            trust=trust_ or self.trust, verifier=ev.Ed25519Verifier())

    def withdraw(self, stmt, *, trust_=None):
        return self.dest.withdraw_selection(selection_text=text(stmt), context=s.CTX, trust=trust_ or self.trust, verifier=ev.Ed25519Verifier())

    def state(self, gs=GS):
        return self.dest.read_selection_state(gs)

    def verify(self):
        return self.dest.verify_selection_store()

    def sql(self, statement, params=(), *, script=None):
        conn = sqlite3.connect(self.dest.path, isolation_level=None)
        try:
            if script:
                conn.executescript(script)
            return conn.execute(statement, params).fetchall() if statement else None
        finally:
            conn.close()

    def entries(self):
        return [json.loads(row[0]) for row in self.sql("SELECT entry_json FROM selection_log_v1 ORDER BY activation_sequence")]

    def head(self):
        rows = self.sql("SELECT activation_sequence, entry_hash FROM selection_log_v1 ORDER BY activation_sequence DESC LIMIT 1")
        return tuple(rows[0]) if rows else None

    def dump(self):
        out = {}
        for table in sa.TABLES:
            out[table] = self.sql(f"SELECT * FROM {table} ORDER BY 1, 2" if table != "selection_log_v1" else f"SELECT * FROM {table} ORDER BY 1")
        return out

    def drop_guards(self):
        self.sql(None, script=";".join(f"DROP TRIGGER IF EXISTS {name[0]}" for name in
                                       self.sql("SELECT name FROM sqlite_master WHERE type='trigger'")))

    def rewrite_anchor(self, **over):
        data = json.loads(self.anchor.read_text())
        anchor = data["anchor"]
        anchor.update(over)
        self.anchor.write_text(json.dumps({"anchor": anchor, "anchor_digest": sa.anchor_digest(anchor)}))


REC = reg_record()
ST1 = select_stmt(REC)


def refused(call, code, cls=None, cause=None):
    with pytest.raises(sa.SelectionRefusal) as info:
        call()
    err = info.value
    assert err.code == code, (err.code, err.cause)
    if cls is not None:
        assert err.refusal_class == cls
    if cause is not None:
        assert err.cause == cause
    return err


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path).boot()


# ================================================================================================================== ACTIVATION
def test_a01_activation_commits_state_and_log_and_derives_activation_ref(env):
    res = env.activate(ST1)
    entries = env.entries()
    assert [e["activation_sequence"] for e in entries] == [1, 2, 3]
    act = entries[-1]
    assert act["entry_type"] == "activate" and act["policy_generation"] == 1 and act["supersedes"] is None
    state = env.state()
    assert state.status == "active" and state.activation_ref == res.activation_ref == env.head()[1] == res.entry_hash
    assert state.policy_generation == 1 and state.selection_epoch == 1 and state.selection_id == "sel-test-1"
    assert state.participation == "required" and state.scope_required is True
    assert state.record_digest == REC["record_digest"] and state.effective_policy_digest == effective_digest()
    assert state.trust_snapshot_version == 1 and state.trust_snapshot_fingerprint == fingerprint(env.trust)
    assert state.selection_digest == c.digest(ST1) and env.verify().state == "known"


def test_a02_unbaselined_store_refuses_activation_and_writes_nothing(tmp_path):
    e = Env(tmp_path)
    e.trust = trust(1)
    refused(lambda: e.activate(ST1), "store_unknown", "HOLD")
    assert e.sql("SELECT count(*) FROM selection_log_v1") == [(0,)] and e.verify().state == "unknown"


def test_a03_forged_signature_refuses_without_any_write(env):
    before, head = env.dump(), env.head()
    refused(lambda: env.activate(ST1, sk=OTHER_SK), "signature_invalid")
    assert env.head() == head and env.dump() == before and env.state().status == "not_activated"


def test_a04_fault_inside_the_transaction_rolls_back_state_and_log_together(env, monkeypatch):
    before = env.dump()

    def boom(stage):
        if stage == "after_log_insert":
            raise RuntimeError("injected fault")

    monkeypatch.setattr(env.store, "_stage", boom)
    with pytest.raises(RuntimeError):
        env.activate(ST1)
    assert env.dump() == before and env.verify().state == "known"
    monkeypatch.undo()
    assert env.activate(ST1).entry_type == "activate" and env.state().status == "active"


def test_a05_state_survives_a_new_process(env):
    res = env.activate(ST1)
    code = ("import json,sys; from datetime import datetime,timezone; from engine.local_authority_effect import AtomicAuthorityEffectDestination as D;"
            f"d=D({str(env.root)!r}, clock=lambda: datetime(2026,10,25,12,tzinfo=timezone.utc)); d.configure_selection_authority({str(env.anchor)!r});"
            f"st=d.read_selection_state({GS!r}); v=d.verify_selection_store();"
            "print(json.dumps([st.status, st.activation_ref, st.policy_generation, st.selection_epoch, v.state]))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=f"{ROOT}:{PIN_DIR}"))
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["active", res.activation_ref, 1, 1, "known"]


def test_a06_activation_ref_is_never_an_input_and_the_read_interface_is_immutable(env):
    params = inspect.signature(env.dest.activate_selection).parameters
    assert "activation_ref" not in params and "policy_generation" not in params
    with pytest.raises(TypeError):
        env.dest.activate_selection(activation_ref="sha256:" + "a" * 64, record_text="", selection_text="", context=s.CTX, trust=env.trust,
                                    verifier=ev.Ed25519Verifier())
    env.activate(ST1)
    state = env.state()
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.status = "revoked"
    assert not any(callable(getattr(state, n)) for n in dir(state) if not n.startswith("_"))


# ================================================================================================================== WITHDRAW / REVOKE
def test_w01_withdraw_is_durable_and_bumps_generation(env):
    env.activate(ST1)
    w = withdraw_stmt(ST1, 2)
    res = env.withdraw(w)
    state = env.state()
    assert state.status == "withdrawn" and state.transition_ref == res.transition_ref == env.head()[1]
    assert state.policy_generation == 2 and state.selection_epoch == 2 and state.scope_required is True
    last = env.entries()[-1]
    assert last["entry_type"] == "withdraw" and last["state_before"] == "active" and last["state_after"] == "withdrawn"
    assert last["supersedes"] == {"selection_id": "sel-test-1", "epoch": 1, "selection_digest": c.digest(ST1)}
    assert env.sql("SELECT high_water_epoch FROM selection_highwater_v1 WHERE hw_kind='scope'") == [(2,)]


def test_w02_older_or_equal_epoch_cannot_restore_authority_after_withdrawal(env):
    env.activate(ST1)
    env.withdraw(withdraw_stmt(ST1, 2))
    err = refused(lambda: env.activate(ST1), "selection_epoch_stale", "REFUSE_STALE")
    assert err.cause is None or isinstance(err.cause, str)
    refused(lambda: env.activate(select_stmt(REC, epoch=2, supersedes=sup(ST1))), "selection_epoch_stale", "REFUSE_STALE")
    assert env.state().status == "withdrawn" and env.state().policy_generation == 2


def test_w03_withdraw_needs_the_exact_current_row_and_an_active_row(env):
    env.activate(ST1)
    wrong = withdraw_stmt(ST1, 2)
    wrong["supersedes"] = dict(wrong["supersedes"], statement_digest="sha256:" + "0" * 64)
    refused(lambda: env.withdraw(wrong), "selection_supersession_invalid")
    w2 = withdraw_stmt(ST1, 2)
    env.withdraw(w2)
    refused(lambda: env.withdraw(withdraw_stmt(w2, 3)), "selection_withdrawn")
    assert env.state().policy_generation == 2


def test_w04_snapshot_revocation_is_terminal_for_the_pair_but_a_fresh_epoch_is_new_authority(env):
    env.activate(ST1)
    res = env.sync(2, revoked_selections=frozenset({("sel-test-1", 1)}))
    state = env.state()
    assert state.status == "revoked" and state.policy_generation == 2 and state.scope_required is True
    kinds = [e["entry_type"] for e in env.entries()]
    assert kinds[-2:] == ["sync", "revoke"] and len(res.entry_hashes) == 2
    assert env.sql("SELECT kind, selection_id, epoch FROM selection_revoked_v1") == [("pair", "sel-test-1", 1)]
    refused(lambda: env.activate(ST1), "selection_revoked")
    st2 = select_stmt(REC, epoch=2, supersedes=sup(ST1))
    env.activate(st2)
    assert env.state().status == "active" and env.state().policy_generation == 3
    assert env.sql("SELECT count(*) FROM selection_revoked_v1") == [(1,)]


def test_w05_issuer_key_revocation_revokes_the_row_and_keeps_the_key_unusable(env):
    env.activate(ST1)
    env.sync(2, revoked_key_ids=frozenset({s.KEY_ID}))
    assert env.state().status == "revoked"
    last = env.entries()[-1]
    assert last["entry_type"] == "revoke" and last["cause"] == "issuer_key_revoked" and last["issuer_key_id"] == s.KEY_ID
    assert env.sql("SELECT kind, key_id FROM selection_revoked_v1 WHERE kind='key'") == [("key", s.KEY_ID)]
    refused(lambda: env.activate(select_stmt(REC, epoch=5)), "issuer_key_revoked")


def test_w06_saturation_epoch_blocks_advance_but_revocation_still_works(env):
    top = select_stmt(REC, epoch=INT_MAX)
    env.activate(top)
    refused(lambda: env.activate(select_stmt(REC, epoch=INT_MAX, supersedes=sup(top))), "selection_supersession_invalid")
    refused(lambda: env.activate(top), "selection_epoch_stale", "REFUSE_STALE")
    env.sync(2, revoked_selections=frozenset({("sel-test-1", INT_MAX)}))
    assert env.state().status == "revoked"
    assert sa.next_generation(5, saturating=True) == 6 and sa.next_generation(INT_MAX, saturating=True) == INT_MAX
    refused(lambda: sa.next_generation(INT_MAX, saturating=False), "field_invalid")


# ================================================================================================================== HIGH-WATER / LINEAGE
def test_h01_stale_epoch_and_broken_supersession_refuse(env):
    st5 = select_stmt(REC, epoch=5)
    env.activate(st5)
    refused(lambda: env.activate(select_stmt(REC, epoch=4, supersedes=sup(st5))), "selection_supersession_invalid")
    refused(lambda: env.activate(st5), "selection_epoch_stale", "REFUSE_STALE")
    refused(lambda: env.activate(select_stmt(REC, epoch=6)), "selection_supersession_invalid")  # history exists: supersedes may not be null
    wrong = sup(st5)
    wrong["statement_digest"] = "sha256:" + "1" * 64
    refused(lambda: env.activate(select_stmt(REC, epoch=6, supersedes=wrong)), "selection_supersession_invalid")
    assert env.state().selection_epoch == 5 and env.state().policy_generation == 1


def test_h02_lineage_replacement_is_refused_and_nothing_resets(env):
    env.activate(ST1)
    before = env.dump()
    other = select_stmt(reg_record(record_id="f1ab-v04-other"), sid="sel-test-2", epoch=9)
    refused(lambda: env.activate(other, reg_record(record_id="f1ab-v04-other")), "selection_conflict", "REFUSE", "lineage_replacement_prohibited")
    refused(lambda: env.withdraw(withdraw_stmt(other, 10)), "selection_conflict", "REFUSE", "lineage_replacement_prohibited")
    assert env.dump() == before


def test_h03_snapshot_rollback_refuses(env):
    env.sync(3)
    refused(lambda: env.sync(2), "trust_snapshot_version_stale", "REFUSE_STALE")
    refused(lambda: env.sync(3, revoked_key_ids=frozenset({"key-other-9"})), "trust_snapshot_version_stale", "REFUSE_STALE")
    head = env.head()
    assert env.sync(3).entry_hashes == () and env.head() == head  # identical redelivery is accepted and writes nothing
    assert env.verify().state == "known"


def test_h04_a_later_snapshot_cannot_omit_an_earlier_revocation(env):
    env.sync(2, revoked_selections=frozenset({("sel-gone-1", 1)}))
    refused(lambda: env.sync(3), "trust_snapshot_invalid", "REFUSE", "revocation_omitted")
    assert env.sql("SELECT count(*) FROM selection_revoked_v1") == [(1,)]


def test_h05_epoch_floor_cannot_be_lowered_and_floors_stale_epochs(env):
    env.sync(2, min_epochs=(("sel-test-1", 3),))
    refused(lambda: env.activate(select_stmt(REC, epoch=2)), "selection_epoch_stale")
    refused(lambda: env.sync(3, min_epochs=(("sel-test-1", 2),)), "trust_snapshot_invalid", "REFUSE", "min_epoch_floor_lowered")
    assert env.activate(select_stmt(REC, epoch=3)).entry_type == "activate"


def test_h06_activation_requires_the_synced_snapshot(env):
    newer = trust(5)
    refused(lambda: env.activate(ST1, trust_=newer), "trust_snapshot_invalid", "REFUSE", "snapshot_not_synced")
    assert env.state().status == "not_activated"


# ================================================================================================================== RATCHET / GENERATION
def test_r01_required_activation_sets_the_ratchet_and_blocks_optional(env):
    env.activate(ST1)
    assert env.sql("SELECT count(*) FROM selection_scope_required_v1") == [(1,)]
    assert env.entries()[-1]["ratchet_after"] is True
    refused(lambda: env.activate(select_stmt(REC, epoch=2, participation="optional", supersedes=sup(ST1))), "participation_mismatch")
    refused(lambda: env.withdraw(withdraw_stmt(ST1, 2, participation="optional")), "participation_mismatch")


def test_r02_ratchet_and_binding_outcomes_survive_withdrawal_revocation_and_restart(env):
    env.activate(ST1)
    env.withdraw(withdraw_stmt(ST1, 2))
    env.open()
    assert env.state().status == "withdrawn" and env.state().scope_required is True
    refused(lambda: env.activate(ST1), "selection_epoch_stale")
    env.sync(2, revoked_selections=frozenset({("sel-test-1", 2)}))
    env.open()
    assert env.state().status == "revoked" and env.state().scope_required is True and env.verify().state == "known"
    with pytest.raises(sqlite3.DatabaseError):
        env.sql("DELETE FROM selection_scope_required_v1")
    assert env.sql("SELECT count(*) FROM selection_scope_required_v1") == [(1,)]


def test_r03_authenticated_ratchet_provisioning_sets_it_without_an_activation(env):
    env.dest.sync_selection("ratchet_provision", governed_scope=GS, determination_ref=DET2)
    state = env.state()
    assert state.status == "not_activated" and state.scope_required is True
    last = env.entries()[-1]
    assert (last["entry_type"], last["cause"], last["ratchet_after"], last["participation"]) == ("sync", "ratchet_provision", True, "required")
    refused(lambda: env.activate(select_stmt(REC, participation="optional")), "participation_mismatch")
    assert env.sql("SELECT count(*) FROM selection_activation_v1") == [(0,)]


def test_r04_non_participation_preconditions(env):
    env.activate(ST1)
    refused(lambda: env.dest.sync_selection("non_participation", governed_scope=GS, determination_ref=DET2), "field_invalid", "REFUSE")
    env.dest.sync_selection("non_participation", governed_scope=GS_OTHER, determination_ref=DET2)
    env.dest.sync_selection("non_participation", governed_scope=GS_OTHER, determination_ref=DET2)  # repeated identical entry stays admissible
    other = env.state(GS_OTHER)
    assert other.status == "not_activated" and other.scope_required is False and other.non_participation_determined is True
    assert env.state().non_participation_determined is False


def test_r05_policy_generation_is_monotone_and_distinct_from_epoch(env):
    st10 = select_stmt(REC, epoch=10)
    env.activate(st10)
    g1 = env.state().policy_generation
    w20 = withdraw_stmt(st10, 20)
    env.withdraw(w20)
    g2 = env.state().policy_generation
    env.activate(select_stmt(REC, epoch=30, supersedes=sup(w20)))
    g3 = env.state().policy_generation
    assert (g1, g2, g3) == (1, 2, 3) and env.state().selection_epoch == 30 and g3 != env.state().selection_epoch


# ================================================================================================================== AUDIT LOG
def test_l01_log_is_the_exact_24_member_domain_separated_chain(env):
    env.activate(ST1)
    env.withdraw(withdraw_stmt(ST1, 2))
    env.sync(2, revoked_selections=frozenset({("sel-test-1", 2)}))
    rows = env.sql("SELECT activation_sequence, entry_hash, entry_json FROM selection_log_v1 ORDER BY activation_sequence")
    prev = "sha256:" + "0" * 64
    assert [row[0] for row in rows] == list(range(1, len(rows) + 1)) and len(rows) >= 6
    for seq, h, raw in rows:
        e = json.loads(raw)
        assert tuple(sorted(e)) == tuple(sorted(sa.ENTRY_MEMBERS)) and len(e) == 24
        assert e["prev_entry_hash"] == prev and e["activation_sequence"] == seq
        assert h == "sha256:" + hashlib.sha256(b"ocs-reg/s4/log-entry\n" + c.canonical_bytes(e)).hexdigest()
        assert raw == c.canonical_bytes(e).decode("ascii")
        assert (e["reasons_registry_label"], e["reasons_registry_sha256"]) == (sa.REASONS_LABEL, sa.REASONS_SHA256)
        prev = h


@pytest.mark.parametrize("mutate", [
    lambda e: e.update(selection_id=None),                                          # activate without a selection
    lambda e: e.update(cause="baseline"),                                           # cause invalid for the entry type
])
def test_l02_closed_matrix_refuses_invalid_entries(mutate):
    good = sa.build_entry(sequence=2, prev_entry_hash="sha256:" + "0" * 64, entry_type="activate", cause="none", governed_scope_digest="sha256:" + "1" * 64,
                          scope_key_digest="sha256:" + "2" * 64, selection_id="sel-x", selection_epoch=1, selection_digest="sha256:" + "3" * 64,
                          policy_generation=1, supersedes=None, participation="required", state_before="none", state_after="active",
                          ratchet_after=True, snapshot_version=1, snapshot_fingerprint="sha256:" + "4" * 64, issuer_key_id="key-x",
                          recorded_at="2026-10-25T12:00:00.000000Z")
    assert sa.validate_entry(good) is None
    bad = dict(good)
    mutate(bad)
    assert sa.validate_entry(bad) is not None


def test_l03_reasons_registry_outside_the_allow_list_refuses_and_writes_nothing(tmp_path):
    e = Env(tmp_path, reasons=("runtime-authority-reasons/0.4", sa.REASONS_SHA256))
    refused(lambda: e.dest.sync_selection("baseline", determination_ref=DET), "field_invalid", "REFUSE", "reasons_registry_label")
    e2 = Env(tmp_path / "second", reasons=(sa.REASONS_LABEL, "sha256:" + "9" * 64))
    refused(lambda: e2.dest.sync_selection("baseline", determination_ref=DET), "field_invalid", "REFUSE", "reasons_registry_sha256")
    assert e.sql("SELECT count(*) FROM selection_log_v1") == [(0,)] and e2.sql("SELECT count(*) FROM selection_log_v1") == [(0,)]


def test_l04_history_rows_are_insert_only(env):
    env.activate(ST1)
    env.sync(2, revoked_selections=frozenset({("sel-test-1", 1)}))
    for statement in ("UPDATE selection_log_v1 SET entry_json='{}'", "DELETE FROM selection_log_v1",
                      "DELETE FROM selection_revoked_v1", "UPDATE selection_revoked_v1 SET epoch=9",
                      "DELETE FROM selection_lineage_v1", "UPDATE selection_lineage_v1 SET selection_id='x'",
                      "DELETE FROM selection_scope_required_v1", "DELETE FROM selection_generation_v1",
                      "UPDATE selection_generation_v1 SET policy_generation=0"):
        with pytest.raises(sqlite3.DatabaseError):
            env.sql(statement)
    assert env.verify().state == "known"


# ================================================================================================================== RESTART / RESTORE
def test_s01_restoring_an_older_database_copy_makes_the_store_unknown(env, tmp_path):
    env.activate(ST1)
    older = tmp_path / "older.sqlite3"
    src = sqlite3.connect(env.dest.path)
    dst = sqlite3.connect(older)
    src.backup(dst)
    dst.close()
    src.close()
    env.withdraw(withdraw_stmt(ST1, 2))
    live = sqlite3.connect(env.dest.path, isolation_level=None)
    back = sqlite3.connect(older)
    back.backup(live)
    back.close()
    live.close()
    env.open()
    status = env.verify()
    assert status.state == "unknown" and status.cause.startswith("anchor_ahead")
    refused(lambda: env.state(), "store_unknown", "HOLD")
    refused(lambda: env.activate(select_stmt(REC, epoch=5)), "store_unknown", "HOLD")


def test_s02_missing_anchor_on_a_store_with_history_is_unknown(env):
    env.activate(ST1)
    env.anchor.unlink()
    assert env.verify().state == "unknown" and env.verify().cause.startswith("anchor_absent")
    refused(lambda: env.state(), "store_unknown", "HOLD")


def test_s03_anchored_entry_hash_mismatch_is_unknown(env):
    env.activate(ST1)
    env.rewrite_anchor(head_entry_hash="sha256:" + "7" * 64)
    assert env.verify().state == "unknown" and env.verify().cause.startswith("anchor_hash_mismatch")


def test_s04_store_ahead_of_a_stale_anchor_with_an_unbroken_chain_is_accepted(env, tmp_path):
    env.activate(ST1)
    stale = env.anchor.read_text()
    env.withdraw(withdraw_stmt(ST1, 2))
    env.anchor.write_text(stale)
    assert env.verify().state == "known"
    env.sync(2)
    assert json.loads(env.anchor.read_text())["anchor"]["head_sequence"] == env.head()[0]


def test_s05_genesis_baseline_is_refused_when_an_anchor_exists(env, tmp_path):
    env.activate(ST1)
    lost = Env.__new__(Env)
    lost.root, lost.anchor, lost.now, lost.reasons = tmp_path / "lost", env.anchor, r.NOW, None
    lost.open()
    refused(lambda: lost.dest.sync_selection("baseline", determination_ref=DET), "store_unknown", "HOLD", "genesis_refused_anchor_exists")
    assert lost.verify().state == "unknown"


def test_s06_anchored_digests_that_replay_does_not_reproduce_are_unknown(env):
    env.activate(ST1)
    env.rewrite_anchor(scope_required_digest="sha256:" + "5" * 64)
    assert env.verify().state == "unknown" and env.verify().cause.startswith("anchor_digest_mismatch")


# ================================================================================================================== CORRUPT / UNKNOWN
def test_c01_an_edited_log_entry_breaks_the_chain(env):
    env.activate(ST1)
    env.drop_guards()
    row = env.sql("SELECT entry_json FROM selection_log_v1 WHERE activation_sequence=3")[0][0]
    edited = json.loads(row)
    edited["selection_epoch"] = 99
    env.sql("UPDATE selection_log_v1 SET entry_json=? WHERE activation_sequence=3", (c.canonical_bytes(edited).decode("ascii"),))
    assert env.verify().state == "unknown"
    refused(lambda: env.state(), "store_unknown", "HOLD")


def test_c02_a_sequence_gap_is_unknown(env):
    env.activate(ST1)
    env.drop_guards()
    env.sql("DELETE FROM selection_log_v1 WHERE activation_sequence=2")
    assert env.verify().state == "unknown"


def test_c03_a_state_row_with_no_matching_log_entry_is_unknown(env):
    env.activate(ST1)
    env.withdraw(withdraw_stmt(ST1, 2))
    env.sql("UPDATE selection_activation_v1 SET activation_state='active'")
    status = env.verify()
    assert status.state == "unknown" and status.cause.startswith("table_mismatch")
    refused(lambda: env.state(), "store_unknown", "HOLD")


def test_c04_a_deleted_ratchet_row_is_unknown_never_optional(env):
    env.activate(ST1)
    env.drop_guards()
    env.sql("DELETE FROM selection_scope_required_v1")
    assert env.verify().state == "unknown"
    refused(lambda: env.state(), "store_unknown", "HOLD")


def test_c05_an_entry_with_an_unknown_reasons_label_is_rejected_by_replay(env):
    env.activate(ST1)
    env.drop_guards()
    seq, raw = env.sql("SELECT activation_sequence, entry_json FROM selection_log_v1 ORDER BY activation_sequence DESC LIMIT 1")[0]
    e = json.loads(raw)
    e["reasons_registry_label"] = "runtime-authority-reasons/0.4"
    new_hash = sa.entry_hash_of(e)
    env.sql("UPDATE selection_log_v1 SET entry_json=?, entry_hash=? WHERE activation_sequence=?", (c.canonical_bytes(e).decode("ascii"), new_hash, seq))
    env.rewrite_anchor(head_entry_hash=new_hash)  # isolate the check: only the label itself can now cause the refusal
    status = env.verify()
    assert status.state == "unknown" and "reasons" in status.cause


# ================================================================================================================== V04 REMEDIATION (PR #146)
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("column,value", [
    ("valid_until", "2099-01-01T00:00:00Z"), ("valid_from", "2000-01-01T00:00:00Z"), ("record_id", "f1ab-forged"),
    ("record_digest", "sha256:" + "5" * 64), ("activation_ref", "sha256:" + "6" * 64), ("record_policy_digest", "sha256:" + "7" * 64)])
def test_p01_corrupted_authority_column_is_unknown_before_use(env, column, value):
    env.activate(ST1)
    env.sql(f"UPDATE selection_activation_v1 SET {column}=?", (value,))
    status = env.verify()
    assert status.state == "unknown" and status.cause.startswith("table_mismatch")
    refused(lambda: env.state(), "store_unknown", "HOLD")


def test_p02_corrupted_transition_ref_after_withdraw_is_unknown(env):
    env.activate(ST1)
    env.withdraw(withdraw_stmt(ST1, 2))
    env.sql("UPDATE selection_activation_v1 SET transition_ref=?", ("sha256:" + "8" * 64,))
    assert env.verify().state == "unknown"
    refused(lambda: env.state(), "store_unknown", "HOLD")


def test_p03_intact_history_reads_the_same_verdict_after_restart(env):
    env.activate(ST1)
    env.withdraw(withdraw_stmt(ST1, 2))
    before = env.state()
    env.open()
    after = env.state()
    assert env.verify().state == "known" and after == before and after.status == "withdrawn"
    assert after.valid_until and after.record_id and after.effective_policy_digest == effective_digest()


def _anchor(seq, ev=0, head=None, store="store-x"):
    return {"anchor_version": sa.ANCHOR_VERSION, "store_id": store, "head_sequence": seq, "head_entry_hash": head or "sha256:" + format(seq, "x").zfill(64),
            "evidence_head_sequence": ev, "evidence_head_hash": sa.ZERO_HASH}


def test_a01_anchor_is_monotonic_stale_and_equal_position_deterministic(tmp_path):
    host = sa.HostAnchorFile(tmp_path / "anchor.json")
    assert host.advance(_anchor(1)) == "written" and host.advance(_anchor(3)) == "written"
    raw = (tmp_path / "anchor.json").read_bytes()
    assert host.advance(_anchor(2)) == "stale" and (tmp_path / "anchor.json").read_bytes() == raw  # a stale writer cannot overwrite
    assert host.advance(_anchor(3)) == "unchanged" and (tmp_path / "anchor.json").read_bytes() == raw
    with pytest.raises(sa.ReplayError):  # equal position, different head: conflict, never a silent replace
        host.advance(_anchor(3, head="sha256:" + "9" * 64))
    assert host.advance(_anchor(5, ev=2)) == "written"
    raw = (tmp_path / "anchor.json").read_bytes()
    with pytest.raises(sa.ReplayError):  # log ahead but evidence behind is incomparable: refused, nothing written
        host.advance(_anchor(6, ev=1))
    assert (tmp_path / "anchor.json").read_bytes() == raw


def test_a02_malformed_existing_anchor_is_never_overwritten(tmp_path):
    path = tmp_path / "anchor.json"
    path.write_text("{not json")
    with pytest.raises(sa.ReplayError):
        sa.HostAnchorFile(path).advance(_anchor(1))
    assert path.read_text() == "{not json"
    with pytest.raises(sa.ReplayError):  # absent anchor after genesis is not permission to start over either
        sa.HostAnchorFile(tmp_path / "other.json").advance(_anchor(7))
    assert not (tmp_path / "other.json").exists()


def test_a03_lock_held_by_another_process_refuses_without_writing(tmp_path):
    path = tmp_path / "anchor.json"
    host = sa.HostAnchorFile(path)
    host.advance(_anchor(1))
    raw = path.read_bytes()
    code = textwrap.dedent(f"""
        import fcntl, sys, time
        h = open({str(path) + '.lock'!r}, 'a+b'); fcntl.flock(h.fileno(), fcntl.LOCK_EX)
        print('ready', flush=True); time.sleep(3)
    """)
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        host.LOCK_TIMEOUT_SECONDS = 0.2
        with pytest.raises(sa.ReplayError) as info:
            host.advance(_anchor(2))
        assert "anchor_lock_unavailable" in str(info.value) and path.read_bytes() == raw
    finally:
        child.kill()
        child.wait()


def test_a04_concurrent_processes_cannot_regress_the_anchor(tmp_path):
    path = tmp_path / "anchor.json"
    sa.HostAnchorFile(path).advance(_anchor(1))
    code = textwrap.dedent("""
        import sys; sys.path[:0] = [{root!r}, {src!r}]
        from engine import registry_selection_authority as sa
        host = sa.HostAnchorFile({path!r}); n = int(sys.argv[1])
        for seq in ([n, n + 10, n + 20] if n % 2 else [n + 20, n + 10, n]):
            a = {{"anchor_version": sa.ANCHOR_VERSION, "store_id": "store-x", "head_sequence": seq, "head_entry_hash": "sha256:" + format(seq, "x").zfill(64),
                  "evidence_head_sequence": 0, "evidence_head_hash": sa.ZERO_HASH}}
            host.advance(a)
    """).format(root=str(ROOT), src=str(ROOT / "src"), path=str(path))
    procs = [subprocess.Popen([sys.executable, "-c", code, str(n)]) for n in (2, 3, 4, 5, 6, 7)]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    assert sa.HostAnchorFile(path).read()["head_sequence"] == 27  # the maximum offered; no older head ever landed last


def test_a05_interrupted_write_leaves_the_prior_anchor_intact(tmp_path, monkeypatch):
    path = tmp_path / "anchor.json"
    host = sa.HostAnchorFile(path)
    host.advance(_anchor(1))
    raw = path.read_bytes()
    monkeypatch.setattr(sa.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        host.advance(_anchor(2))
    monkeypatch.undo()
    assert path.read_bytes() == raw and host.read()["head_sequence"] == 1
    assert not [f for f in tmp_path.iterdir() if f.name.startswith(".anchor-")]
