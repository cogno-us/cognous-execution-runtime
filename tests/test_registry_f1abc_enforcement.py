"""M2 PR-3 oracles: Registry-governed numeric ceilings (`max_amount`, `max_effects`) and composite (seven-member) effect-time enforcement for the
`f1abc` family (contract `r08-v3`). BUILDER_TESTED developer evidence. NOT independently qualified.

Reuses the PR-2 and V05 fixtures by import (no new framework). Needs `OCS_REGISTRY_PIN_DIR` at the pinned Registry commit and PyNaCl; the effect-time
group also needs Control Plane V02 + pydantic>=2.12 (otherwise that group is reported NOT RUN).

  C composition (adapter)  C-01 Registry below caller   C-02 equal   C-03 above (caller wins)   C-04 numeric boundaries   C-05 max_amount 0
                           C-06 max_effects 0 deny-all  C-07 malformed numerics   C-08 caller float/bool refused   C-09 five-set intersections
                           C-10 the evaluator and target rules are untouched
  G effect-time guard      G-01 valid run executes   G-02 policy below record   G-03 policy above ceiling (x2) refused   G-04 record deny-all
                           G-05 boundaries   G-06 float/malformed policy ceilings   G-07 composite digest mismatch   G-08 revoked   G-09 stale
                           G-10 wrong tenant/scope/family   G-11 tampered record ceilings   G-12 budget preservation   G-13 restart/recovery
                           G-14 f1ab regression (floats and sets unchanged)
"""
from __future__ import annotations

import dataclasses
import sqlite3

import pytest

import test_registry_f1abc_selection as pr2  # skips the module when the Registry pin / PyNaCl are absent
import test_registry_f1a_narrowing as m1
import test_registry_selection_authority as v4

import r08_support as r  # noqa: E402
import r08v3_support as t3  # noqa: E402
import s01_support as s  # noqa: E402
from registry_adapters import ed25519_verifier as ev  # noqa: E402
from registry_successor import v3_policy_identity as ident3  # noqa: E402

from engine import registry_f1abc_narrowing as adapter  # noqa: E402
from engine import registry_selection_authority as sa  # noqa: E402

FAMILY = sa.FAMILY_F1ABC
SETS = sa.sa_fields()


# ============================================================================================================ C composition (adapter)
def caller(**over):
    kw = dict(max_amount=100, max_effects=3)
    kw.update(over)
    return m1.caller_policy(**kw)


def rec7(**over):
    return t3.record(t3.payload(**over))


def sel7(rec):
    return r.dumps(t3.envelope(t3.statement(rec)))


def run7(*, cal=None, rec=None, op=None, op_over=None, unit="u-read", participation="required", sel=None, record_text=None):
    rec = rec if rec is not None else rec7()
    return adapter.evaluate_with_registry_selection_f1abc(
        cal if cal is not None else caller(), op if op is not None else m1.frozen_op(unit, **(op_over or {})), participation=participation,
        record_text=record_text if record_text is not None else r.dumps(rec), selection_text=sel if sel is not None else sel7(rec),
        context=s.CTX, trust=s.trust(pub=t3.PK), verifier=ev.Ed25519Verifier(), now=r.NOW)


def digest7(cal, **ceil):
    sets = {k: sorted(getattr(cal, k)) for k in SETS}
    return ident3.effective_policy_digest(FAMILY, {**sets, **ceil})


@pytest.fixture
def built(monkeypatch):
    calls = []
    real = adapter.build_narrowed_policy
    monkeypatch.setattr(adapter, "build_narrowed_policy", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    return calls


def refused_no_policy(res, built, adapter_refusal=None, registry_code=None):
    assert res.allowed is False and res.evaluator_refusal is None and res.effective_policy_digest is None and res.non_authorizing is True
    if adapter_refusal:
        assert res.adapter_refusal == adapter_refusal, res
    if registry_code:
        assert res.registry_code == registry_code, res
    assert built == []  # no executable policy object was constructed


def narrowed_sets_caller(**over):
    """A caller whose sets equal the Registry payload's, so the ceilings are the only difference."""
    kw = dict(allowed_actions=frozenset({"act-copy", "act-list"}), allowed_adapters=frozenset({"a-local"}), allowed_units=frozenset({"u-read"}))
    kw.update(over)
    return caller(**kw)


def test_c01_registry_ceilings_below_the_caller_win(built):
    cal, rec = caller(max_amount=1000, max_effects=5), rec7(max_amount=40, max_effects=3)
    ok = run7(cal=cal, rec=rec, op_over={"amount": 30.0})
    assert ok.allowed is True and built == [1] and ok.effective_policy_digest == digest7(narrowed_sets_caller(), max_amount=40, max_effects=3)
    over = run7(cal=cal, rec=rec, op_over={"amount": 50.0})  # below the caller's 1000, above Registry's 40
    assert over.allowed is False and over.evaluator_refusal == "local amount restriction"
    eff = run7(cal=cal, rec=rec, op_over={"amount": 30.0, "effective_max_effects": 4})  # below the caller's 5, above Registry's 3
    assert eff.allowed is False and eff.evaluator_refusal == "local effect-count restriction"


def test_c02_registry_ceilings_equal_to_the_caller(built):
    cal, rec = caller(max_amount=100, max_effects=3), rec7(max_amount=100, max_effects=3)
    res = run7(cal=cal, rec=rec, op_over={"amount": 100.0, "effective_max_effects": 3})
    assert res.allowed is True and res.effective_policy_digest == digest7(narrowed_sets_caller(), max_amount=100, max_effects=3)
    assert run7(cal=cal, rec=rec, op_over={"amount": 100.5}).evaluator_refusal == "local amount restriction"


def test_c03_registry_ceilings_above_the_caller_never_raise_it(built):
    cal, rec = caller(max_amount=100, max_effects=2), rec7(max_amount=10 ** 6, max_effects=1000)
    res = run7(cal=cal, rec=rec, op_over={"amount": 90.0, "effective_max_effects": 2})
    assert res.allowed is True and res.effective_policy_digest == digest7(narrowed_sets_caller(), max_amount=100, max_effects=2)  # min(caller, Registry)
    assert run7(cal=cal, rec=rec, op_over={"amount": 150.0}).evaluator_refusal == "local amount restriction"
    assert run7(cal=cal, rec=rec, op_over={"effective_max_effects": 3}).evaluator_refusal == "local effect-count restriction"


def test_c04_numeric_boundaries(built):
    top, eff_top = 2 ** 53, 2 ** 53 - 1
    res = run7(cal=caller(max_amount=top, max_effects=eff_top), rec=rec7(max_amount=top, max_effects=eff_top), op_over={"amount": float(top)})
    assert res.allowed is True  # binary64 comparison: float(2**53) is not greater than the exactly equal ceiling
    assert res.effective_policy_digest == digest7(narrowed_sets_caller(), max_amount=top, max_effects=eff_top)
    one_over = run7(cal=caller(max_amount=top, max_effects=eff_top), rec=rec7(max_amount=top, max_effects=eff_top), op_over={"amount": float(top) * 2})
    assert one_over.evaluator_refusal == "local amount restriction"
    built.clear()
    for bad in ({"max_amount": top + 1}, {"max_effects": top}):  # one past each Registry range
        refused_no_policy(run7(rec=rec7(**bad)), built, "registry_not_applied")
    one = run7(cal=caller(max_amount=0, max_effects=1), rec=rec7(max_amount=0, max_effects=1), op_over={"amount": 0.0})
    assert one.allowed is True and one.effective_policy_digest == digest7(narrowed_sets_caller(), max_amount=0, max_effects=1)  # smallest valid pair


def test_c05_max_amount_zero_is_a_valid_ceiling(built):
    rec = rec7(max_amount=0, max_effects=3)
    assert run7(rec=rec, op_over={"amount": 0.0}).allowed is True
    res = run7(rec=rec, op_over={"amount": 0.01})
    assert res.allowed is False and res.evaluator_refusal == "local amount restriction" and res.effective_policy_digest  # a policy WAS built and decided


def test_c06_max_effects_zero_is_deny_all_before_any_policy_is_built(built):
    refused_no_policy(run7(rec=rec7(max_effects=0)), built, "registry_deny_all")


@pytest.mark.parametrize("over", [{"max_amount": 1.5}, {"max_amount": True}, {"max_amount": "5"}, {"max_amount": -1}, {"max_effects": -1}, {"max_effects": 2.0},
                                  {"max_effects": False}, {"max_effects": None}, {"max_amount": 2 ** 53 + 1}])
def test_c07_malformed_registry_numerics_are_refused(built, over):
    refused_no_policy(run7(rec=rec7(**over)), built, "registry_not_applied")


@pytest.mark.parametrize("over,expected", [({"max_amount": 100.0}, "caller_float_unsupported"), ({"max_effects": 3.0}, "caller_float_unsupported"),
                                           ({"max_amount": True}, "caller_ceiling_invalid"), ({"max_effects": True}, "caller_ceiling_invalid"),
                                           ({"max_amount": -1}, "caller_ceiling_invalid"), ({"max_effects": 0}, "caller_ceiling_invalid")])
def test_c08_caller_floats_and_malformed_ceilings_are_refused_before_registry_runs(built, over, expected):
    # (a policy that cannot hold the value is built through dataclasses.replace, which does not validate numbers)
    cal = dataclasses.replace(caller(), **over)
    refused_no_policy(run7(cal=cal), built, expected)


@pytest.mark.parametrize("field,caller_over,payload_over,op_over,message", [
    ("allowed_actions", {}, {"allowed_actions": ["act-list"]}, {"action_id": "act-copy", "requested_permissions": ("act-copy",)}, "local action restriction"),
    ("allowed_adapters", {"allowed_adapters": frozenset({"a-local", "a-other"})}, {"allowed_adapters": ["a-local"]}, {"adapter_id": "a-other"}, "local adapter restriction"),
    ("allowed_units", {"allowed_units": frozenset({"u-read", "u-write"})}, {"allowed_units": ["u-read"]}, {"unit": "u-write"}, "local unit restriction"),
    ("allowed_institutions", {"allowed_institutions": frozenset({"inst-a", "inst-b"})}, {"allowed_institutions": ["inst-a"]}, {"institution_id": "inst-b"},
     "local institution restriction"),
    ("allowed_authority_domains", {"allowed_authority_domains": frozenset({"domain-a", "domain-b"})}, {"allowed_authority_domains": ["domain-a"]},
     {"authority_domain": "domain-b"}, "local authority-domain restriction"),
])
def test_c09_each_of_the_five_sets_is_intersected(built, field, caller_over, payload_over, op_over, message):
    cal = caller(**{"allowed_actions": frozenset({"act-copy", "act-list"}), **caller_over})
    rec = rec7(**payload_over)
    refusal = run7(cal=cal, rec=rec, op_over={k: v for k, v in op_over.items() if k != "unit"}, unit=op_over.get("unit", "u-read"))
    assert refusal.allowed is False and refusal.evaluator_refusal == message
    # a Registry member the caller lacks never widens the caller
    wider = rec7(**{field: sorted(set(rec["payload"][field]) | {"x-extra"})})
    res = run7(cal=cal, rec=wider, op_over={})
    assert res.allowed is True
    assert run7(cal=cal, rec=wider, op_over={k: v for k, v in op_over.items() if k != "unit"}, unit=op_over.get("unit", "u-read")).allowed is False


def test_c10_other_caller_constraints_and_the_evaluator_are_untouched():
    cal = caller(allowed_target_prefixes=("urn:cognous:synthetic-account:customer-1",), target_match="exact")
    assert run7(cal=cal).allowed is True
    assert run7(cal=cal, op_over={"target": "urn:cognous:synthetic-account:customer-1/x"}).evaluator_refusal == "local target restriction"
    narrowed = adapter.build_narrowed_policy(cal, {**{k: ("act-list",) for k in ("allowed_actions",)}, "allowed_adapters": ("a-local",), "allowed_units": ("u-read",),
                                                   "allowed_institutions": ("inst-a",), "allowed_authority_domains": ("domain-a",), "max_amount": 7, "max_effects": 2})
    assert narrowed.allowed_target_prefixes == cal.allowed_target_prefixes and narrowed.target_match == "exact" and (narrowed.max_amount, narrowed.max_effects) == (7, 2)
    assert cal.max_amount == 100 and cal.max_effects == 3  # the caller's own object is never mutated
    assert run7(participation="optional").allowed is False  # an optional Registry non-application never falls back to the caller here (selection is required)


def _patched_effective(monkeypatch, **changes):
    import importlib
    mod = importlib.import_module("registry_successor.v3_decision")
    real = mod.verify_and_decide_v3

    def f(*a, **k):
        d = real(*a, **k)
        return dataclasses.replace(d, effective={**d.effective, **changes})
    monkeypatch.setattr(mod, "verify_and_decide_v3", f)


@pytest.mark.parametrize("changes,expected", [({"max_amount": 101}, "effective_policy_widens_caller"), ({"max_effects": 4}, "effective_policy_widens_caller"),
                                              ({"allowed_units": ("u-read", "u-write", "u-x")}, "effective_policy_widens_caller"),
                                              ({"max_effects": 0}, "registry_deny_all"), ({"max_amount": 1.5}, "effective_policy_invalid"),
                                              ({"max_effects": True}, "effective_policy_invalid"), ({"allowed_units": ()}, "registry_deny_all")])
def test_c11_a_decision_that_widens_or_is_malformed_is_refused_even_if_registry_misbehaved(built, monkeypatch, changes, expected):
    """Defence in depth behind Registry's own composition: the adapter re-checks the effective policy it is handed."""
    _patched_effective(monkeypatch, **changes)
    refused_no_policy(run7(cal=caller(max_amount=100, max_effects=3), rec=rec7(max_amount=100, max_effects=3)), built, expected)


# ============================================================================================================ G effect-time guard
class _LazyEffectGuard:
    """The V05 effect-guard fixtures, imported on first use so that only the effect-time tests (not the composition tests) are skipped (NOT RUN)
    when Control Plane V02 + pydantic>=2.12 are absent."""

    def __getattr__(self, name):
        if name.startswith("_") or name.startswith("pytest"):
            raise AttributeError(name)  # pytest's collection probes module attributes; that must not trigger the import
        return getattr(pytest.importorskip("test_registry_selection_effect_guard"), name)


eg = _LazyEffectGuard()


def policy7(**over):
    kw = dict(max_amount=1000, max_effects=2)
    kw.update(over)
    return eg.effect_policy(**kw)


def digest_of(policy):
    return ident3.effective_policy_digest(FAMILY, {**{k: sorted(getattr(policy, k)) for k in SETS}, "max_amount": policy.max_amount, "max_effects": policy.max_effects})


def record_g(**over):
    return t3.record(t3.payload(allowed_actions=list(v4.PAYLOAD["allowed_actions"]), allowed_adapters=list(v4.PAYLOAD["allowed_adapters"]),
                                allowed_units=list(v4.PAYLOAD["allowed_units"]), allowed_institutions=list(v4.PAYLOAD["allowed_institutions"]),
                                allowed_authority_domains=list(v4.PAYLOAD["allowed_authority_domains"]), **{"max_amount": 1000, "max_effects": 2, **over}))


def gov_f1abc(tmp_path, rec=None, **over):
    g = eg.Gov(tmp_path, activate=False)
    rec = rec if rec is not None else record_g(**over)
    pr2.act3(g.e, pr2.st3(rec), rec)
    g.rec = rec
    return g


def binding7(g, policy=None, **over):
    b = pr2.f1abc_binding(g.e.state(pr2.GS3))
    b["effective_policy_digest"] = digest_of(policy if policy is not None else policy7())
    b.update(over)
    return b


def budget_rows_short(g):
    with sqlite3.connect(g.dest.path) as conn:
        return conn.execute("SELECT budget_id, max_effects, used_effects FROM effect_budgets_v1 ORDER BY budget_id").fetchall()


def run_as(g, n, binding, policy):
    """A further, distinct effect (effect-n) through the same governed destination."""
    g.n += 1
    env = dataclasses.replace(eg.envelope(), effect_id=f"effect-{n}", decision_id=f"decision-{n}")
    cid = f"claim-{g.n}"
    g.dest.provision_claim(eg.make_claim(env, g.now, binding=binding, claim_id=cid))
    return g.executor(policy).execute(envelope=env, claim_id=cid)


def no_effect(g):
    assert g.counts() == (0, 0, 0, 0)  # no effect, no attempt, no receipt, no budget consumed


def budget_rows(g):
    with sqlite3.connect(g.dest.path) as conn:
        return conn.execute("SELECT * FROM effect_budgets_v1 ORDER BY budget_id").fetchall()


def test_g01_valid_run_executes_and_records_the_composite_identity(tmp_path):
    g = gov_f1abc(tmp_path)
    policy = policy7()
    _, _, res = g.run(binding=binding7(g, policy), policy=policy)
    assert res.status == "executed" and g.counts() == (1, 1, 1, 1)
    guard = res.control_plane_evidence["authority_receipt"]["selection_guard"]
    assert guard["result"] == "binding_verified" and guard["effective_policy_digest"] == digest_of(policy) == g.e.state(pr2.GS3).effective_policy_digest


def test_g02_a_policy_narrower_than_the_record_executes(tmp_path):
    g = gov_f1abc(tmp_path)
    policy = policy7(max_amount=100, max_effects=1)
    _, _, res = g.run(binding=binding7(g, policy), policy=policy)
    assert res.status == "executed" and res.control_plane_evidence["authority_receipt"]["selection_guard"]["effective_policy_digest"] == digest_of(policy)


@pytest.mark.parametrize("record_over,policy_over,cause", [
    ({"max_amount": 1000}, {"max_amount": 1001}, "policy_exceeds_ceiling:max_amount"),
    ({"max_effects": 1}, {"max_effects": 2}, "policy_exceeds_ceiling:max_effects"),
    ({"max_effects": 0}, {"max_effects": 1}, "policy_exceeds_ceiling:max_effects"),   # record deny-all admits no policy
])
def test_g03_a_policy_above_a_registry_ceiling_is_refused(tmp_path, record_over, policy_over, cause):
    g = gov_f1abc(tmp_path, **record_over)
    policy = policy7(**policy_over)
    _, _, res = g.run(binding=binding7(g, policy), policy=policy)
    typed = eg.refused(res, "policy_identity_mismatch")
    assert typed["cause"] == cause
    no_effect(g)


def test_g04_record_max_effects_zero_refuses_every_run(tmp_path):
    g = gov_f1abc(tmp_path, max_effects=0)
    for n in (1, 2):
        policy = policy7(max_effects=n)
        eg.refused(g.run(binding=binding7(g, policy), policy=policy)[2], "policy_identity_mismatch")
    no_effect(g)


def test_g05_boundaries_pass_the_guard(tmp_path):
    g = gov_f1abc(tmp_path, max_amount=2 ** 53, max_effects=2 ** 53 - 1)
    policy = policy7(max_amount=2 ** 53, max_effects=2 ** 53 - 1)
    assert g.run(binding=binding7(g, policy), policy=policy)[2].status == "executed"
    z = gov_f1abc(tmp_path / "z", max_amount=0, max_effects=1)
    zp = policy7(max_amount=0, max_effects=1)
    _, _, res = z.run(binding=binding7(z, zp), policy=zp)
    assert res.status == "denied" and res.error == "local amount restriction"  # the existing evaluator refuses amount 50 under a 0 ceiling, pre-dispatch
    no_effect(z)


@pytest.mark.parametrize("policy_over", [{"max_amount": 1000.0}, {"max_effects": 2.0}, {"max_amount": float("inf")}, {"max_amount": -1}, {"max_effects": 0},
                                         {"max_amount": 2 ** 53 + 1}, {"max_effects": True}])
def test_g06_float_or_malformed_policy_ceilings_never_execute(tmp_path, policy_over):
    g = gov_f1abc(tmp_path)
    policy = dataclasses.replace(policy7(), **policy_over)
    _, _, res = g.run(binding=binding7(g, policy7()), policy=policy)
    assert res.status == "denied" and res.attempted is False
    no_effect(g)


def test_g06b_a_float_ceiling_is_refused_by_the_guard_itself(tmp_path):
    g = gov_f1abc(tmp_path)
    policy = policy7(max_amount=1000.0)  # a valid f1ab-style policy: the evaluator would accept it
    _, _, res = g.run(binding=binding7(g, policy7()), policy=policy)
    assert eg.refused(res, "composition_caller_invalid")["cause"] == "policy_limits_not_exact_integers"
    no_effect(g)


def test_g07_composite_digest_mismatch(tmp_path):
    g = gov_f1abc(tmp_path)
    policy = policy7(max_amount=500)
    for wrong in (digest_of(policy7()),                               # the digest of a different ceiling
                  v4.effective_digest(),                              # the five-set (f1ab-shaped) identity of the same sets
                  g.e.state(pr2.GS3).effective_policy_digest[:-1] + "0"):
        eg.refused(g.run(binding=binding7(g, policy, effective_policy_digest=wrong), policy=policy)[2], "policy_identity_mismatch")
    no_effect(g)


def test_g08_revoked_selection(tmp_path):
    g = gov_f1abc(tmp_path)
    binding = binding7(g)
    g.e.sync(2, revoked_selections=frozenset({(pr2.SID3, 1)}))
    eg.refused(g.run(binding=binding)[2], "selection_revoked")
    no_effect(g)


def test_g09_effect_time_stale_selection(tmp_path):
    g = gov_f1abc(tmp_path)
    old, policy = binding7(g), policy7()
    pr2.act3(g.e, pr2.st3(g.rec, epoch=2, supersedes=v4.sup(pr2.st3(g.rec))), g.rec)
    eg.refused(g.run(binding=old, policy=policy)[2], "selection_epoch_stale")
    no_effect(g)
    assert run_as(g, 2, binding7(g), policy).status == "executed"  # the current activation still executes


def guard_direct(g, binding, policy):
    """The store's lock-time guard on a forged binding, bypassing claim provisioning (which already refuses a binding that disagrees with its claim)."""
    import types
    claim = types.SimpleNamespace(policy_binding=binding, tenant_id="tenant-a", institution_id="inst-a", authority_domain="domain-a")
    conn = g.e.store._open()
    try:
        conn.execute("BEGIN IMMEDIATE")
        return g.e.store.check_effect(conn, claim, g.now, None, sa_sets(policy), policy_effective_limits=sa.policy_limits_of(policy))
    finally:
        conn.execute("ROLLBACK")
        conn.close()


def sa_sets(policy):
    return sa.policy_sets_of(policy)


def test_g10_wrong_tenant_scope_or_family(tmp_path):
    g = gov_f1abc(tmp_path)
    policy = policy7()
    assert guard_direct(g, binding7(g, policy), policy)["result"] == "binding_verified"
    for over in (dict(scope=dict(tenant="tenant-other", institution="inst-a", authority_domain="domain-a")),
                 dict(scope=dict(tenant="tenant-a", institution="inst-other", authority_domain="domain-a")),
                 dict(scope=dict(tenant="tenant-a", institution="inst-a", authority_domain="domain-other")),
                 dict(operation="op-other"), dict(consumer_id="consumer-other"),
                 dict(family="f1ab-local-execution-allowlist")):  # the family is part of the governed scope
        with pytest.raises(sa.SelectionRefusal) as info:
            guard_direct(g, binding7(g, policy, **over), policy)
        assert info.value.code == "not_activated", (over, info.value.code)
    # and a claim whose binding disagrees with its own scope is refused before it can exist
    with pytest.raises(Exception):
        g.claim(binding=binding7(g, policy, scope=dict(tenant="tenant-other", institution="inst-a", authority_domain="domain-a")))
    no_effect(g)


def test_g11_tampered_record_ceilings_make_the_store_unknown(tmp_path):
    g = gov_f1abc(tmp_path, max_amount=10)
    policy = policy7(max_amount=5000)
    binding = binding7(g, policy)
    g.e.drop_guards()
    g.e.sql("UPDATE selection_activation_v1 SET record_json=REPLACE(record_json, '\"max_amount\":10,', '\"max_amount\":10000,')")
    res = g.run(binding=binding, policy=policy)[2]
    typed = eg.refused(res, "store_unknown")
    assert typed["refusal_class"] == "HOLD"
    no_effect(g)


def test_g12_registry_configuration_never_resets_or_replaces_a_grant_budget(tmp_path):
    g = gov_f1abc(tmp_path, max_effects=5)
    refused_policy = policy7(max_effects=6)
    eg.refused(g.run(binding=binding7(g, refused_policy), policy=refused_policy)[2], "policy_identity_mismatch")
    assert [(m, u) for _, m, u in budget_rows_short(g)] == [(1, 0)]  # a refusal consumes nothing; the budget is the Control Plane's (1), not Registry's (5)
    no_effect(g)
    policy = policy7(max_effects=5)
    assert run_as(g, 2, binding7(g, policy), policy).status == "executed"
    rows = budget_rows_short(g)
    assert sorted((m, u) for _, m, u in rows) == [(1, 0), (1, 1)]  # exactly one effect consumed; neither budget was reset, replaced or raised to 5
    assert g.counts()[0] == 1


def test_g13_restart_and_recovery(tmp_path):
    g = gov_f1abc(tmp_path)
    policy = policy7()
    assert g.run(binding=binding7(g, policy), policy=policy)[2].status == "executed"
    g.e.open()  # a new destination over the same database and anchor
    g.dest = g.e.dest
    assert g.e.verify().state == "known"
    assert run_as(g, 2, binding7(g, policy), policy).status == "executed" and g.counts()[0] == 2
    pr2.wd3(g.e, pr2.st3(g.rec), 2)
    g.e.open()
    g.dest = g.e.dest
    typed = run_as(g, 3, binding7(g, policy), policy)
    assert typed.status == "denied" and typed.control_plane_evidence["selection_refusal"]["code"] == "selection_withdrawn"
    assert g.counts()[0] == 2  # no effect after the withdrawal, across the restart


def test_g14_f1ab_enforcement_is_unchanged(tmp_path):
    g = eg.Gov(tmp_path)  # f1ab, required, default float-ceiling policy
    _, _, res = g.run()
    assert res.status == "executed" and res.control_plane_evidence["authority_receipt"]["selection_guard"]["result"] == "binding_verified"
    widened = eg.effect_policy(allowed_units=frozenset({"u-read", "u-write"}))  # sets outside the record are still refused
    eg.refused(run_as(g, 2, eg.binding_of(g.state()), widened) if False else g.run(policy=widened)[2], "policy_identity_mismatch")
    # f1ab ignores the ceilings entirely: floats and large values keep working and leave its identity untouched
    other = eg.effect_policy(max_amount=1.0e9, max_effects=7)
    assert run_as(g, 3, eg.binding_of(g.state()), other).status == "executed"
