"""V01 / M1 acceptance oracles: authenticated Registry F1a (five-set `f1ab`) narrowing exercised through the existing Runtime evaluator.

BUILDER_TESTED developer demonstration. NOT independently qualified. NOT an operational authorization mechanism. No claim that the Control Plane,
activation, durable state, commit-time authorization, restart/restore or production controls exist.

PREDECLARED INVENTORY (committed before `engine/registry_f1a_narrowing.py` exists; expected outcomes are the assertions below):

  M1-01 caller-only baseline                        caller policy alone allows u-read AND u-write (existing `LocalExecutionPolicy.check`)
  M1-02 authenticated narrowing                     signed r08-v2 f1ab selection narrows units to {u-read}: u-read ALLOWED
  M1-03 authenticated refusal                       u-write REFUSED by the existing evaluator ("local unit restriction"), Registry kind `applied`
  M1-04 five-set intersection                       exact per-set intersection, never wider than caller or record
  M1-05 explicit deny-all                           empty effective set -> refused before any executable policy object is built
  M1-06 missing / unknown set                       typed Registry refusal (`composition_record_invalid`), no policy
  M1-07 malformed record                            refused, no policy
  M1-08 forged / modified selection or record       signature / digest refusal, no policy
  M1-09 wrong consumer / tenant / scope / operation typed `selection_*_mismatch`, no policy
  M1-10 expired selection                           `selection_expired`
  M1-11 withdrawn selection                         `selection_withdrawn`
  M1-12 expired trust snapshot                      `trust_snapshot_expired`
  M1-13 snapshot version floor                      NOT RUN for M1 (durable floor enforcement is V04/V05)
  M1-14 effective-policy digest                     digest of the five sets used to build the narrowed policy == Registry canonical digest
  M1-15 tampered / mismatched digest                refusal (never a warning); no caller-supplied digest exists
  M1-16 evaluator compatibility                     protected blob unchanged; adapter with a non-narrowing record == direct `check` across a grid
  M1-17 no external effects                         no executor / SQLite / file / socket / subprocess use by the adapter
  M1-18 forged decision object                      impossible through the public interface (raw-text parameters only)
  M1-19 unrelated caller constraints preserved      target prefixes, amount, effect limits, target_match unchanged
  M1-20 dependency reproducibility                  exact Registry pin checkout, clean tree, recorded provenance

Environment: `OCS_REGISTRY_PIN_DIR` names a checkout of cogno-us/cognous-ocs-registry at the pin below, run with Registry's hash-locked
requirements-ed25519.lock.txt (PyNaCl). Without them this module is reported NOT RUN (skipped with a reason), never passed with a double.
Every selection here is signed with a real synthetic Ed25519 key; no FakeVerifier is used.
"""
from __future__ import annotations

import ast
import copy
import dataclasses
import datetime
import inspect
import itertools
import os
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PIN = "943ab1831e39fadac1eb2a414ae322ccfa09bc6a"
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
from registry_successor import decision as reg_decision  # noqa: E402
from registry_successor import v2_f1ab as f1ab  # noqa: E402
from registry_successor import v2_policy_identity as ident  # noqa: E402

from engine import registry_f1a_narrowing as adapter  # noqa: E402
from engine.safe_executor import (  # noqa: E402
    EXECUTION_ENVELOPE_VERSION,
    ExecutionEnvelope,
    ExecutionOperation,
    FrozenOperation,
    LocalExecutionPolicy,
    commitment,
    snapshot_envelope,
)

ROOT = Path(__file__).resolve().parent.parent
PROTECTED_BLOB = "2fc880dae37eac76e5074533435740ce0684209d"
FIELDS = ("allowed_actions", "allowed_adapters", "allowed_units", "allowed_institutions", "allowed_authority_domains")
PK, SK = nb.crypto_sign_seed_keypair(bytes(range(32)))
OTHER_PK, OTHER_SK = nb.crypto_sign_seed_keypair(bytes(range(1, 33)))


# ------------------------------------------------------------------------------------------------------------------ builders
def caller_policy(**over) -> LocalExecutionPolicy:
    kw = dict(allowed_institutions=frozenset({"inst-a"}), allowed_authority_domains=frozenset({"domain-a"}),
              allowed_adapters=frozenset({"a-local", "a-other"}), allowed_actions=frozenset({"act-copy", "act-list"}),
              allowed_target_prefixes=("urn:cognous:synthetic-account:",), allowed_units=frozenset({"u-read", "u-write"}),
              max_amount=1000.0, max_effects=1)
    kw.update(over)
    return LocalExecutionPolicy(**kw)


def frozen_op(unit="u-read", **over) -> FrozenOperation:
    payload = {"purpose": "v01-m1"}
    kw = dict(actor="urn:cognous:identity:agent-1", principal="urn:cognous:principal:service", institution_id="inst-a",
              authority_domain="domain-a", manifest_id="manifest-v01", manifest_version="1.0", manifest_digest="sha256:" + "a" * 64,
              proposal_commitment="sha256:" + "b" * 64, action_id="act-list", adapter_id="a-local",
              target="urn:cognous:synthetic-account:customer-1", payload=payload, payload_commitment=commitment(payload),
              requested_permissions=("act-list",), amount=50.0, unit=unit, effects=1, authority_context_id="urn:cognous:authority-context:v01",
              requirement_id="urn:cognous:requirement:v01", grant_id="urn:cognous:grant:v01", grant_revision="1", effective_max_effects=1)
    kw.update(over)
    envelope = ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-v01", "effect-v01", ExecutionOperation(**kw))
    return snapshot_envelope(envelope).operation


def reg_payload(**over) -> dict:
    p = {"allowed_actions": ["act-list"], "allowed_adapters": ["a-local"], "allowed_units": ["u-read"],
         "allowed_institutions": ["inst-a"], "allowed_authority_domains": ["domain-a"]}
    p.update(over)
    return p


def reg_record(pl=None, **over) -> dict:
    body = dict(r.make_record_v2(), family=f1ab.FAMILY_F1AB, record_id="f1ab-v01-record", payload=pl if pl is not None else reg_payload())
    body.update(over)
    return r.seal_record(body)


def sign(statement, sk=SK, domain=None) -> str:
    import base64
    return base64.b64encode(nb.crypto_sign((domain or s.V2_DOMAIN) + c.canonical_bytes(statement), sk)[:64]).decode("ascii")


def selection(rec, sk=SK, participation="required", **over) -> str:
    stmt = r.make_statement_v2(rec, family=f1ab.FAMILY_F1AB, participation=participation, **over)
    return r.dumps(s.envelope(stmt, signature=sign(stmt, sk), pub=PK))


def run(unit="u-read", *, caller=None, rec=None, sel=None, record_text=None, ctx=None, trust=None, now=None, verifier=None,
        participation="required", op=None, op_over=None):
    rec = rec if rec is not None else reg_record()
    return adapter.evaluate_with_registry_selection(
        caller if caller is not None else caller_policy(), op if op is not None else frozen_op(unit, **(op_over or {})),
        participation=participation, record_text=record_text if record_text is not None else r.dumps(rec),
        selection_text=sel if sel is not None else selection(rec, participation=participation), context=ctx or s.CTX,
        trust=trust or s.trust(pub=PK), verifier=verifier or ev.Ed25519Verifier(), now=now or r.NOW)


def digest_of(sets: dict) -> str:
    return ident.effective_policy_digest(f1ab.FAMILY_F1AB, {k: sorted(sets[k]) for k in FIELDS})


def assert_refused_no_policy(res, registry_code=None, adapter_refusal=None):
    assert res.allowed is False and res.effective_policy_digest is None and res.evaluator_refusal is None
    if registry_code is not None:
        assert res.registry_code == registry_code, res
    if adapter_refusal is not None:
        assert res.adapter_refusal == adapter_refusal, res
    assert res.non_authorizing is True


@pytest.fixture(autouse=True)
def _forbid_policy_construction_on_refusal(monkeypatch):
    """Spy on the only place a narrowed policy is built; tests that expect refusal-before-construction assert on `built`."""
    built = []
    real = adapter._build_narrowed_policy
    monkeypatch.setattr(adapter, "_build_narrowed_policy", lambda *a, **k: (built.append(1), real(*a, **k))[1])
    return built


@pytest.fixture
def built(_forbid_policy_construction_on_refusal):
    return _forbid_policy_construction_on_refusal


# ------------------------------------------------------------------------------------------------------------------ M1-01..03
def test_m1_01_caller_only_baseline_allows_both_units():
    for unit in ("u-read", "u-write"):
        caller_policy().check(frozen_op(unit))  # existing evaluator: raises PermissionError on refusal


def test_m1_02_authenticated_narrowing_allows_u_read(built):
    res = run("u-read")
    assert res.allowed is True and res.evaluator_refusal is None and res.registry_code is None and res.adapter_refusal is None
    assert res.registry_kind == "applied" and res.effective_policy_digest and res.non_authorizing is True
    assert built == [1]


def test_m1_03_authenticated_narrowing_refuses_u_write_through_the_existing_evaluator():
    res = run("u-write")
    assert res.allowed is False and res.evaluator_refusal == "local unit restriction"
    assert res.registry_code is None and res.registry_kind == "applied" and res.adapter_refusal is None
    assert res.effective_policy_digest  # the digest of the policy the evaluator actually used
    with pytest.raises(PermissionError, match="local unit restriction"):  # the same op is refused by the evaluator for the same reason
        dataclasses.replace(caller_policy(), allowed_units=frozenset({"u-read"})).check(frozen_op("u-write"))
    assert run("u-read").effective_policy_digest == res.effective_policy_digest  # same selection -> same effective policy


def test_m1_03_only_the_authenticated_selection_changes_the_outcome():
    """Same caller, same operations, same evaluator: with a record that does not narrow units, u-write is allowed; the narrowing record refuses it."""
    wide = reg_record(reg_payload(allowed_units=["u-read", "u-write"], allowed_actions=["act-copy", "act-list"], allowed_adapters=["a-local", "a-other"]))
    assert run("u-write", rec=wide).allowed is True
    assert run("u-write").allowed is False


# ------------------------------------------------------------------------------------------------------------------ M1-04
def test_m1_04_five_set_intersection_is_exact_and_never_widens():
    caller = caller_policy(allowed_institutions=frozenset({"inst-a", "inst-b"}), allowed_authority_domains=frozenset({"domain-a", "domain-b"}))
    pl = {"allowed_actions": ["act-copy", "act-list", "act-new"], "allowed_adapters": ["a-local", "a-new"],
          "allowed_units": ["u-new", "u-read", "u-write"], "allowed_institutions": ["inst-a", "inst-z"], "allowed_authority_domains": ["domain-a"]}
    rec = reg_record(pl)
    expected = {k: set(getattr(caller, k)) & set(pl[k]) for k in FIELDS}
    assert expected == {"allowed_actions": {"act-copy", "act-list"}, "allowed_adapters": {"a-local"}, "allowed_units": {"u-read", "u-write"},
                        "allowed_institutions": {"inst-a"}, "allowed_authority_domains": {"domain-a"}}
    res = run("u-read", caller=caller, rec=rec)
    assert res.allowed is True and res.effective_policy_digest == digest_of(expected)
    for refused_op in ({"unit": "u-new"}, {"adapter_id": "a-new"}, {"action_id": "act-new"}, {"institution_id": "inst-z"}):
        unit = refused_op.pop("unit", "u-read")
        assert run(unit, caller=caller, rec=rec, op_over=refused_op).allowed is False, refused_op  # caller never permitted these
    assert run("u-read", caller=caller, rec=rec, op_over={"institution_id": "inst-b"}).allowed is False  # caller permits inst-b, record does not
    assert run("u-read", caller=caller, rec=rec, op_over={"authority_domain": "domain-b"}).allowed is False


@pytest.mark.parametrize("field,value,reason", [
    ("allowed_adapters", "a-other", "local adapter restriction"),
    ("allowed_actions", "act-copy", "local action restriction"),
    ("allowed_units", "u-write", "local unit restriction"),
])
def test_m1_04_each_narrowed_dimension_is_enforced_by_the_existing_evaluator(field, value, reason):
    over = {"allowed_adapters": {"adapter_id": value}, "allowed_actions": {"action_id": value}, "allowed_units": {}}[field]
    res = run(value if field == "allowed_units" else "u-read", op_over=over)
    assert res.allowed is False and res.evaluator_refusal == reason


def test_m1_04_institutions_and_authority_domains_are_present_and_unchanged():
    rec_same = reg_record()  # equals the caller's single institution / domain
    assert run("u-read", rec=rec_same).allowed is True
    assert run("u-read", op_over={"institution_id": "inst-b"}).evaluator_refusal == "local institution restriction"


# ------------------------------------------------------------------------------------------------------------------ M1-05
@pytest.mark.parametrize("field", FIELDS)
def test_m1_05_empty_effective_set_is_explicit_deny_all_before_any_policy_is_built(field, built):
    rec = reg_record(reg_payload(**{field: []}))
    res = run("u-read", rec=rec)
    assert_refused_no_policy(res, adapter_refusal="registry_deny_all")
    assert res.registry_kind == "applied_deny_all" and built == []


def test_m1_05_empty_intersection_with_nonempty_record_is_also_deny_all(built):
    rec = reg_record(reg_payload(allowed_units=["u-other"]))  # record is non-empty but disjoint from the caller's units
    res = run("u-read", rec=rec)
    assert_refused_no_policy(res, adapter_refusal="registry_deny_all")
    assert built == []


# ------------------------------------------------------------------------------------------------------------------ M1-06 / 07
@pytest.mark.parametrize("mutate", [
    lambda p: p.pop("allowed_institutions"), lambda p: p.pop("allowed_authority_domains"), lambda p: p.pop("allowed_units"),
    lambda p: p.update(allowed_amount=["x"]), lambda p: p.update(unknown_set=["x"]),
])
def test_m1_06_missing_or_unknown_set_is_a_typed_registry_refusal(mutate, built):
    pl = reg_payload()
    mutate(pl)
    rec = reg_record(pl)
    assert_refused_no_policy(run("u-read", rec=rec, sel=selection(rec)), registry_code="composition_record_invalid")
    assert built == []


@pytest.mark.parametrize("text", ["", "not json", "[]", "null", "{", '{"kind": "record"}'])
def test_m1_07_malformed_record_text_is_refused(text, built):
    res = run("u-read", record_text=text)
    assert_refused_no_policy(res)
    assert res.registry_code is not None and built == []


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(allowed_units=["u-write", "u-read"]),   # unsorted
    lambda p: p.update(allowed_units="u-read"),                # wrong type
    lambda p: p.update(allowed_units=["u-read", "u-read"]),    # duplicate
    lambda p: p.update(allowed_units=["u*"]),                  # wildcard character
    lambda p: p.update(allowed_units=[""]),                    # empty token
])
def test_m1_07_malformed_payload_values_are_refused(mutate, built):
    pl = reg_payload()
    mutate(pl)
    rec = reg_record(pl)
    assert_refused_no_policy(run("u-read", rec=rec, sel=selection(rec)), registry_code="composition_record_invalid")
    assert built == []


def test_m1_07_v1_contract_record_is_refused_at_the_v2_entry(built):
    rec = r.make_record()  # r08-v1 f1a record
    stmt = r.make_statement_v2(rec)
    sel = r.dumps(s.envelope(stmt, signature=sign(stmt), pub=PK))
    assert_refused_no_policy(run("u-read", rec=rec, sel=sel), registry_code="composition_record_invalid")
    assert built == []


# ------------------------------------------------------------------------------------------------------------------ M1-08
def test_m1_08_forged_signature_is_refused(built):
    import base64
    rec = reg_record()
    stmt = r.make_statement_v2(rec, family=f1ab.FAMILY_F1AB)
    bad = bytearray(base64.b64decode(sign(stmt)))
    bad[0] ^= 1
    sel = r.dumps(s.envelope(stmt, signature=base64.b64encode(bytes(bad)).decode("ascii"), pub=PK))
    assert_refused_no_policy(run("u-read", rec=rec, sel=sel), registry_code="signature_invalid")
    assert built == []


def test_m1_08_signature_by_another_key_and_v1_domain_signature_are_refused(built):
    rec = reg_record()
    assert_refused_no_policy(run("u-read", rec=rec, sel=selection(rec, sk=OTHER_SK)), registry_code="signature_invalid")
    stmt = r.make_statement_v2(rec, family=f1ab.FAMILY_F1AB)
    sel = r.dumps(s.envelope(stmt, signature=sign(stmt, domain=s.V1_DOMAIN), pub=PK))
    assert_refused_no_policy(run("u-read", rec=rec, sel=sel), registry_code="signature_invalid")
    assert built == []


def test_m1_08_modified_selection_statement_is_refused(built):
    rec = reg_record()
    stmt = r.make_statement_v2(rec, family=f1ab.FAMILY_F1AB)
    sig = sign(stmt)
    altered = copy.deepcopy(stmt)
    altered["selection_id"] = "sel-test-2"
    sel = r.dumps(s.envelope(altered, signature=sig, pub=PK))  # re-digested, original signature kept
    assert_refused_no_policy(run("u-read", rec=rec, sel=sel), registry_code="signature_invalid")
    assert built == []


def test_m1_08_modified_record_is_refused_and_cannot_widen(built):
    rec = reg_record()
    sel = selection(rec)
    widened = copy.deepcopy(rec)
    widened["payload"]["allowed_units"] = ["u-read", "u-write"]
    assert_refused_no_policy(run("u-write", rec=widened, sel=sel), registry_code="composition_record_invalid")  # altered, not resealed
    resealed = r.seal_record(widened)
    res = run("u-write", rec=resealed, sel=sel)  # altered and resealed: not the record the signed selection pins
    assert_refused_no_policy(res, registry_code="record_digest_mismatch")
    assert built == []


def test_m1_08_a_different_record_than_the_pinned_one_is_refused(built):
    pinned = reg_record()
    other = reg_record(reg_payload(allowed_units=["u-read", "u-write"]), record_version=2)
    res = run("u-write", rec=other, sel=selection(pinned))
    assert_refused_no_policy(res)
    assert res.registry_code in ("record_identity_mismatch", "record_digest_mismatch") and built == []


# ------------------------------------------------------------------------------------------------------------------ M1-09
@pytest.mark.parametrize("ctx,code", [
    (c.ExpectedContext("consumer-other", "tenant-a", "inst-a", "domain-a", "op-test"), "selection_consumer_mismatch"),
    (c.ExpectedContext("consumer-test", "tenant-b", "inst-a", "domain-a", "op-test"), "selection_scope_mismatch"),
    (c.ExpectedContext("consumer-test", "tenant-a", "inst-b", "domain-a", "op-test"), "selection_scope_mismatch"),
    (c.ExpectedContext("consumer-test", "tenant-a", "inst-a", "domain-b", "op-test"), "selection_scope_mismatch"),
    (c.ExpectedContext("consumer-test", "tenant-a", "inst-a", "domain-a", "op-other"), "selection_operation_mismatch"),
])
def test_m1_09_wrong_consumer_tenant_scope_or_operation_is_refused(ctx, code, built):
    assert_refused_no_policy(run("u-read", ctx=ctx), registry_code=code)
    assert built == []


# ------------------------------------------------------------------------------------------------------------------ M1-10..12
def test_m1_10_expired_and_not_yet_effective_selections_are_refused(built):
    late = r.NOW + datetime.timedelta(days=3)
    assert_refused_no_policy(run("u-read", now=late, trust=s.trust(pub=PK, valid_until="2026-12-31T00:00:00Z")), registry_code="selection_expired")
    early = r.NOW - datetime.timedelta(days=3)
    assert_refused_no_policy(run("u-read", now=early), registry_code="selection_not_yet_effective")
    assert built == []


def test_m1_11_withdrawn_selection_is_refused(built):
    rec = reg_record()
    wd = s.withdraw_statement(r.make_statement_v2(rec, family=f1ab.FAMILY_F1AB))
    sel = r.dumps(s.envelope(wd, signature=sign(wd), pub=PK))
    assert_refused_no_policy(run("u-read", rec=rec, sel=sel), registry_code="selection_withdrawn")
    assert built == []


def test_m1_12_expired_trust_snapshot_is_refused(built):
    assert_refused_no_policy(run("u-read", trust=s.trust(pub=PK, valid_until="2026-10-25T11:00:00Z")), registry_code="trust_snapshot_expired")
    assert built == []


def test_m1_13_snapshot_version_floor():
    pytest.skip("NOT RUN (by M1 definition): the public verify_and_decide_v2 takes no snapshot-floor input; durable floor enforcement is V04/V05.")


# ------------------------------------------------------------------------------------------------------------------ M1-14 / 15
def test_m1_14_digest_equals_registry_canonical_digest_of_the_exact_policy_used():
    res = run("u-read")
    narrowed = adapter._build_narrowed_policy(caller_policy(), {"allowed_actions": ("act-list",), "allowed_adapters": ("a-local",),
                                                                "allowed_units": ("u-read",), "allowed_institutions": ("inst-a",),
                                                                "allowed_authority_domains": ("domain-a",)})
    used = {k: getattr(narrowed, k) for k in FIELDS}
    assert res.effective_policy_digest == digest_of(used)
    d = reg_decision.verify_and_decide_v2("required", {k: sorted(getattr(caller_policy(), k)) for k in FIELDS}, r.dumps(reg_record()),
                                          selection(reg_record()), s.CTX, s.trust(pub=PK), ev.Ed25519Verifier(), r.NOW)
    assert d.kind == "applied" and res.effective_policy_digest == digest_of({k: set(d.effective[k]) for k in FIELDS})
    assert res.effective_policy_digest.startswith("sha256:")


def test_m1_14_different_effective_policies_have_different_digests():
    a = run("u-read").effective_policy_digest
    b = run("u-read", rec=reg_record(reg_payload(allowed_units=["u-read", "u-write"]))).effective_policy_digest
    assert a != b


def test_m1_15_a_digest_mismatch_is_a_refusal_not_a_warning(monkeypatch):
    calls = []
    real = ident.effective_policy_digest

    def flaky(family, payload, *a, **k):
        calls.append(1)
        out = real(family, payload, *a, **k)
        return out if len(calls) == 1 else "sha256:" + "0" * 64  # the second computation disagrees
    monkeypatch.setattr(ident, "effective_policy_digest", flaky)
    res = run("u-read")
    assert_refused_no_policy(res, adapter_refusal="effective_policy_digest_mismatch")
    assert len(calls) == 2


def test_m1_15_registry_digest_failure_refuses(monkeypatch):
    def boom(*a, **k):
        raise ident.IdentityRefusal("field_invalid") if False else RuntimeError("digest unavailable")
    monkeypatch.setattr(ident, "effective_policy_digest", boom)
    assert_refused_no_policy(run("u-read"), adapter_refusal="effective_policy_digest_unavailable")


def test_m1_15_there_is_no_caller_supplied_digest_input():
    names = set(inspect.signature(adapter.evaluate_with_registry_selection).parameters)
    assert not [n for n in names if "digest" in n or "hash" in n or "fingerprint" in n]


def test_m1_15_an_effective_set_wider_than_the_caller_is_refused(monkeypatch, built):
    real = reg_decision.verify_and_decide_v2

    def widened(*a, **k):
        d = real(*a, **k)
        eff = dict(d.effective)
        eff["allowed_units"] = tuple(sorted(set(eff["allowed_units"]) | {"u-evil"}))
        return dataclasses.replace(d, effective=eff)
    monkeypatch.setattr(reg_decision, "verify_and_decide_v2", widened)
    assert_refused_no_policy(run("u-read"), adapter_refusal="effective_policy_widens_caller")
    assert built == []


@pytest.mark.parametrize("tamper", [
    lambda eff: eff.pop("allowed_institutions"),
    lambda eff: eff.update(extra=("x",)),
    lambda eff: eff.update(allowed_units=["u-read"]),   # list, not tuple
    lambda eff: eff.update(allowed_units=("u-read", "")),
    lambda eff: eff.update(allowed_units=()),
])
def test_m1_15_a_malformed_effective_policy_from_the_decision_is_refused(monkeypatch, tamper, built):
    real = reg_decision.verify_and_decide_v2

    def tampered(*a, **k):
        d = real(*a, **k)
        eff = dict(d.effective)
        tamper(eff)
        return dataclasses.replace(d, effective=eff)
    monkeypatch.setattr(reg_decision, "verify_and_decide_v2", tampered)
    res = run("u-read")
    assert_refused_no_policy(res)
    assert res.adapter_refusal in ("effective_policy_invalid", "registry_deny_all") and built == []


def test_m1_15_non_applied_or_non_decision_results_never_proceed(monkeypatch, built):
    for fake in (None, "applied", {"kind": "applied"}, object()):
        monkeypatch.setattr(reg_decision, "verify_and_decide_v2", lambda *a, _f=fake, **k: _f)
        assert_refused_no_policy(run("u-read"), adapter_refusal="registry_decision_invalid")
    real_cls = reg_decision.PolicyDecision
    monkeypatch.setattr(reg_decision, "verify_and_decide_v2", lambda *a, **k: real_cls("overlay_not_applied", {k_: ("x",) for k_ in FIELDS}, False, (), "selection_missing", "", True))
    res = run("u-read")
    assert_refused_no_policy(res, registry_code="selection_missing")
    assert res.registry_kind == "overlay_not_applied" and built == []
    monkeypatch.setattr(reg_decision, "verify_and_decide_v2", lambda *a, **k: real_cls("required_policy_unavailable", None, False, (), "signature_invalid"))
    assert_refused_no_policy(run("u-read"), registry_code="signature_invalid")


# ------------------------------------------------------------------------------------------------------------------ M1-16
def test_m1_16_protected_runtime_source_is_byte_identical():
    blob = subprocess.run(["git", "hash-object", "engine/safe_executor.py"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    assert blob == PROTECTED_BLOB == r.BLOB  # the Registry record's evaluator identity names this very blob
    diff = subprocess.run(["git", "diff", "--quiet", "c5e97e87dd239c821b1b4a406a55dcd03c5bc61f", "--", "engine/safe_executor.py"], cwd=ROOT).returncode
    assert diff == 0


def test_m1_16_non_narrowing_record_gives_exactly_the_direct_evaluator_result_across_a_grid():
    caller = caller_policy(allowed_institutions=frozenset({"inst-a", "inst-b"}))
    pl = {"allowed_actions": ["act-copy", "act-list"], "allowed_adapters": ["a-local", "a-other"], "allowed_units": ["u-read", "u-write"],
          "allowed_institutions": ["inst-a", "inst-b"], "allowed_authority_domains": ["domain-a"]}
    rec = reg_record(pl)
    sel = selection(rec)
    grid = itertools.product(("u-read", "u-write", "u-none"), ("a-local", "a-other", "a-none"), ("act-list", "act-copy", "act-none"),
                             ("inst-a", "inst-b", "inst-none"), ("domain-a", "domain-none"),
                             ("urn:cognous:synthetic-account:c1", "urn:elsewhere:c1"), (50.0, 5000.0))
    seen = set()
    for unit, adp, act, inst, dom, tgt, amt in itertools.islice(grid, 0, None, 7):
        over = dict(adapter_id=adp, action_id=act, institution_id=inst, authority_domain=dom, target=tgt, amount=amt)
        op = frozen_op(unit, **over)
        try:
            caller.check(op)
            direct = None
        except PermissionError as exc:
            direct = str(exc)
        res = run(unit, caller=caller, rec=rec, sel=sel, op=op)
        assert res.allowed is (direct is None) and res.evaluator_refusal == direct, (unit, over)
        seen.add(direct)
    assert None in seen and len(seen) >= 5  # the sample exercises allow and several distinct evaluator refusals


def test_m1_16_narrowing_is_monotone_across_a_grid():
    rec = reg_record()
    sel = selection(rec)
    for unit, adp, act in itertools.product(("u-read", "u-write"), ("a-local", "a-other"), ("act-list", "act-copy")):
        op = frozen_op(unit, adapter_id=adp, action_id=act)
        res = run(unit, rec=rec, sel=sel, op=op)
        if res.allowed:
            caller_policy().check(op)  # allowed after narrowing => allowed by the caller alone
        assert res.allowed is (unit == "u-read" and adp == "a-local" and act == "act-list"), (unit, adp, act)


# ------------------------------------------------------------------------------------------------------------------ M1-17
def test_m1_17_adapter_source_has_no_effect_capable_imports():
    src = Path(adapter.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").split(".")[0] if n.level == 0 else "<relative>")
    assert mods <= {"__future__", "dataclasses", "importlib", "typing", "engine"}, mods
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & {"open", "exec", "eval", "compile", "__import__"}, called
    engine_imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "engine.safe_executor" for a in n.names}
    assert engine_imports <= {"FrozenOperation", "LocalExecutionPolicy"}, engine_imports  # no destination, executor, envelope or workflow


def test_m1_17_no_files_sqlite_network_or_subprocess_are_used(monkeypatch, tmp_path):
    def forbid(name):
        def _raise(*a, **k):
            raise AssertionError(f"adapter attempted an external effect: {name}")
        return _raise
    for target, name in ((sqlite3, "connect"), (subprocess, "Popen"), (subprocess, "run"), (os, "system"), (socket, "create_connection")):
        monkeypatch.setattr(target, name, forbid(f"{target.__name__}.{name}"))
    monkeypatch.setattr(socket.socket, "connect", forbid("socket.connect"))
    monkeypatch.setattr(socket.socket, "sendto", forbid("socket.sendto"))
    monkeypatch.chdir(tmp_path)
    before = sorted(p.name for p in tmp_path.rglob("*"))
    for unit in ("u-read", "u-write"):
        run(unit)
    run("u-read", ctx=c.ExpectedContext("x", "tenant-a", "inst-a", "domain-a", "op-test"))
    assert sorted(p.name for p in tmp_path.rglob("*")) == before == []


def test_m1_17_result_carries_no_authority_or_executable_policy():
    res = run("u-read")
    assert {f.name for f in dataclasses.fields(res)} == {"allowed", "adapter_refusal", "evaluator_refusal", "registry_code", "registry_kind",
                                                         "effective_policy_digest", "non_authorizing", "classification"}
    assert res.non_authorizing is True and res.classification == adapter.CLASSIFICATION
    assert "non-authorizing" in adapter.CLASSIFICATION
    with pytest.raises(dataclasses.FrozenInstanceError):
        res.allowed = False  # type: ignore[misc]
    for forbidden in ("policy", "grant", "approval", "claim", "authorized", "operationally_authorized"):
        assert not hasattr(res, forbidden)


# ------------------------------------------------------------------------------------------------------------------ M1-18
def test_m1_18_public_interface_takes_raw_inputs_only():
    sig = inspect.signature(adapter.evaluate_with_registry_selection)
    names = list(sig.parameters)
    assert names == ["caller_policy", "op", "participation", "record_text", "selection_text", "context", "trust", "verifier", "now"]
    assert all(sig.parameters[n].kind is inspect.Parameter.KEYWORD_ONLY for n in names[2:])
    for bad in ("decision", "policy_decision", "report", "verification_report", "authenticated", "verified", "authorized", "effective_policy_digest"):
        with pytest.raises(TypeError):
            adapter.evaluate_with_registry_selection(caller_policy(), frozen_op(), participation="required", record_text="x", selection_text="y",
                                                     context=s.CTX, trust=s.trust(pub=PK), verifier=ev.Ed25519Verifier(), now=r.NOW, **{bad: True})


def test_m1_18_a_forged_decision_or_report_object_in_place_of_text_is_refused(built):
    forged = reg_decision.PolicyDecision("applied", {k: tuple(getattr(caller_policy(), k)) for k in FIELDS}, False, (), None, "", False)
    for bogus in (forged, {"kind": "applied"}, b"bytes", None, 7):
        res = adapter.evaluate_with_registry_selection(caller_policy(), frozen_op(), participation="required", record_text=bogus,
                                                       selection_text=bogus, context=s.CTX, trust=s.trust(pub=PK), verifier=ev.Ed25519Verifier(), now=r.NOW)
        assert_refused_no_policy(res, adapter_refusal="input_invalid")
    assert built == []


def test_m1_18_the_registry_verify_and_decide_v2_is_called_once_with_the_raw_texts(monkeypatch):
    calls = []
    real = reg_decision.verify_and_decide_v2
    monkeypatch.setattr(reg_decision, "verify_and_decide_v2", lambda *a, **k: (calls.append((a, k)), real(*a, **k))[1])
    rec = reg_record()
    text, sel = r.dumps(rec), selection(rec)
    run("u-read", rec=rec, record_text=text, sel=sel)
    assert len(calls) == 1
    args = calls[0][0]
    assert args[0] == "required" and args[2] == text and args[3] == sel and type(args[2]) is str


@pytest.mark.parametrize("kwargs", [
    dict(participation="bogus"), dict(participation=None), dict(participation=1),
])
def test_m1_18_invalid_participation_is_refused(kwargs, built):
    assert_refused_no_policy(run("u-read", **kwargs), adapter_refusal="input_invalid")
    assert built == []


@pytest.mark.parametrize("caller,op", [
    ({"allowed_units": ["u-read"]}, None), (None, {"unit": "u-read"}), ("caller", None), (None, "op"),
])
def test_m1_18_wrong_policy_or_operation_types_are_refused(caller, op):
    res = adapter.evaluate_with_registry_selection(caller if caller is not None else caller_policy(), op if op is not None else frozen_op(),
                                                   participation="required", record_text=r.dumps(reg_record()), selection_text=selection(reg_record()),
                                                   context=s.CTX, trust=s.trust(pub=PK), verifier=ev.Ed25519Verifier(), now=r.NOW)
    assert_refused_no_policy(res, adapter_refusal="input_invalid")


def test_m1_18_optional_participation_never_falls_back_to_the_caller_policy():
    rec = reg_record()
    assert run("u-read", rec=rec, participation="optional").allowed is True  # a valid signed optional selection applies the same narrowing
    assert run("u-write", rec=rec, participation="optional").allowed is False
    forged = selection(rec, sk=OTHER_SK, participation="optional")
    res = run("u-write", rec=rec, sel=forged, participation="optional")  # Registry says overlay_not_applied; the caller policy would allow u-write
    assert_refused_no_policy(res)
    assert res.registry_kind == "overlay_not_applied" and res.registry_code == "signature_invalid"
    caller_policy().check(frozen_op("u-write"))  # ... and the caller alone indeed would have allowed it


def test_m1_18_registry_unavailable_fails_closed(monkeypatch):
    import importlib
    real = importlib.import_module
    monkeypatch.setattr(adapter.importlib, "import_module", lambda name, *a, **k: (_ for _ in ()).throw(ImportError(name)) if name.startswith("registry_") else real(name, *a, **k))
    assert_refused_no_policy(run("u-read"), adapter_refusal="registry_unavailable")


# ------------------------------------------------------------------------------------------------------------------ M1-19
def test_m1_19_unrelated_caller_constraints_are_preserved():
    caller = caller_policy(allowed_target_prefixes=("urn:cognous:synthetic-account:customer-1",), max_amount=100.0, max_effects=2, target_match="exact")
    eff = {"allowed_actions": ("act-list",), "allowed_adapters": ("a-local",), "allowed_units": ("u-read",), "allowed_institutions": ("inst-a",),
           "allowed_authority_domains": ("domain-a",)}
    narrowed = adapter._build_narrowed_policy(caller, eff)
    assert type(narrowed) is LocalExecutionPolicy and narrowed is not caller
    for name in ("allowed_target_prefixes", "max_amount", "max_effects", "target_match"):
        assert getattr(narrowed, name) == getattr(caller, name), name
    assert {f.name for f in dataclasses.fields(LocalExecutionPolicy)} == set(FIELDS) | {"allowed_target_prefixes", "max_amount", "max_effects", "target_match"}
    assert caller.allowed_units == frozenset({"u-read", "u-write"})  # the caller's own policy object is never mutated


def test_m1_19_unrelated_restrictions_still_refuse_through_the_adapter():
    caller = caller_policy(allowed_target_prefixes=("urn:cognous:synthetic-account:customer-1",), max_amount=100.0, max_effects=1, target_match="exact")
    rec = reg_record()
    assert run("u-read", caller=caller, rec=rec).allowed is True
    assert run("u-read", caller=caller, rec=rec, op_over={"amount": 500.0}).evaluator_refusal == "local amount restriction"
    assert run("u-read", caller=caller, rec=rec, op_over={"target": "urn:cognous:synthetic-account:customer-1/x"}).evaluator_refusal == "local target restriction"
    assert run("u-read", caller=caller, rec=rec, op_over={"effective_max_effects": 2}).evaluator_refusal == "local effect-count restriction"


# ------------------------------------------------------------------------------------------------------------------ M1-20
def test_m1_20_registry_dependency_is_the_exact_pin_and_recorded(record_property):
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PIN_DIR, capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=PIN_DIR, capture_output=True, text=True, check=True).stdout
    assert head == PIN and dirty == ""
    for mod in (c, reg_decision, f1ab, ident, ev):
        assert str(Path(mod.__file__).resolve()).startswith(PIN_DIR), mod.__file__  # the code under test is the pinned checkout, not an installed copy
    assert hasattr(reg_decision, "validate_record_text_v2")  # the V01a repair is present at the pin
    import nacl
    record_property("registry_pin", head)
    record_property("pynacl", nacl.__version__)
    record_property("python", sys.version.split()[0])
    lock = Path(PIN_DIR, "requirements-ed25519.lock.txt").read_text(encoding="utf-8")
    assert "pynacl==" + nacl.__version__ in lock  # the PyNaCl in use is the one Registry's hash-locked file names
    assert subprocess.run(["git", "ls-files", "--error-unmatch", "requirements-ed25519.lock.txt"], cwd=PIN_DIR, capture_output=True).returncode == 0


def test_m1_20_runtime_has_no_registry_copy_or_pyproject_dependency():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert not [p for p in tracked if p.startswith("registry_") or "registry_successor" in p]
    src = Path(adapter.__file__).read_text(encoding="utf-8")
    assert "sys.path" not in src and "import registry_successor" not in src.replace("import_module(", "")  # resolved lazily from the host's environment
