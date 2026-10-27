"""F-01 remediation oracles (V07 finding, HIGH): the effect-time guard enforces that the executor's effective policy is a subset of the
authenticated activated Registry record, for each of the five F1a sets, independently of the claim supplier (r08-v2 Amendment 3).

Committed with the patch. BUILDER_TESTED, non-attesting; NOT independently qualified. Budget: at most 20 targeted local executions.
Reuses the V05 fixtures by import (`test_registry_selection_effect_guard`); the V04/V05 batteries are not repeated here.

  F01-1  original A08 widening attempt: refused, zero effects      F01-2  strictly narrower policy than the record: executes
  F01-3  equal policy: executes                                    F01-4..8  widening ONE of the five sets: refused (one case per set)
  F01-9  missing/malformed authenticated record: HOLD (store_unknown) F01-10 claim digest vs executable policy; sets vs digest; sets absent
  F01-11 withdrawn selection still refused                         F01-12 legacy (ungoverned and never-activated) still executes
  F01-13 failing evidence write: zero effects                      F01-14 a racing revocation stays atomic with the new check in place
"""
from __future__ import annotations

import sqlite3
import threading
import time

import pytest

import test_registry_selection_effect_guard as g

v4, sa = g.v4, g.sa
AtomicAuthorityEffectDestination = g.AtomicAuthorityEffectDestination
AtomicLocalControlPlaneExecutor = g.AtomicLocalControlPlaneExecutor

EXTRA = {"allowed_actions": "act-extra", "allowed_adapters": "a-extra", "allowed_units": "u-extra",
         "allowed_institutions": "inst-extra", "allowed_authority_domains": "domain-extra"}
FIELDS = sa.sa_fields()


def wide_payload():
    return {k: sorted(set(v4.PAYLOAD[k]) | {EXTRA[k]}) for k in FIELDS}


def wide_gov(tmp_path):
    """A governed destination whose activated Registry record allows the base member AND one extra member in each of the five sets."""
    gov = g.Gov(tmp_path, activate=False)
    rec = v4.reg_record(payload=wide_payload())
    gov.stmt = v4.select_stmt(rec)
    gov.e.activate(gov.stmt, rec=rec)
    return gov


def policy_with(**sets):
    return g.effect_policy(**{k: frozenset(v) for k, v in sets.items()})


def provision_for(gov, policy, claim_id, env=None):
    """A claim whose binding carries the digest of `policy` (what a supplier that composed for that policy would issue)."""
    env = env or g.envelope()
    binding = g.binding_of(gov.state(), effective_policy_digest=sa.policy_digest_of(policy))
    gov.dest.provision_claim(g.make_claim(env, gov.now, binding=binding, claim_id=claim_id))
    return env


def run(gov, policy, claim_id="f01-1", env=None):
    env = provision_for(gov, policy, claim_id, env)
    return gov.executor(policy).execute(envelope=env, claim_id=claim_id)


# F01-1 ----------------------------------------------------------------------------------------------------------------------------
def test_f01_1_original_a08_widening_attempt_is_refused_with_zero_effects(tmp_path):
    gov = g.Gov(tmp_path)  # the record allows only act-list
    wide = policy_with(allowed_actions={"act-list", "act-extra"})
    env = g.envelope(action_id="act-extra")
    result = run(gov, wide, "adv-wide", env)
    typed = g.refused(result, "policy_identity_mismatch", "REFUSE")
    assert typed["cause"] == "policy_not_subset:allowed_actions"
    assert gov.counts() == (0, 0, 0, 0)


# F01-2 / F01-3 ----------------------------------------------------------------------------------------------------------------------
def test_f01_2_a_strictly_narrower_policy_than_the_record_commits(tmp_path):
    gov = wide_gov(tmp_path)
    narrow = g.effect_policy()  # base members only: a proper subset of the wide record in every set
    assert all(set(getattr(narrow, k)) < set(wide_payload()[k]) for k in FIELDS)
    result = run(gov, narrow)
    assert result.status == "executed" and gov.counts()[0] == 1


def test_f01_3_an_equal_policy_commits(tmp_path):
    gov = wide_gov(tmp_path)
    equal = policy_with(**wide_payload())
    result = run(gov, equal, env=g.envelope(action_id="act-extra"))
    assert result.status == "executed" and gov.counts()[0] == 1


# F01-4 .. F01-8 ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("field", FIELDS)
def test_f01_4_to_8_widening_one_set_is_refused(tmp_path, field):
    gov = g.Gov(tmp_path)
    widened = policy_with(**{field: set(v4.PAYLOAD[field]) | {EXTRA[field]}})
    result = run(gov, widened)  # the envelope itself stays inside the record; only the executor policy (and the supplier's digest) widen
    typed = g.refused(result, "policy_identity_mismatch", "REFUSE")
    assert typed["cause"] == f"policy_not_subset:{field}"
    assert gov.counts() == (0, 0, 0, 0)


# F01-9 -------------------------------------------------------------------------------------------------------------------------------
def test_f01_9a_a_tampered_record_payload_in_the_store_holds(tmp_path):
    gov = g.Gov(tmp_path)
    env = provision_for(gov, g.effect_policy(), "f01-9a")  # a valid claim, provisioned BEFORE the store is damaged
    with sqlite3.connect(gov.dest.path) as conn:
        conn.execute("UPDATE selection_activation_v1 SET record_json = replace(record_json, 'act-list', 'act-list\",\"act-zzz')")
    result = gov.executor().execute(envelope=env, claim_id="f01-9a")
    g.refused(result, "store_unknown", "HOLD")
    assert gov.counts() == (0, 0, 0, 0)


def test_f01_9b_the_guard_itself_holds_on_a_missing_or_malformed_record(tmp_path):
    gov = g.Gov(tmp_path)
    store = gov.dest._selection_store
    digest, sets = sa.policy_digest_of(g.effect_policy()), sa.policy_sets_of(g.effect_policy())
    for bad in ({"record_json": "{}", "record_digest": "x", "record_policy_digest": "x"},
                {"record_json": "not json", "record_digest": "x", "record_policy_digest": "x"},
                {"record_json": None, "record_digest": None, "record_policy_digest": None}):
        with pytest.raises(sa.SelectionRefusal) as exc:
            store._check_policy_containment(bad, digest, sets)
        assert exc.value.code == "store_unknown" and exc.value.refusal_class == "HOLD"


# F01-10 ------------------------------------------------------------------------------------------------------------------------------
def test_f01_10a_claim_digest_that_does_not_match_the_executable_policy_is_refused(tmp_path):
    gov = wide_gov(tmp_path)
    claim_for = policy_with(**wide_payload())  # the claim binds the wide policy ...
    env = provision_for(gov, claim_for, "f01-10a")
    result = gov.executor(g.effect_policy()).execute(envelope=env, claim_id="f01-10a")  # ... the executor holds the narrow one
    g.refused(result, "policy_identity_mismatch", "REFUSE")
    assert gov.counts() == (0, 0, 0, 0)


def test_f01_10b_sets_that_do_not_reproduce_the_supplied_digest_are_refused(tmp_path):
    gov = g.Gov(tmp_path)
    policy = g.effect_policy()
    env = provision_for(gov, policy, "f01-10b")
    wide_sets = sa.policy_sets_of(policy_with(allowed_actions={"act-list", "act-extra"}))
    with pytest.raises(sa.SelectionRefusal) as exc:
        gov.dest.execute_claim_atomic("f01-10b", g.snapshot_envelope(env), attempt_id="a1",
                                      policy_effective_digest=sa.policy_digest_of(policy), policy_effective_sets=wide_sets)
    assert exc.value.code == "policy_identity_mismatch" and exc.value.cause == "policy_sets_digest"
    assert gov.counts() == (0, 0, 0, 0)


def test_f01_10c_a_bound_claim_without_executor_sets_is_refused(tmp_path):
    gov = g.Gov(tmp_path)
    policy = g.effect_policy()
    env = provision_for(gov, policy, "f01-10c")
    with pytest.raises(sa.SelectionRefusal) as exc:
        gov.dest.execute_claim_atomic("f01-10c", g.snapshot_envelope(env), attempt_id="a1", policy_effective_digest=sa.policy_digest_of(policy))
    assert exc.value.code == "verifier_unavailable" and exc.value.cause == "policy_sets_unavailable"
    assert gov.counts() == (0, 0, 0, 0)


# F01-11 ------------------------------------------------------------------------------------------------------------------------------
def test_f01_11_a_withdrawn_selection_is_still_refused(tmp_path):
    gov = wide_gov(tmp_path)
    policy = g.effect_policy()
    env = provision_for(gov, policy, "f01-11")
    gov.e.withdraw(v4.withdraw_stmt(gov.stmt, 2))
    g.refused(gov.executor(policy).execute(envelope=env, claim_id="f01-11"), "selection_withdrawn")
    assert gov.counts() == (0, 0, 0, 0)


# F01-12 ------------------------------------------------------------------------------------------------------------------------------
def test_f01_12a_legacy_ungoverned_execution_is_unchanged(tmp_path):
    g.test_l01_legacy_claim_without_a_selection_store_is_unchanged(tmp_path)


def test_f01_12b_legacy_claim_in_a_never_activated_scope_still_falls_back(tmp_path):
    g.test_l02_legacy_claim_in_a_never_activated_scope_falls_back_with_evidence(tmp_path)


# F01-13 ------------------------------------------------------------------------------------------------------------------------------
def test_f01_13_a_failing_evidence_write_leaves_zero_effects_on_the_new_path(tmp_path):
    gov = wide_gov(tmp_path)

    class Failing(AtomicAuthorityEffectDestination):
        def _selection_receipt_evidence(self, evidence):
            raise OSError("evidence store unavailable")

    bad = Failing(gov.e.root, clock=lambda: gov.e.now)
    bad.configure_selection_authority(gov.e.anchor)
    policy = g.effect_policy()
    env = provision_for(gov, policy, "f01-13")
    result = AtomicLocalControlPlaneExecutor(workflow=None, destination=bad, policy=policy).execute(envelope=env, claim_id="f01-13")
    assert result.status != "executed" and gov.counts() == (0, 0, 0, 0)


# F01-14 ------------------------------------------------------------------------------------------------------------------------------
def test_f01_14_a_racing_revocation_stays_atomic_with_the_containment_check(tmp_path):
    gov = wide_gov(tmp_path)
    policy = g.effect_policy()
    env = provision_for(gov, policy, "f01-14")
    outcome = {}
    revoking = v4.trust(2, revoked_selections=frozenset({("sel-test-1", 1)}))

    class Racing(AtomicAuthorityEffectDestination):
        def _transaction_stage(self, stage):
            if stage == "after_effect_insert_before_commit":  # containment has passed; the effect is inserted; the lock is held
                def revoke():
                    other = AtomicAuthorityEffectDestination(gov.e.root, clock=lambda: gov.e.now)
                    other.configure_selection_authority(gov.e.anchor)
                    other.sync_selection("snapshot", trust=revoking)
                t = threading.Thread(target=revoke)
                t.start()
                time.sleep(0.4)
                outcome["still_blocked"] = t.is_alive()
                outcome["thread"] = t
            return None

    racing = Racing(gov.e.root, clock=lambda: gov.e.now)
    racing.configure_selection_authority(gov.e.anchor)
    result = AtomicLocalControlPlaneExecutor(workflow=None, destination=racing, policy=policy).execute(envelope=env, claim_id="f01-14")
    outcome["thread"].join(timeout=30)
    assert outcome["still_blocked"] is True and result.status == "executed" and gov.counts()[0] == 1
    assert gov.state().status == "revoked"
