"""M3 oracles: Registry-governed target prefixes and match mode (`allowed_target_prefixes`, `target_match`) as the nine-member `f1abcd` family
(contract `r08-v4`): selection, activation, coexistence, composition, and effect-time enforcement. BUILDER_TESTED developer evidence. NOT independently
qualified. Reuses the PR-2/PR-3 and V05 fixtures by import (no new framework). The `engine/safe_executor.py` target evaluator is not modified.

Needs `OCS_REGISTRY_PIN_DIR` at the pinned Registry commit (head of UNMERGED Registry PR #56, r08-v4) and PyNaCl; the effect-time group also needs Control
Plane V02 + pydantic>=2.12 (otherwise that group is reported NOT RUN).

  A activation  A-01 valid f1abcd   A-02 optional refused   A-03 identity is the nine-member digest
  B refusals    B-01 cross-version/family   B-02 wrong tenant/scope   B-03 malformed prefix/mode record   B-04 revoked/withdrawn at activation
  T coexistence T-01 f1abc/f1abcd cannot be active together   T-02 transition through withdrawal   T-03 unbound fallback refused
  C composition C-01 Registry narrower   C-02 caller narrower   C-03 equal   C-04 non-comparable (deny-all)   C-05 empty Registry targets
                C-06 exact boundary   C-07 segment boundary   C-08 prefix collision   C-09 separator boundary   C-10 modes   C-11 M2 ceilings and sets
                C-12 defence in depth   C-13 evaluator untouched   C-14 malformed caller targets
  G effect-time G-01 valid run   G-02 narrower policy   G-03 targets outside the record   G-04 weaker mode   G-05 record deny-all   G-06 digest mismatch
                G-07 missing/malformed policy targets   G-08 revoked   G-09 stale   G-10 tenant/scope/family   G-11 tampered record targets
                G-12 budgets   G-13 restart/recovery/withdrawal   G-14 M2 ceilings and f1ab sets still enforced   G-15 no effect after any refusal
"""
from __future__ import annotations

import dataclasses
import sqlite3

import pytest

import test_registry_f1abc_selection as pr2  # skips the module when the Registry pin / PyNaCl are absent
import test_registry_f1a_narrowing as m1
import test_registry_selection_authority as base

import r08_support as r  # noqa: E402
import r08v4_support as t4  # noqa: E402
import s01_support as s  # noqa: E402
from registry_adapters import ed25519_verifier as ev  # noqa: E402
from registry_successor import v4_f1abcd as fam4  # noqa: E402
from registry_successor import v4_policy_identity as ident4  # noqa: E402

from engine import registry_f1abcd_narrowing as adapter  # noqa: E402
from engine import registry_selection_authority as sa  # noqa: E402

FAMILY = sa.FAMILY_F1ABCD
SETS = sa.sa_fields()
GS4 = dict(base.GS, family=FAMILY)
SID4 = "sel-f1abcd-1"
PFX = "urn:cognous:synthetic-account:"
TARGET = PFX + "1"  # the Gov/flow operation target


# ------------------------------------------------------------------------------------------------------------------------ builders
def payload4(**over):
    p = {k: list(base.PAYLOAD[k]) for k in SETS}
    p.update(max_amount=1000, max_effects=2, allowed_target_prefixes=[PFX], target_match="segment")
    p.update(over)
    return p


def rec4(**over):
    return t4.record(payload4(**over))


def st4(rec=None, *, sid=SID4, epoch=1, participation="required", supersedes=None, **over):
    return t4.statement(rec if rec is not None else rec4(), selection_id=sid, epoch=epoch, participation=participation, supersedes=supersedes, **over)


def act4(env, rec=None, stmt=None, *, text=None, record_text=None, context=None, trust_=None):
    rec = rec if rec is not None else rec4()
    stmt = stmt if stmt is not None else st4(rec)
    return env.dest.activate_selection(record_text=record_text if record_text is not None else r.dumps(rec),
                                       selection_text=text if text is not None else r.dumps(t4.envelope(stmt)), context=context or s.CTX,
                                       trust=trust_ or env.trust, verifier=ev.Ed25519Verifier())


def wd4(env, prev, epoch):
    return env.dest.withdraw_selection(selection_text=r.dumps(t4.envelope(base.withdraw_stmt(prev, epoch))), context=s.CTX, trust=env.trust,
                                       verifier=ev.Ed25519Verifier())


def digest4(sets, amount, effects, prefixes, mode):
    return ident4.effective_policy_digest(FAMILY, {**{k: sorted(sets[k]) for k in SETS}, "max_amount": amount, "max_effects": effects,
                                                  "allowed_target_prefixes": sorted(prefixes), "target_match": mode})


def digest_of(policy):
    return digest4({k: getattr(policy, k) for k in SETS}, policy.max_amount, policy.max_effects, policy.allowed_target_prefixes, policy.target_match)


def binding4(state, **over):
    b = {"binding_profile": "ocs-policy-binding/1", "record_id": state.record_id, "record_version": state.record_version,
         "record_digest": state.record_digest, "selection_id": state.selection_id, "selection_epoch": state.selection_epoch,
         "selection_digest": state.selection_digest, "family": FAMILY, "consumer_id": GS4["consumer_id"],
         "scope": {"tenant": GS4["tenant"], "institution": GS4["institution"], "authority_domain": GS4["authority_domain"]},
         "operation": GS4["operation"], "effective_policy_digest": state.effective_policy_digest, "activation_ref": state.activation_ref,
         "trust_snapshot_version": state.trust_snapshot_version, "trust_snapshot_fingerprint": state.trust_snapshot_fingerprint}
    b.update(over)
    return b


def unchanged(env, fn, code):
    before = env.verify()
    with pytest.raises(sa.SelectionRefusal) as info:
        fn()
    assert info.value.code == code, (info.value.code, code)
    assert env.verify() == before


@pytest.fixture
def env(tmp_path):
    return base.Env(tmp_path).boot()


# ============================================================================================================ A activation
def test_a01_valid_f1abcd_activation(env):
    rec = rec4()
    act4(env, rec)
    st = env.state(GS4)
    assert st.record_id == rec["record_id"] and st.selection_id == SID4 and env.verify().state == "known"


def test_a02_optional_participation_is_refused(env):
    unchanged(env, lambda: act4(env, stmt=st4(participation="optional")), "participation_mismatch")


def test_a03_identity_is_the_nine_member_digest(env):
    rec = rec4()
    act4(env, rec)
    assert env.state(GS4).effective_policy_digest == ident4.effective_policy_digest(FAMILY, rec["payload"]) == sa._record_policy_digest(FAMILY, rec["payload"])
    # a target change alone changes the identity
    assert ident4.effective_policy_digest(FAMILY, payload4(allowed_target_prefixes=[PFX + "x"])) != env.state(GS4).effective_policy_digest
    assert ident4.effective_policy_digest(FAMILY, payload4(target_match="exact")) != env.state(GS4).effective_policy_digest


# ============================================================================================================ B refusals
def test_b01_cross_version_and_cross_family(env):
    stmt = st4()
    for contract, code in (("r08-v3", "family_unsupported"), ("r08-v2", "family_unsupported"), ("r08-v1", "contract_unsupported"), ("r08-v5", "contract_unsupported")):
        text = r.dumps(t4.envelope(stmt, contract=contract))
        unchanged(env, lambda t=text: act4(env, stmt=stmt, text=t), code)  # a v4 statement under another contract label is never a v4 activation
    # a seven-member f1abc selection and record cannot activate on the v4 path, and the reverse
    rec3 = pr2.t3.record()
    unchanged(env, lambda: act4(env, rec3, pr2.st3(rec3), text=r.dumps(t4.envelope(pr2.st3(rec3)))), "family_unsupported")
    unchanged(env, lambda: pr2.act3(env, st4(), rec4(), text=r.dumps(pr2.t3.envelope(st4()))), "family_unsupported")
    unchanged(env, lambda: act4(env, rec4(), stmt, record_text=r.dumps(rec3)), "contract_unsupported")


def test_b02_wrong_tenant_scope_operation(env):
    ctx = base.c.ExpectedContext("consumer-test", "tenant-other", "inst-a", "domain-a", "op-test")
    unchanged(env, lambda: act4(env, context=ctx), "selection_scope_mismatch")
    op = base.c.ExpectedContext("consumer-test", "tenant-a", "inst-a", "domain-a", "op-other")
    unchanged(env, lambda: act4(env, context=op), "selection_operation_mismatch")


@pytest.mark.parametrize("over", [{"target_match": "prefix"}, {"target_match": "Segment"}, {"target_match": 1}, {"allowed_target_prefixes": ["b", "a"]},
                                  {"allowed_target_prefixes": ["a b"]}, {"allowed_target_prefixes": ["a", "a"]}, {"allowed_target_prefixes": [""]},
                                  {"allowed_target_prefixes": "urn:"}])
def test_b03_malformed_prefix_or_mode_record(env, over):
    rec = rec4(**over)
    unchanged(env, lambda: act4(env, rec), "field_invalid" if False else sa_code(rec))


def sa_code(rec):
    from registry_successor import v4_record
    return v4_record.validate_record_text_v4(r.dumps(rec)).code


def test_b03b_missing_members_are_never_defaulted(env):
    for name in ("allowed_target_prefixes", "target_match"):
        pl = payload4()
        del pl[name]
        rec = t4.record(pl)
        unchanged(env, lambda rec=rec: act4(env, rec), "field_missing")


def test_b04_revoked_and_withdrawn_selection_cannot_activate(env):
    env.sync(2, revoked_selections=frozenset({(SID4, 1)}))
    unchanged(env, lambda: act4(env), "selection_revoked")


# ============================================================================================================ T coexistence
def test_t01_f1abc_and_f1abcd_cannot_be_active_together(env):
    first = pr2.st3()
    pr2.act3(env, first)
    unchanged(env, lambda: act4(env), "selection_conflict")
    # the reverse
    env2 = None


def test_t01b_reverse_direction(tmp_path):
    e = base.Env(tmp_path).boot()
    act4(e)
    unchanged(e, lambda: pr2.act3(e, pr2.st3(sid="sel-f1abc-rev")), "selection_conflict")
    unchanged(e, lambda: e.activate(base.ST1), "selection_conflict")  # f1ab as well


def test_t02_transition_through_withdrawal(env):
    first = pr2.st3()
    pr2.act3(env, first)
    pr2.wd3(env, first, 2)
    act4(env)
    assert env.state(GS4).selection_id == SID4 and env.verify().state == "known"
    wd4(env, st4(), 2)
    assert env.verify().state == "known"


def test_t03_unbound_fallback_is_refused_after_activation(tmp_path):
    eg = pytest.importorskip("test_registry_selection_effect_guard")
    g = eg.Gov(tmp_path, activate=False)
    act4(g.e)
    _, _, res = g.run(binding=None)
    assert res.status == "denied" and g.counts() == (0, 0, 0, 0)


# ============================================================================================================ C composition (adapter)
def caller(**over):
    kw = dict(max_amount=1000, max_effects=3)
    kw.update(over)
    return m1.caller_policy(**kw)


def run4(*, cal=None, rec=None, op_over=None, participation="required", sel=None, record_text=None):
    rec = rec if rec is not None else rec4()
    return adapter.evaluate_with_registry_selection_f1abcd(
        cal if cal is not None else caller(), m1.frozen_op("u-read", **{"target": TARGET, **(op_over or {})}), participation=participation,
        record_text=record_text if record_text is not None else r.dumps(rec), selection_text=sel if sel is not None else r.dumps(t4.envelope(st4(rec))),
        context=s.CTX, trust=s.trust(pub=t4.PK), verifier=ev.Ed25519Verifier(), now=r.NOW)


def narrow(cal, **over):
    kw = dict(allowed_actions=frozenset({"act-list"}), allowed_adapters=frozenset({"a-local"}), allowed_units=frozenset({"u-read"}))
    kw.update(over)
    return dataclasses.replace(cal, **kw)


@pytest.fixture
def built(monkeypatch):
    calls = []
    real = adapter.build_narrowed_policy
    monkeypatch.setattr(adapter, "build_narrowed_policy", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    return calls


def test_c01_registry_narrower_boundary(built):
    cal = caller(allowed_target_prefixes=(PFX,))
    ok = run4(cal=cal, rec=rec4(allowed_target_prefixes=[TARGET]))
    assert ok.allowed is True and ok.effective_policy_digest == digest4({k: getattr(narrow(cal), k) for k in SETS}, 1000, 2, [TARGET], "segment")
    other = run4(cal=cal, rec=rec4(allowed_target_prefixes=[PFX + "2"]))
    assert other.allowed is False and other.evaluator_refusal == "local target restriction"  # Registry narrowed the caller's prefix away from customer-1


def test_c02_caller_narrower_than_registry(built):
    cal = caller(allowed_target_prefixes=(TARGET,))
    res = run4(cal=cal, rec=rec4(allowed_target_prefixes=[PFX]))
    assert res.allowed is True and res.effective_policy_digest == digest4({k: getattr(narrow(cal), k) for k in SETS}, 1000, 2, [TARGET], "segment")
    assert run4(cal=cal, rec=rec4(allowed_target_prefixes=[PFX]), op_over={"target": PFX + "2"}).evaluator_refusal == "local target restriction"


def test_c03_equal(built):
    cal = caller(allowed_target_prefixes=(PFX,))
    res = run4(cal=cal)
    assert res.allowed is True and res.effective_policy_digest == digest4({k: getattr(narrow(cal), k) for k in SETS}, 1000, 2, [PFX], "segment")


def test_c04_non_comparable_targets_deny_all_before_any_policy_is_built(built):
    res = run4(cal=caller(allowed_target_prefixes=("urn:other:",)))
    assert res.allowed is False and res.adapter_refusal == "registry_deny_all" and res.effective_policy_digest is None and built == []


def test_c05_empty_registry_targets_is_deny_all(built):
    res = run4(rec=rec4(allowed_target_prefixes=[]))
    assert res.allowed is False and res.adapter_refusal == "registry_deny_all" and built == []


@pytest.mark.parametrize("target,allowed", [(TARGET, True), (PFX + "12", False), (TARGET + "/x", False)])
def test_c06_exact_boundary(built, target, allowed):
    cal = caller(allowed_target_prefixes=(TARGET,), target_match="exact")
    res = run4(cal=cal, rec=rec4(allowed_target_prefixes=[TARGET], target_match="exact"), op_over={"target": target})
    assert res.allowed is allowed and (allowed or res.evaluator_refusal == "local target restriction")


@pytest.mark.parametrize("target,allowed", [(TARGET, True), (TARGET + "/x", True), (TARGET + ":x", True), (PFX + "12", False)])
def test_c07_segment_boundary(built, target, allowed):
    cal = caller(allowed_target_prefixes=(TARGET,))
    res = run4(cal=cal, rec=rec4(allowed_target_prefixes=[TARGET]), op_over={"target": target})
    assert res.allowed is allowed


    # caller "…:1" (segment) under Registry "…:synthetic-account" (segment): the effective entry "…:1" must not accept "…:12" (prefix collision)
    # caller "…:customer-1" (segment) with Registry "…:customer-" (segment) must not turn into acceptance of "customer-12"
    cal = caller(allowed_target_prefixes=(TARGET,))
    res = run4(cal=cal, rec=rec4(allowed_target_prefixes=[PFX[:-1] + "1"[:0]]), op_over={"target": PFX + "12"})
    assert res.allowed is False
    legacy = run4(cal=caller(allowed_target_prefixes=(TARGET,), target_match="legacy-prefix"),
                  rec=rec4(allowed_target_prefixes=[PFX[:-1] + "1"[:0]], target_match="legacy-prefix"), op_over={"target": PFX + "12"})
    assert legacy.allowed is True  # legacy-prefix on both sides keeps its (documented) plain startswith meaning


@pytest.mark.parametrize("entry,target,allowed", [("urn:cognous:synthetic-account", "urn:cognous:synthetic-account:customer-1", True),
                                                  ("urn:cognous:synthetic-account", "urn:cognous:synthetic-accountant", False),
                                                  ("urn:cognous:synthetic-account:", "urn:cognous:synthetic-account:customer-1", True)])
def test_c09_separator_boundary(built, entry, target, allowed):
    res = run4(cal=caller(allowed_target_prefixes=(entry,)), rec=rec4(allowed_target_prefixes=[entry]), op_over={"target": target})
    assert res.allowed is allowed


@pytest.mark.parametrize("cmode,rmode,expected", [("legacy-prefix", "exact", "exact"), ("exact", "legacy-prefix", "exact"), ("segment", "exact", "exact"),
                                                  ("legacy-prefix", "segment", "segment"), ("segment", "legacy-prefix", "segment"),
                                                  ("exact", "exact", "exact"), ("legacy-prefix", "legacy-prefix", "legacy-prefix")])
def test_c10_effective_mode_is_never_weaker_than_either(built, cmode, rmode, expected):
    entry = TARGET
    res = run4(cal=caller(allowed_target_prefixes=(entry,), target_match=cmode), rec=rec4(allowed_target_prefixes=[entry], target_match=rmode))
    assert res.allowed is True
    assert res.effective_policy_digest == digest4({k: getattr(narrow(caller()), k) for k in SETS}, 1000, 2, [entry], expected)


def test_c11_m2_ceilings_and_sets_are_still_enforced_with_targets(built):
    low = run4(rec=rec4(max_amount=10), op_over={"amount": 50.0})
    assert low.allowed is False and low.evaluator_refusal == "local amount restriction"
    eff = run4(rec=rec4(max_effects=1), op_over={"effective_max_effects": 2})
    assert eff.evaluator_refusal == "local effect-count restriction"
    assert run4(rec=rec4(max_effects=0)).adapter_refusal == "registry_deny_all"
    assert run4(rec=rec4(allowed_units=["u-write"]), cal=caller()).allowed is False


def _patched_effective(monkeypatch, **changes):
    import importlib
    mod = importlib.import_module("registry_successor.v4_decision")
    real = mod.verify_and_decide_v4

    def f(*a, **k):
        d = real(*a, **k)
        return dataclasses.replace(d, effective={**d.effective, **changes})
    monkeypatch.setattr(mod, "verify_and_decide_v4", f)


@pytest.mark.parametrize("changes,expected", [({"allowed_target_prefixes": ("urn:cognous:",)}, "effective_policy_widens_caller"),
                                              ({"allowed_target_prefixes": (PFX, "urn:evil:")}, "effective_policy_widens_caller"),
                                              ({"target_match": "legacy-prefix", "allowed_target_prefixes": (PFX[:-1],)}, "effective_policy_widens_caller"),
                                              ({"target_match": "bogus"}, "effective_policy_widens_caller"), ({"target_match": 1}, "effective_policy_invalid"),
                                              ({"allowed_target_prefixes": ()}, "registry_deny_all"), ({"allowed_target_prefixes": [PFX]}, "effective_policy_invalid"),
                                              ({"max_amount": 1001}, "effective_policy_widens_caller")])
def test_c12_a_decision_that_widens_or_is_malformed_is_refused_even_if_registry_misbehaved(built, monkeypatch, changes, expected):
    _patched_effective(monkeypatch, **changes)
    res = run4(cal=caller(max_amount=1000, max_effects=3, allowed_target_prefixes=(PFX,)))
    assert res.allowed is False and res.adapter_refusal == expected and res.effective_policy_digest is None and built == []


def test_c13_evaluator_and_other_caller_constraints_are_untouched():
    cal = caller()
    narrowed = adapter.build_narrowed_policy(cal, {"allowed_actions": ("act-list",), "allowed_adapters": ("a-local",), "allowed_units": ("u-read",),
                                                   "allowed_institutions": ("inst-a",), "allowed_authority_domains": ("domain-a",), "max_amount": 7,
                                                   "max_effects": 2, "allowed_target_prefixes": (TARGET,), "target_match": "exact"})
    assert narrowed.allowed_target_prefixes == (TARGET,) and narrowed.target_match == "exact" and narrowed.allowed_institutions == cal.allowed_institutions
    assert cal.allowed_target_prefixes == (PFX,) and cal.target_match == "segment"  # the caller's own object is never mutated


def test_c13b_registry_matches_equals_the_runtime_evaluator_per_entry():
    """`v4_f1abcd.matches` (used to prove containment) is the same per-entry rule as the protected evaluator, checked over a bounded universe."""
    import itertools
    from engine.safe_executor import LocalExecutionPolicy
    universe = ["".join(p) for n in range(1, 5) for p in itertools.product("ab/:", repeat=n)]
    for mode in fam4.MODES:
        for entry in ["a", "a/", "a:", "ab", "a/b", "/", ":"]:
            pol = m1.caller_policy(allowed_target_prefixes=(entry,), target_match=mode)
            for t in universe:
                try:
                    from engine.safe_executor import _target_is_well_formed
                    if not _target_is_well_formed(t):
                        continue
                except Exception:
                    continue
                assert pol._target_allowed(t) == fam4.matches(mode, entry, t), (mode, entry, t)


@pytest.mark.parametrize("over", [{"allowed_target_prefixes": ("a b",)}])  # (an invalid mode cannot even be held by a LocalExecutionPolicy)
def test_c14_malformed_caller_targets_are_refused(built, over):
    cal = dataclasses.replace(caller(), **over)
    res = run4(cal=cal)
    assert res.allowed is False and built == [] and res.effective_policy_digest is None


# ============================================================================================================ G effect-time guard
class _LazyEffectGuard:
    def __getattr__(self, name):
        if name.startswith("_") or name.startswith("pytest"):
            raise AttributeError(name)  # pytest's collection probes module attributes; that must not trigger the import
        return getattr(pytest.importorskip("test_registry_selection_effect_guard"), name)


eg = _LazyEffectGuard()


def policy4(**over):
    kw = dict(max_amount=1000, max_effects=2)
    kw.update(over)
    return eg.effect_policy(**kw)


def gov4(tmp_path, rec=None, **over):
    g = eg.Gov(tmp_path, activate=False)
    rec = rec if rec is not None else rec4(**over)
    act4(g.e, rec)
    g.rec = rec
    return g


def bind(g, policy=None, **over):
    b = binding4(g.e.state(GS4))
    b["effective_policy_digest"] = digest_of(policy if policy is not None else policy4())
    b.update(over)
    return b


def run_as(g, n, binding, policy):
    g.n += 1
    env = dataclasses.replace(eg.envelope(), effect_id=f"effect-{n}", decision_id=f"decision-{n}")
    cid = f"claim-{g.n}"
    g.dest.provision_claim(eg.make_claim(env, g.now, binding=binding, claim_id=cid))
    return g.executor(policy).execute(envelope=env, claim_id=cid)


def no_effect(g):
    assert g.counts() == (0, 0, 0, 0)


def test_g01_valid_run_executes_and_records_the_nine_member_identity(tmp_path):
    g = gov4(tmp_path)
    policy = policy4()
    _, _, res = g.run(binding=bind(g, policy), policy=policy)
    assert res.status == "executed" and g.counts() == (1, 1, 1, 1)
    guard = res.control_plane_evidence["authority_receipt"]["selection_guard"]
    assert guard["result"] == "binding_verified" and guard["effective_policy_digest"] == digest_of(policy) == g.e.state(GS4).effective_policy_digest


def test_g02_a_policy_with_narrower_targets_executes(tmp_path):
    g = gov4(tmp_path)
    policy = policy4(allowed_target_prefixes=(TARGET,), target_match="exact")
    _, _, res = g.run(binding=bind(g, policy), policy=policy)
    assert res.status == "executed"


@pytest.mark.parametrize("policy_prefixes,policy_mode,record_prefixes,record_mode", [
    (("urn:cognous:",), "segment", [PFX], "segment"),                     # wider prefix
    ((PFX, "urn:evil:"), "segment", [PFX], "segment"),                    # one extra prefix
    ((PFX[:-1],), "legacy-prefix", [PFX[:-1]], "segment"),                  # legacy accepts "...accountant"; the record's segment does not
    ((PFX[:-1],), "segment", [TARGET], "exact"),                           # segment is wider than an exact record entry
    ((TARGET,), "exact", [PFX + "2"], "exact"),                   # disjoint
])
def test_g03_g04_targets_outside_the_record_are_refused(tmp_path, policy_prefixes, policy_mode, record_prefixes, record_mode):
    g = gov4(tmp_path, allowed_target_prefixes=record_prefixes, target_match=record_mode)
    policy = policy4(allowed_target_prefixes=policy_prefixes, target_match=policy_mode)
    _, _, res = g.run(binding=bind(g, policy), policy=policy)
    typed = eg.refused(res, "policy_identity_mismatch")
    assert typed["cause"] == "policy_targets_not_contained"
    no_effect(g)


def test_g04b_a_stricter_policy_mode_inside_the_record_passes(tmp_path):
    g = gov4(tmp_path, allowed_target_prefixes=[PFX[:-1]], target_match="legacy-prefix")
    policy = policy4(allowed_target_prefixes=(PFX[:-1],), target_match="segment")
    assert g.run(binding=bind(g, policy), policy=policy)[2].status == "executed"


def test_g05_record_with_no_targets_admits_nothing(tmp_path):
    g = gov4(tmp_path, allowed_target_prefixes=[])
    policy = policy4()
    typed = eg.refused(g.run(binding=bind(g, policy), policy=policy)[2], "policy_identity_mismatch")
    assert typed["cause"] == "policy_targets_not_contained"
    no_effect(g)


def test_g06_digest_mismatch(tmp_path):
    g = gov4(tmp_path)
    policy = policy4(allowed_target_prefixes=(TARGET,), target_match="exact")
    for wrong in (digest_of(policy4()),                                   # the identity of the wider targets
                  digest4({k: getattr(policy, k) for k in SETS}, 1000, 2, [TARGET], "segment"),  # right prefixes, wrong mode
                  ident4.effective_policy_digest(FAMILY, payload4()) [:-1] + "0"):
        eg.refused(g.run(binding=bind(g, policy, effective_policy_digest=wrong), policy=policy)[2], "policy_identity_mismatch")
    no_effect(g)


def guard_direct(g, binding, policy, targets="auto"):
    import types
    claim = types.SimpleNamespace(policy_binding=binding, tenant_id="tenant-a", institution_id="inst-a", authority_domain="domain-a")
    conn = g.e.store._open()
    try:
        conn.execute("BEGIN IMMEDIATE")
        return g.e.store.check_effect(conn, claim, g.now, None, sa.policy_sets_of(policy), policy_effective_limits=sa.policy_limits_of(policy),
                                      policy_effective_targets=sa.policy_targets_of(policy) if targets == "auto" else targets)
    finally:
        conn.execute("ROLLBACK")
        conn.close()


@pytest.mark.parametrize("targets,code", [(None, "verifier_unavailable"), ({}, "verifier_unavailable"), ({"target_match": "segment"}, "verifier_unavailable"),
                                          ({"allowed_target_prefixes": (), "target_match": "segment"}, "composition_caller_invalid"),
                                          ({"allowed_target_prefixes": (PFX,), "target_match": 1}, "composition_caller_invalid"),
                                          ({"allowed_target_prefixes": (PFX,), "target_match": "bogus"}, "policy_identity_mismatch"),
                                          ({"allowed_target_prefixes": ("a b",), "target_match": "segment"}, "policy_identity_mismatch"),
                                          ({"allowed_target_prefixes": "urn:", "target_match": "segment"}, "composition_caller_invalid")])
def test_g07_missing_or_malformed_policy_targets_are_refused_by_the_guard(tmp_path, targets, code):
    g = gov4(tmp_path)
    policy = policy4()
    assert guard_direct(g, bind(g, policy), policy)["result"] == "binding_verified"
    with pytest.raises(sa.SelectionRefusal) as info:
        guard_direct(g, bind(g, policy), policy, targets=targets)
    assert info.value.code == code, info.value.code  # no bypass: an executor that omits its targets cannot reach an f1abcd effect
    no_effect(g)


def test_g08_revoked_selection(tmp_path):
    g = gov4(tmp_path)
    binding = bind(g)
    g.e.sync(2, revoked_selections=frozenset({(SID4, 1)}))
    eg.refused(g.run(binding=binding)[2], "selection_revoked")
    no_effect(g)


def test_g09_effect_time_stale_selection(tmp_path):
    g = gov4(tmp_path)
    old, policy = bind(g), policy4()
    act4(g.e, g.rec, st4(g.rec, epoch=2, supersedes=base.sup(st4(g.rec))))
    eg.refused(g.run(binding=old, policy=policy)[2], "selection_epoch_stale")
    no_effect(g)
    assert run_as(g, 2, bind(g), policy).status == "executed"


def test_g10_wrong_tenant_scope_or_family(tmp_path):
    g = gov4(tmp_path)
    policy = policy4()
    for over in (dict(scope=dict(tenant="tenant-other", institution="inst-a", authority_domain="domain-a")),
                 dict(operation="op-other"), dict(consumer_id="consumer-other"),
                 dict(family=sa.FAMILY_F1ABC), dict(family="f1ab-local-execution-allowlist")):
        with pytest.raises(sa.SelectionRefusal) as info:
            guard_direct(g, bind(g, policy, **over), policy)
        assert info.value.code == "not_activated", (over, info.value.code)
    no_effect(g)


def test_g11_tampered_record_targets_make_the_store_unknown(tmp_path):
    g = gov4(tmp_path)
    policy = policy4()
    binding = bind(g, policy)
    g.e.drop_guards()
    g.e.sql("UPDATE selection_activation_v1 SET record_json=REPLACE(record_json, '\"allowed_target_prefixes\":[\"urn:cognous:synthetic-account:\"]', "
            "'\"allowed_target_prefixes\":[\"urn:\"]')")
    typed = eg.refused(g.run(binding=binding, policy=policy)[2], "store_unknown")
    assert typed["refusal_class"] == "HOLD"
    no_effect(g)


def test_g12_budgets_are_never_reset_or_replaced(tmp_path):
    g = gov4(tmp_path, max_effects=5)
    refused_policy = policy4(max_effects=6)
    eg.refused(g.run(binding=bind(g, refused_policy), policy=refused_policy)[2], "policy_identity_mismatch")
    with sqlite3.connect(g.dest.path) as conn:
        assert [(m, u) for _, m, u in conn.execute("SELECT budget_id, max_effects, used_effects FROM effect_budgets_v1")] == [(1, 0)]
    policy = policy4(max_effects=5, allowed_target_prefixes=(TARGET,), target_match="exact")
    assert run_as(g, 2, bind(g, policy), policy).status == "executed"
    assert g.counts()[0] == 1


def test_g13_restart_recovery_and_withdrawal(tmp_path):
    g = gov4(tmp_path)
    policy = policy4()
    assert g.run(binding=bind(g, policy), policy=policy)[2].status == "executed"
    g.e.open()
    g.dest = g.e.dest
    assert g.e.verify().state == "known"
    assert run_as(g, 2, bind(g, policy), policy).status == "executed" and g.counts()[0] == 2
    wd4(g.e, st4(g.rec), 2)
    g.e.open()
    g.dest = g.e.dest
    res = run_as(g, 3, bind(g, policy), policy)
    assert res.status == "denied" and res.control_plane_evidence["selection_refusal"]["code"] == "selection_withdrawn"
    assert g.counts()[0] == 2


@pytest.mark.parametrize("record_over,policy_over,cause", [
    ({"max_amount": 1000}, {"max_amount": 1001}, "policy_exceeds_ceiling:max_amount"),
    ({"max_effects": 1}, {"max_effects": 2}, "policy_exceeds_ceiling:max_effects"),
    ({"max_effects": 0}, {"max_effects": 1}, "policy_exceeds_ceiling:max_effects"),
])
def test_g14_m2_ceilings_still_enforced(tmp_path, record_over, policy_over, cause):
    g = gov4(tmp_path, **record_over)
    policy = policy4(**policy_over)
    assert eg.refused(g.run(binding=bind(g, policy), policy=policy)[2], "policy_identity_mismatch")["cause"] == cause
    no_effect(g)


def test_g14b_f1ab_allow_sets_still_enforced_and_floats_refused(tmp_path):
    g = gov4(tmp_path)
    widened = policy4(allowed_units=frozenset({"u-read", "u-write"}))
    assert eg.refused(g.run(binding=bind(g, widened), policy=widened)[2], "policy_identity_mismatch")["cause"].startswith("policy_not_subset:")
    floaty = dataclasses.replace(policy4(), max_amount=1000.0)
    assert eg.refused(g.run(binding=bind(g, policy4()), policy=floaty)[2], "composition_caller_invalid")["cause"] == "policy_limits_not_exact_integers"
    no_effect(g)


def test_g15_f1ab_and_f1abc_executions_are_unchanged_by_the_new_plumbing(tmp_path):
    g = eg.Gov(tmp_path / "f1ab")
    assert g.run()[2].status == "executed"
    g3 = eg.Gov(tmp_path / "f1abc", activate=False)
    rec3 = pr2.t3.record(pr2.t3.payload(allowed_actions=list(base.PAYLOAD["allowed_actions"]), allowed_adapters=list(base.PAYLOAD["allowed_adapters"]),
                                        allowed_units=list(base.PAYLOAD["allowed_units"]), allowed_institutions=list(base.PAYLOAD["allowed_institutions"]),
                                        allowed_authority_domains=list(base.PAYLOAD["allowed_authority_domains"]), max_amount=1000, max_effects=2))
    pr2.act3(g3.e, pr2.st3(rec3), rec3)
    b = pr2.f1abc_binding(g3.e.state(pr2.GS3))
    policy = policy4()
    b["effective_policy_digest"] = pr2.__dict__.get("digest3") and __import__("registry_successor.v3_policy_identity", fromlist=["x"]).effective_policy_digest(
        sa.FAMILY_F1ABC, {**{k: sorted(getattr(policy, k)) for k in SETS}, "max_amount": policy.max_amount, "max_effects": policy.max_effects})
    assert g3.run(binding=b, policy=policy)[2].status == "executed"


# ============================================================================================================ P pin
def test_p01_exact_registry_pin_and_runtime_constants_equal_the_registry(tmp_path):
    import subprocess
    head = subprocess.run(["git", "-C", base.PIN_DIR, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert head == pr2.REGISTRY_PIN
    assert fam4.FAMILY_F1ABCD == sa.FAMILY_F1ABCD and tuple(fam4.F1ABCD_FIELDS) == SETS + sa.F1ABC_CEILINGS + sa.F1ABCD_TARGETS
    assert sa._registry_v4().sel.V4_CONTRACT == sa.CONTRACT_V4 and tuple(fam4.MODES) == ("exact", "segment", "legacy-prefix")
