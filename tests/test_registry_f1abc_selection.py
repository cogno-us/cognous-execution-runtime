"""M2 PR-2 oracles: governed selection, activation and atomic family transition for the composite Registry family `f1abc` (contract `r08-v3`).

BUILDER_TESTED developer evidence. NOT independently qualified. Selection, activation and transition handling. (PR-2 refused every f1abc effect;
M2 PR-3 replaced that refusal with effect-time enforcement of all seven members: see `test_registry_f1abc_enforcement.py`.)

Environment: `OCS_REGISTRY_PIN_DIR` names a checkout of cogno-us/cognous-ocs-registry at the exact merged r08-v3 commit `REGISTRY_PIN` below, and PyNaCl
is installed; without them the module is reported NOT RUN (skipped), never passed. The effect-denial tests also need Control Plane V02 + pydantic>=2.12.

  A activation   A-01 valid f1abc activation   A-02 optional participation refused   A-03 digest tamper makes the store unknown
  B refusals     B-01 wrong family   B-02 wrong contract version   B-03 wrong tenant/scope   B-04 invalid signature   B-05 stale epoch/generation
                 B-06 revoked selection   B-07 withdrawn selection   B-08 invalid record payload
  T transition   T-01 f1ab -> f1abc   T-02 f1abc -> f1ab   T-03 simultaneous active families refused, store unchanged   T-04 unbound fallback refused
                 T-05 unbound fallback in the transition gap
  R restart      R-01 restart with an existing activation   R-02 restart with a withdrawn activation
  E effect       E-03 f1ab unchanged next to a withdrawn f1abc
  P pin          P-01 exact Registry pin, v3 modules from the pin   P-02 Runtime constants equal the Registry's
"""
from __future__ import annotations

import subprocess
import types

import pytest

import test_registry_selection_authority as v4  # skips the module when the Registry pin / PyNaCl are absent

import r08_support as r  # noqa: E402  (Registry test builders on the pinned checkout's tests dir, put on sys.path by v4)
import r08v3_support as t3  # noqa: E402
import s01_support as s  # noqa: E402
from registry_adapters import ed25519_verifier as ev  # noqa: E402
from registry_successor import common as c  # noqa: E402

from engine import registry_selection_authority as sa  # noqa: E402

REGISTRY_PIN = "b7e6a173877c731273858b01b26214147dd0736c"  # M3: merge commit of cogno-us/cognous-ocs-registry PR #56 (r08-v4); includes the merged r08-v3 (PR #54, ba98dd26)
FAMILY = sa.FAMILY_F1ABC
GS3 = dict(v4.GS, family=FAMILY)
SID3 = "sel-f1abc-1"
REC3 = t3.record()
REC3_DIGEST_PAYLOAD = t3.payload()


def st3(rec=None, *, sid=SID3, epoch=1, participation="required", supersedes=None, **over):
    return t3.statement(rec if rec is not None else REC3, selection_id=sid, epoch=epoch, participation=participation, supersedes=supersedes, **over)


def act3(env, stmt=None, rec=None, *, text=None, record_text=None, trust_=None, context=None):
    rec = rec if rec is not None else REC3
    stmt = stmt if stmt is not None else st3(rec)
    return env.dest.activate_selection(record_text=record_text if record_text is not None else r.dumps(rec),
                                       selection_text=text if text is not None else r.dumps(t3.envelope(stmt)),
                                       context=context or s.CTX, trust=trust_ or env.trust, verifier=ev.Ed25519Verifier())


def wd3(env, prev, epoch, *, trust_=None):
    stmt = v4.withdraw_stmt(prev, epoch)
    return env.dest.withdraw_selection(selection_text=r.dumps(t3.envelope(stmt)), context=s.CTX, trust=trust_ or env.trust, verifier=ev.Ed25519Verifier())


def digest3():
    return __import__("registry_successor.v3_policy_identity", fromlist=["x"]).effective_policy_digest(FAMILY, REC3["payload"])


refused = v4.refused


@pytest.fixture
def env(tmp_path):
    return v4.Env(tmp_path).boot()


def unchanged(env, call, code, cause=None):
    before = env.dump()
    err = refused(call, code, cause=cause)
    assert env.dump() == before  # a refusal writes nothing: no log entry, no row
    return err


# ================================================================================================================== A activation
def test_a01_valid_f1abc_activation_binds_the_seven_member_identity(env):
    res = act3(env)
    st = env.state(GS3)
    assert st.status == "active" and st.selection_id == SID3 and st.participation == "required" and st.scope_required is True
    assert st.activation_ref == res.activation_ref == env.head()[1] and st.policy_generation == 1
    assert st.effective_policy_digest == digest3() and st.record_digest == REC3["record_digest"]
    assert env.verify().state == "known"
    # the digest covers the two ceilings: a different ceiling is a different identity
    other = __import__("registry_successor.v3_policy_identity", fromlist=["x"]).effective_policy_digest(FAMILY, dict(REC3["payload"], max_effects=4))
    assert other != digest3() != v4.effective_digest()


def test_a02_f1abc_requires_required_participation(env):
    unchanged(env, lambda: act3(env, st3(participation="optional")), "participation_mismatch", "f1abc_requires_required_participation")


def test_a03_tampered_composite_policy_digest_makes_the_store_unknown(env):
    act3(env)
    env.drop_guards()
    env.sql("UPDATE selection_activation_v1 SET record_policy_digest=?", ("sha256:" + "0" * 64,))
    status = env.verify()
    assert status.state == "unknown" and status.cause.startswith("table_mismatch")


def test_a04_five_set_digest_is_not_accepted_for_an_f1abc_row(env):
    act3(env)
    env.drop_guards()
    env.sql("UPDATE selection_activation_v1 SET record_policy_digest=?", (v4.effective_digest(),))  # the f1ab-shaped identity of the same sets
    assert env.verify().state == "unknown"


# ================================================================================================================== B refusals
def test_b01_wrong_family(env):
    stmt = st3()
    stmt["family"] = v4.f1ab.FAMILY_F1AB
    unchanged(env, lambda: act3(env, stmt), "family_unsupported")
    v2_with_f1abc = v4.select_stmt(v4.REC)
    v2_with_f1abc["family"] = FAMILY
    unchanged(env, lambda: env.activate(v2_with_f1abc), "family_unsupported")
    f1ab_record = v4.reg_record()
    unchanged(env, lambda: act3(env, record_text=r.dumps(f1ab_record)), "contract_unsupported")  # an f1ab record never activates an f1abc selection


@pytest.mark.parametrize("contract,code", [("r08-v2", "family_unsupported"), ("r08-v1", "contract_unsupported"), ("r08-v5", "contract_unsupported"), ("r08-v4", "family_unsupported"),
                                           ("", "contract_unsupported")])
def test_b02_wrong_contract_version(env, contract, code):
    stmt = st3()
    text = r.dumps(t3.envelope(stmt, contract=contract))  # an r08-v2 label is routed to the v2 path, which does not know the f1abc family; r08-v4 (M3) routes to the v4 path, which does not know f1abc
    unchanged(env, lambda: act3(env, stmt, text=text), code)


def test_b02b_a_v3_envelope_never_activates_on_the_v2_path_and_the_reverse(env):
    unchanged(env, lambda: env.dest.activate_selection(record_text=r.dumps(v4.REC), selection_text=r.dumps(t3.envelope(st3())), context=s.CTX,
                                                       trust=env.trust, verifier=ev.Ed25519Verifier()), "contract_unsupported")
    unchanged(env, lambda: act3(env, text=v4.text(v4.ST1)), "contract_unsupported")  # a v2 f1ab envelope cannot reach the v3 path


def test_b03_wrong_tenant_or_scope(env):
    ctx = c.ExpectedContext("consumer-test", "tenant-other", "inst-a", "domain-a", "op-test")
    unchanged(env, lambda: act3(env, context=ctx), "selection_scope_mismatch")
    op = c.ExpectedContext("consumer-test", "tenant-a", "inst-a", "domain-a", "op-other")
    unchanged(env, lambda: act3(env, context=op), "selection_operation_mismatch")
    stmt = st3()
    stmt["scope"] = dict(stmt["scope"], tenant="tenant-other")
    unchanged(env, lambda: act3(env, stmt), "issuer_not_authorized")  # no signer holds a grant for that tenant


def test_b04_invalid_signature(env):
    stmt = st3()
    unchanged(env, lambda: act3(env, stmt, text=r.dumps(t3.envelope(stmt, sk=t3.OTHER_SK))), "signature_invalid")
    unchanged(env, lambda: act3(env, stmt, text=r.dumps(t3.envelope(stmt, domain=t3.V2_DOMAIN))), "signature_invalid")  # signed over the v2 domain


def test_b05_stale_generation_and_epoch(env):
    first = st3()
    act3(env, first)
    act3(env, st3(epoch=2, supersedes=v4.sup(first)))
    assert env.state(GS3).selection_epoch == 2 and env.state(GS3).policy_generation == 2
    unchanged(env, lambda: act3(env, first), "selection_epoch_stale")
    unchanged(env, lambda: act3(env, st3(epoch=2, supersedes=v4.sup(first))), "selection_epoch_stale")


def test_b06_revoked_selection(env):
    stmt = st3()
    act3(env, stmt)
    env.sync(2, revoked_selections=frozenset({(SID3, 1)}))
    assert env.state(GS3).status == "revoked"
    unchanged(env, lambda: act3(env, stmt), "selection_revoked")
    act3(env, st3(epoch=2, supersedes=v4.sup(stmt)))  # a fresh higher epoch is new authority (V04 W-04); the revoked pair itself stays dead
    assert env.state(GS3).status == "active" and env.state(GS3).selection_epoch == 2 and env.verify().state == "known"
    unchanged(env, lambda: act3(env, stmt), "selection_revoked")


def test_b07_withdrawn_selection(env):
    first = st3()
    act3(env, first)
    wd3(env, first, 2)
    assert env.state(GS3).status == "withdrawn"
    unchanged(env, lambda: act3(env, first), "selection_epoch_stale")  # the old epoch cannot restore authority
    unchanged(env, lambda: wd3(env, first, 3), "selection_withdrawn")  # nor can a withdrawn row be withdrawn again


@pytest.mark.parametrize("over", [{"max_effects": 2 ** 53}, {"max_amount": 2 ** 53 + 1}, {"max_effects": -1}, {"max_amount": 1.5}, {"max_effects": True}])
def test_b08_invalid_record_payload(env, over):
    bad = t3.record(t3.payload(**over))
    err = None
    with pytest.raises(sa.SelectionRefusal) as info:
        act3(env, st3(bad), bad)
    err = info.value
    assert err.code in ("field_range", "field_type_invalid", "field_invalid", "field_value_invalid", "record_invalid") or err.cause == "record", (err.code, err.cause)
    assert env.verify().state == "known" and env.state(GS3).status == "not_activated"


# ================================================================================================================== T transition
def test_t01_f1ab_to_f1abc_requires_withdrawal_first(env):
    env.activate(v4.ST1)
    unchanged(env, lambda: act3(env), "selection_conflict", "family_coexistence")
    wd = v4.withdraw_stmt(v4.ST1, 2)
    env.withdraw(wd)
    assert env.state().status == "withdrawn" and env.verify().state == "known"
    act3(env)
    assert env.state(GS3).status == "active" and env.state().status == "withdrawn"
    assert env.verify().state == "known"


def test_t02_f1abc_to_f1ab_requires_withdrawal_first(env):
    first = st3()
    act3(env, first)
    unchanged(env, lambda: env.activate(v4.ST1), "selection_conflict", "family_coexistence")
    wd3(env, first, 2)
    env.activate(v4.ST1)
    assert env.state().status == "active" and env.state(GS3).status == "withdrawn" and env.verify().state == "known"


def test_t03_simultaneous_active_families_cannot_be_forced(env):
    env.activate(v4.ST1)
    before = env.dump()
    for _ in range(2):
        refused(lambda: act3(env), "selection_conflict", cause="family_coexistence")
    assert env.dump() == before and env.state().status == "active" and env.state(GS3).status == "not_activated"
    # a store that nevertheless shows two active families for one governed tuple is not a known store
    wd = v4.withdraw_stmt(v4.ST1, 2)
    env.withdraw(wd)
    act3(env)
    env.drop_guards()
    env.sql("UPDATE selection_activation_v1 SET activation_state='active', transition_ref=NULL WHERE scope_json LIKE '%f1ab-local-execution-allowlist%'")
    assert env.verify().state == "unknown"


def _unbound_claim():
    return types.SimpleNamespace(policy_binding=None, tenant_id="tenant-a", institution_id="inst-a", authority_domain="domain-a")


def _check_unbound(env):
    conn = env.store._open()
    try:
        conn.execute("BEGIN IMMEDIATE")
        return env.store.check_effect(conn, _unbound_claim(), env.now, None)
    finally:
        conn.execute("ROLLBACK")
        conn.close()


def test_t04_unbound_claim_cannot_recover_governed_authority(env):
    first = st3()
    act3(env, first)
    refused(lambda: _check_unbound(env), "selection_missing", cause="scope_required_without_binding")
    wd3(env, first, 2)
    refused(lambda: _check_unbound(env), "selection_missing", cause="scope_required_without_binding")  # the ratchet outlives the withdrawal


def test_t05_transition_gap_fails_closed_for_a_required_scope(env):
    env.activate(v4.ST1)  # required f1ab
    env.withdraw(v4.withdraw_stmt(v4.ST1, 2))
    refused(lambda: _check_unbound(env), "selection_missing", cause="scope_required_without_binding")  # gap between withdraw and f1abc activation
    act3(env)
    refused(lambda: _check_unbound(env), "selection_missing", cause="scope_required_without_binding")


# ================================================================================================================== R restart
def test_r01_restart_with_an_existing_activation(env):
    act3(env)
    before = env.state(GS3)
    env.open()  # a new destination object over the same database and anchor
    assert env.verify().state == "known" and env.state(GS3) == before
    unchanged(env, lambda: env.activate(v4.ST1), "selection_conflict", "family_coexistence")  # the coexistence rule survives the restart


def test_r02_restart_with_a_withdrawn_activation(env):
    first = st3()
    act3(env, first)
    wd3(env, first, 2)
    before = env.state(GS3)
    env.open()
    assert env.verify().state == "known" and env.state(GS3) == before and before.status == "withdrawn"
    env.activate(v4.ST1)  # the withdrawn family no longer blocks f1ab
    assert env.state().status == "active"


# ================================================================================================================== E effect


def f1abc_binding(state):
    return {"binding_profile": "ocs-policy-binding/1", "record_id": state.record_id, "record_version": state.record_version,
            "record_digest": state.record_digest, "selection_id": state.selection_id, "selection_epoch": state.selection_epoch,
            "selection_digest": state.selection_digest, "family": FAMILY, "consumer_id": GS3["consumer_id"],
            "scope": {"tenant": GS3["tenant"], "institution": GS3["institution"], "authority_domain": GS3["authority_domain"]},
            "operation": GS3["operation"], "effective_policy_digest": state.effective_policy_digest, "activation_ref": state.activation_ref,
            "trust_snapshot_version": state.trust_snapshot_version, "trust_snapshot_fingerprint": state.trust_snapshot_fingerprint}


def test_e03_f1ab_behaviour_is_unchanged_next_to_a_withdrawn_f1abc(tmp_path):
    eg = pytest.importorskip("test_registry_selection_effect_guard")
    g = eg.Gov(tmp_path, activate=False)
    first = st3()
    act3(g.e, first)
    wd3(g.e, first, 2)
    g.e.activate(g.stmt)  # f1ab, required, after the f1abc withdrawal
    _, _, result = g.run()
    assert result.status == "executed" and g.counts()[0] == 1
    guard = result.control_plane_evidence["authority_receipt"]["selection_guard"]
    assert guard["result"] == "binding_verified" and guard["scope_required"] is True


# ================================================================================================================== P pin
def test_p01_registry_dependency_is_the_exact_merged_commit():
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=v4.PIN_DIR, capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=v4.PIN_DIR, capture_output=True, text=True, check=True).stdout
    assert head == REGISTRY_PIN and dirty == ""
    regv = sa._registry_v3()
    for mod in (regv.sel, regv.verify, regv.record, regv.ident, regv.fam):
        assert str(__import__("pathlib").Path(mod.__file__).resolve()).startswith(v4.PIN_DIR), mod.__file__


def test_p02_runtime_constants_equal_the_registry_and_routing_is_exact():
    regv = sa._registry_v3()
    assert sa.FAMILY_F1ABC == regv.fam.FAMILY_F1ABC and sa.CONTRACT_V3 == regv.sel.V3_CONTRACT
    assert sa.sa_fields() + sa.F1ABC_CEILINGS == tuple(regv.fam.F1ABC_FIELDS)
    assert sa._declares_v3(r.dumps(t3.envelope(st3()))) is True
    for text in (v4.text(v4.ST1), "not json", "[]", "null", r.dumps(t3.envelope(st3(), contract="r08-v2")), '{"contract_version": ["r08-v3"]}'):
        assert sa._declares_v3(text) is False


def test_p03_replay_invariant_detects_two_active_families_for_one_tuple():
    base = ["c", "t", "i", "d", "o"]
    f1ab, f1abc = sa.canonical_bytes(base + [v4.f1ab.FAMILY_F1AB]).decode(), sa.canonical_bytes(base + [FAMILY]).decode()
    other = sa.canonical_bytes(["c", "t", "i", "d", "o2", FAMILY]).decode()
    assert sa._coexistence_violated([f1ab, f1abc]) is True and sa._coexistence_violated([f1abc, f1ab]) is True
    assert sa._coexistence_violated([f1ab, other]) is False and sa._coexistence_violated([f1abc]) is False and sa._coexistence_violated([]) is False
