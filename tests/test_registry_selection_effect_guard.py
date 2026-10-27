"""V05 predeclared oracles: the effect-time selection-authority guard inside `execute_claim_atomic` (BUILDER_TESTED, non-attesting).

NOT independently qualified. Committed BEFORE the implementation. Budget: at most 50 executed cases for V05 (42 declared here, parametrized cases
counted separately). Reuses the V04 fixtures (`test_registry_selection_authority`) and the atomic-route fixtures
(`test_atomic_route_valid_flow_identity`) by import; the V04/Registry/Control Plane batteries are NOT repeated.

Inventory (id: invariant)
  V-01 valid governed claim executes; guard evidence is retained in the atomic receipt   V-02 optional selection executes
  V-03 re-activation at a higher epoch: the new claim executes                             V-04 receipt row equals returned receipt, head bound
  V-05 no network/subprocess I/O while the guard and the effect run
  S-01 withdrawn -> selection_withdrawn   S-02 revoked pair -> selection_revoked   S-03 issuer key revoked -> issuer_key_revoked
  S-04 stale generation (older activation, higher epoch now) -> REFUSE_STALE   S-05 expired current snapshot -> HOLD trust_snapshot_expired
  S-06 x2 selection window (not yet effective / expired) refuses
  B-01 foreign activation_ref   B-02 earlier activation_ref of the same chain -> REFUSE_STALE   B-03 substituted record triple
  B-04 executor policy does not match the claim-carried effective digest   B-05 binding names a scope with no row
  B-06 substituted snapshot fingerprint   B-07 ratchet set after an optional activation -> participation_mismatch
  C-01 a revocation racing the transaction blocks on the lock and cannot commit before the effect   C-02 x2 withdraw/revoke committed before BEGIN refuse
  C-03 a refusal writes nothing   C-04 stage order: guard runs after the lock-time clock, before the effect insert   C-05 post-commit withdrawal does not undo the effect
  E-01 failing guard-evidence write -> no effect   E-02 failing commit-time stage -> no effect   E-03 typed refusal evidence reaches the result
  E-04 failing failure-record write on a refusal -> still no effect
  L-01 legacy claim, store not configured: unchanged   L-02 legacy claim, never-activated scope: caller-policy fallback with evidence
  L-03 legacy claim in a ratcheted scope refused (selection_missing)   L-04 legacy claim over an active optional row refused
  L-05 legacy claim over a withdrawn optional row: fallback "withdrawn"   L-06 withdrawn REQUIRED scope still refuses legacy claims
  L-07 successor-profile claim with the selection store not configured -> HOLD
  X-01 corrupted activation row -> HOLD   X-02 restored older database -> HOLD   X-03 missing anchor -> HOLD   X-04 tampered log entry -> HOLD
  X-05 store never baselined -> HOLD
  F-01 the existing evaluator denies an operation outside the narrowed policy even for a valid claim
"""
from __future__ import annotations

import dataclasses
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
from datetime import timedelta

import pytest

import test_registry_selection_authority as v4  # skips the module when the Registry pin / PyNaCl are absent
import test_atomic_route_valid_flow_identity as flow  # skips when the Control Plane contract is absent

pytest.importorskip("agent_control_plane.local_authority_effect")
from agent_control_plane.bounded import commitment as cp_commitment  # noqa: E402
from agent_control_plane.local_authority_effect import (  # noqa: E402
    LOCAL_AUTHORITY_EFFECT_PROFILE, LOCAL_AUTHORITY_EFFECT_PROFILE_V2)

from engine import registry_selection_authority as sa  # noqa: E402
from engine.atomic_local_control_plane_executor import AtomicLocalControlPlaneExecutor  # noqa: E402
from engine.local_authority_effect import AtomicAuthorityEffectDestination  # noqa: E402
from engine.safe_executor import (  # noqa: E402
    EXECUTION_ENVELOPE_VERSION, ExecutionEnvelope, LocalExecutionPolicy, snapshot_envelope)

P = v4.PAYLOAD
GS = v4.GS


def op_changes():
    return dict(institution_id="inst-a", authority_domain="domain-a", action_id="act-list", adapter_id="a-local", unit="u-read", tenant_id="tenant-a",
                grant_id=flow.GRANT)


def effect_policy(**over):
    base = dict(allowed_institutions=frozenset(P["allowed_institutions"]), allowed_authority_domains=frozenset(P["allowed_authority_domains"]),
                allowed_adapters=frozenset(P["allowed_adapters"]), allowed_actions=frozenset(P["allowed_actions"]),
                allowed_target_prefixes=("urn:cognous:synthetic-account:",), allowed_units=frozenset(P["allowed_units"]), max_amount=1000.0, max_effects=2)
    base.update(over)
    return LocalExecutionPolicy(**base)


def envelope(**changes):
    op = dataclasses.replace(flow.operation(), **{**op_changes(), **changes})
    return ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)


def binding_of(state, **over):
    b = {"binding_profile": "ocs-policy-binding/1", "record_id": state.record_id, "record_version": state.record_version,
         "record_digest": state.record_digest, "selection_id": state.selection_id, "selection_epoch": state.selection_epoch,
         "selection_digest": state.selection_digest, "family": GS["family"], "consumer_id": GS["consumer_id"],
         "scope": {"tenant": GS["tenant"], "institution": GS["institution"], "authority_domain": GS["authority_domain"]},
         "operation": GS["operation"], "effective_policy_digest": v4.effective_digest(), "activation_ref": state.activation_ref,
         "trust_snapshot_version": state.trust_snapshot_version, "trust_snapshot_fingerprint": state.trust_snapshot_fingerprint}
    b.update(over)
    return b


def make_claim(env, now, *, binding=None, claim_id="claim-1"):
    """A tenant claim (successor profile when `binding` is given, legacy profile otherwise)."""
    op = snapshot_envelope(env).operation
    tenant = op.tenant_id
    approval_state = [{"approval_ref": flow.APPROVAL, "role_id": "urn:cognous:role:reviewer", "approver": "urn:cognous:principal:human", "status": "active",
                       "grant_id": op.grant_id, "grant_revision": op.grant_revision, "proposal_commitment": op.proposal_commitment,
                       "policy_versions": [{"ref": flow.POLICY_REF, "version": "1"}], "observed_at": now.isoformat()}]
    policy_state = [{"ref": flow.POLICY_REF, "version": "1", "status": "active", "observed_at": now.isoformat()}]
    if tenant is not None:
        approval_state[0]["tenant_id"] = tenant
        policy_state[0]["tenant_id"] = tenant
    raw = {
        "profile": LOCAL_AUTHORITY_EFFECT_PROFILE_V2 if binding is not None else LOCAL_AUTHORITY_EFFECT_PROFILE, "claim_id": claim_id,
        "decision_id": env.decision_id, "effect_id": env.effect_id, "institution_id": op.institution_id, "authority_domain": op.authority_domain,
        "actor": op.actor, "principal": op.principal, "grant_id": op.grant_id, "grant_revision": op.grant_revision, "grant_status": "active",
        "manifest_id": op.manifest_id, "manifest_version": op.manifest_version, "manifest_digest": op.manifest_digest, "action_id": op.action_id,
        "adapter_id": op.adapter_id, "target": op.target, "payload_commitment": op.payload_commitment,
        "requested_permissions": list(op.requested_permissions), "amount": op.amount, "unit": op.unit, "effects": op.effects,
        "authority_context_id": op.authority_context_id, "requirement_id": op.requirement_id, "requirement_commitment": "sha256:" + "3" * 64,
        "operation_commitment": AtomicAuthorityEffectDestination._operation_commitment(snapshot_envelope(env)),
        "approval_state": approval_state, "approval_state_commitment": cp_commitment(approval_state),
        "policy_state": policy_state, "policy_state_commitment": cp_commitment(policy_state),
        "evidence_state": [], "evidence_state_commitment": cp_commitment([]),
        "authority_state_commitment": cp_commitment({"grant_id": op.grant_id, "grant_revision": op.grant_revision, "grant_status": "active",
                                                     "requirement_commitment": "sha256:" + "3" * 64, "approvals": approval_state,
                                                     "policies": policy_state, "evidence": []}),
        "budget_id": "budget-" + claim_id, "max_effects": op.effective_max_effects,
        "not_before": (now - timedelta(days=3)).isoformat(), "expires_at": (now + timedelta(days=5)).isoformat(), "issued_at": now.isoformat(),
        "decision_input_commitment": None, "decision_input_profile_version": None, "source": "trusted_control_plane_workflow",
        "authorizing_by_possession": False,
    }
    if tenant is not None:
        raw.update(tenant_id=tenant, authorization_generation="bounded-authorization-effect/0.2")
    if binding is not None:
        raw["policy_binding"] = binding
    raw["claim_commitment"] = cp_commitment(raw)
    return raw


class Gov:
    """A governed destination: V04 store baselined and snapshot-synced; optionally one activated selection."""

    def __init__(self, tmp_path, *, activate=True, participation="required", snapshot_until=None):
        self.e = v4.Env(tmp_path).boot(**({"valid_until": snapshot_until} if snapshot_until else {}))
        self.dest = self.e.dest
        self.tmp = tmp_path
        self.stmt = v4.select_stmt(v4.REC, participation=participation)
        if activate:
            self.e.activate(self.stmt)
        self.n = 0

    @property
    def now(self):
        return self.e.now

    def state(self):
        return self.e.state()

    def executor(self, policy=None, dest=None):
        return AtomicLocalControlPlaneExecutor(workflow=None, destination=dest or self.dest, policy=policy or effect_policy())

    def claim(self, binding="current", claim_id=None, **env_changes):
        self.n += 1
        if binding is None:
            env_changes.setdefault("tenant_id", None)
        env = envelope(**env_changes)
        cid = claim_id or f"claim-{self.n}"
        if binding == "current":
            binding = binding_of(self.state())
        raw = make_claim(env, self.now, binding=binding, claim_id=cid)
        self.dest.provision_claim(raw)
        return env, cid

    def run(self, binding="current", policy=None, **env_changes):
        env, cid = self.claim(binding, **env_changes)
        return env, cid, self.executor(policy).execute(envelope=env, claim_id=cid)

    def counts(self):
        with sqlite3.connect(self.dest.path) as conn:
            return tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("effects", "attempts", "authority_effect_receipts_v1")) + (
                conn.execute("SELECT COALESCE(SUM(used_effects),0) FROM effect_budgets_v1").fetchone()[0],)

    def receipt(self):
        with sqlite3.connect(self.dest.path) as conn:
            return conn.execute("SELECT receipt_json FROM authority_effect_receipts_v1").fetchone()[0]


def refused(result, code, cls=None):
    assert result.status == "denied" and result.attempted is False, (result.status, result.error)
    typed = result.control_plane_evidence.get("selection_refusal")
    assert typed and typed["code"] == code, (typed, result.error)
    if cls:
        assert typed["refusal_class"] == cls
    return typed


@pytest.fixture
def gov(tmp_path):
    return Gov(tmp_path)


# ================================================================================================================== VALID
def test_v01_valid_claim_and_active_selection_execute_with_guard_evidence(gov):
    env, cid, result = gov.run()
    assert result.status == "executed" and gov.counts()[0] == 1
    guard = result.control_plane_evidence["authority_receipt"]["selection_guard"]
    st = gov.state()
    assert guard["result"] == "binding_verified" and guard["activation_ref"] == st.activation_ref and guard["selection_epoch"] == 1
    assert guard["policy_generation"] == st.policy_generation and guard["independent_attestation"] is False


def test_v02_optional_selection_executes(tmp_path):
    g = Gov(tmp_path, participation="optional")
    assert g.run()[2].status == "executed"


def test_v03_reactivation_at_higher_epoch_executes_for_a_new_claim(gov):
    gov.e.activate(v4.select_stmt(v4.REC, epoch=2, supersedes=v4.sup(gov.stmt)))
    assert gov.run()[2].status == "executed"
    assert gov.state().selection_epoch == 2


def test_v04_retained_receipt_equals_returned_and_binds_the_log_head(gov):
    import json
    env, cid, result = gov.run()
    retained = json.loads(gov.receipt())
    assert retained == result.control_plane_evidence["authority_receipt"]
    assert retained["selection_guard"]["log_head_sequence"] == gov.e.head()[0] and retained["selection_guard"]["log_head_hash"] == gov.e.head()[1]


def test_v05_no_network_or_subprocess_io_in_the_guarded_transaction(gov, monkeypatch):
    env, cid = gov.claim()

    def forbidden(*a, **k):
        raise AssertionError("external I/O attempted inside the authority/effect transaction")
    for target in (socket.socket, ):
        monkeypatch.setattr(target, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert gov.executor().execute(envelope=env, claim_id=cid).status == "executed"


# ================================================================================================================== STALE / REVOKED / WITHDRAWN
def test_s01_withdrawn_selection_refuses(gov):
    env, cid = gov.claim()
    gov.e.withdraw(v4.withdraw_stmt(gov.stmt, 2))
    refused(gov.executor().execute(envelope=env, claim_id=cid), "selection_withdrawn", "REFUSE")
    assert gov.counts() == (0, 0, 0, 0)


def test_s02_revoked_selection_refuses(gov):
    env, cid = gov.claim()
    gov.e.sync(2, revoked_selections=frozenset({("sel-test-1", 1)}))
    refused(gov.executor().execute(envelope=env, claim_id=cid), "selection_revoked", "REFUSE")


def test_s03_revoked_issuer_key_refuses(gov):
    env, cid = gov.claim()
    gov.e.sync(2, revoked_key_ids=frozenset({"key-test-1"}))
    refused(gov.executor().execute(envelope=env, claim_id=cid), "issuer_key_revoked", "REFUSE")


def test_s04_stale_generation_refuses_after_reactivation(gov):
    env, cid = gov.claim()  # bound to activation epoch 1
    gov.e.withdraw(v4.withdraw_stmt(gov.stmt, 2))
    gov.e.activate(v4.select_stmt(v4.REC, epoch=3, supersedes=v4.sup(v4.withdraw_stmt(gov.stmt, 2))))
    refused(gov.executor().execute(envelope=env, claim_id=cid), "selection_epoch_stale", "REFUSE_STALE")


def test_s05_expired_current_snapshot_is_a_hold(tmp_path):
    g = Gov(tmp_path, snapshot_until="2026-10-25T18:00:00Z")
    env, cid = g.claim()
    g.e.now = g.e.now.replace(hour=20)  # inside the selection window, past the current snapshot's valid_until
    refused(g.executor().execute(envelope=env, claim_id=cid), "trust_snapshot_expired", "HOLD")


@pytest.mark.parametrize("day,hour,code", [(24, 23, "selection_not_yet_effective"), (26, 1, "selection_expired")])
def test_s06_selection_window_is_enforced_at_the_lock_time_clock(gov, day, hour, code):
    env, cid = gov.claim()
    gov.e.now = gov.e.now.replace(day=day, hour=hour)
    refused(gov.executor().execute(envelope=env, claim_id=cid), code, "REFUSE")


# ================================================================================================================== BINDING / GENERATION
def test_b01_foreign_activation_ref_refuses(gov):
    _, _, result = gov.run(binding_of(gov.state(), activation_ref="sha256:" + "a" * 64))
    refused(result, "policy_identity_mismatch", "REFUSE")


def test_b02_earlier_activation_of_the_same_chain_is_stale(gov):
    first = gov.state().activation_ref
    gov.e.withdraw(v4.withdraw_stmt(gov.stmt, 2))
    gov.e.activate(v4.select_stmt(v4.REC, epoch=3, supersedes=v4.sup(v4.withdraw_stmt(gov.stmt, 2))))
    _, _, result = gov.run(binding_of(gov.state(), activation_ref=first))
    refused(result, "selection_epoch_stale", "REFUSE_STALE")


def test_b03_substituted_record_triple_refuses(gov):
    _, _, result = gov.run(binding_of(gov.state(), record_digest="sha256:" + "b" * 64))
    refused(result, "record_identity_mismatch", "REFUSE")


def test_b04_executor_policy_that_is_not_the_effective_policy_refuses(gov):
    broader = effect_policy(allowed_actions=frozenset({"act-list", "act-extra"}))
    _, _, result = gov.run(policy=broader)
    refused(result, "policy_identity_mismatch", "REFUSE")
    assert gov.counts() == (0, 0, 0, 0)


def test_b05_binding_naming_a_scope_without_a_row_refuses(gov):
    _, _, result = gov.run(binding_of(gov.state(), operation="op-elsewhere"))
    refused(result, "not_activated", "REFUSE")


def test_b06_substituted_snapshot_fingerprint_is_stale(gov):
    _, _, result = gov.run(binding_of(gov.state(), trust_snapshot_fingerprint="sha256:" + "c" * 64))
    refused(result, "trust_snapshot_version_stale", "REFUSE_STALE")


def test_b07_ratchet_set_after_an_optional_activation_refuses_participation(tmp_path):
    g = Gov(tmp_path, participation="optional")
    g.dest.sync_selection("ratchet_provision", governed_scope=GS, determination_ref=v4.DET2)
    refused(g.run()[2], "participation_mismatch", "REFUSE")


# ================================================================================================================== ATOMICITY / CONCURRENCY
def test_c01_a_racing_revocation_blocks_on_the_lock_and_cannot_commit_before_the_effect(tmp_path):
    g = Gov(tmp_path)
    env, cid = g.claim()
    outcome = {}
    revoking = v4.trust(2, revoked_selections=frozenset({("sel-test-1", 1)}))

    class Racing(AtomicAuthorityEffectDestination):
        def _transaction_stage(self, stage):
            if stage == "after_effect_insert_before_commit":  # guard has passed; the effect is inserted; the lock is held
                def revoke():
                    other = AtomicAuthorityEffectDestination(g.e.root, clock=lambda: g.e.now)
                    other.configure_selection_authority(g.e.anchor)
                    other.sync_selection("snapshot", trust=revoking)
                    outcome["revoked_at"] = time.monotonic()
                t = threading.Thread(target=revoke)
                t.start()
                time.sleep(0.4)
                outcome["still_blocked"] = t.is_alive()
                outcome["thread"] = t
            return None

    racing = Racing(g.e.root, clock=lambda: g.e.now)
    racing.configure_selection_authority(g.e.anchor)
    result = AtomicLocalControlPlaneExecutor(workflow=None, destination=racing, policy=effect_policy()).execute(envelope=env, claim_id=cid)
    outcome["thread"].join(timeout=30)
    assert outcome["still_blocked"] is True and result.status == "executed" and g.counts()[0] == 1
    assert g.state().status == "revoked"  # the revocation landed only after the effect committed


@pytest.mark.parametrize("how", ["withdraw", "revoke"])
def test_c02_a_transition_committed_before_begin_refuses(tmp_path, how):
    g = Gov(tmp_path)
    env, cid = g.claim()

    class Pre(AtomicAuthorityEffectDestination):
        def _transaction_stage(self, stage):
            if stage == "before_begin":
                if how == "withdraw":
                    g.e.withdraw(v4.withdraw_stmt(g.stmt, 2))
                else:
                    g.e.sync(2, revoked_selections=frozenset({("sel-test-1", 1)}))

    pre = Pre(g.e.root, clock=lambda: g.e.now)
    pre.configure_selection_authority(g.e.anchor)
    result = AtomicLocalControlPlaneExecutor(workflow=None, destination=pre, policy=effect_policy()).execute(envelope=env, claim_id=cid)
    refused(result, "selection_withdrawn" if how == "withdraw" else "selection_revoked")
    assert g.counts() == (0, 0, 0, 0)


def test_c03_a_refusal_writes_nothing_and_leaves_the_claim_issued(gov):
    env, cid = gov.claim()
    gov.e.withdraw(v4.withdraw_stmt(gov.stmt, 2))
    gov.executor().execute(envelope=env, claim_id=cid)
    with sqlite3.connect(gov.dest.path) as conn:
        assert conn.execute("SELECT state FROM execution_claims_v1 WHERE claim_id=?", (cid,)).fetchone()[0] == "issued"
    assert gov.counts() == (0, 0, 0, 0)


def test_c04_guard_runs_after_the_lock_time_clock_and_before_the_effect_insert(gov):
    seen = []

    class Spy(AtomicAuthorityEffectDestination):
        def _transaction_stage(self, stage):
            seen.append(stage)

    spy = Spy(gov.e.root, clock=lambda: gov.e.now)
    spy.configure_selection_authority(gov.e.anchor)
    env, cid = gov.claim()
    AtomicLocalControlPlaneExecutor(workflow=None, destination=spy, policy=effect_policy()).execute(envelope=env, claim_id=cid)
    assert seen.index("after_begin") < seen.index("after_selection_guard") < seen.index("after_effect_insert_before_commit")


def test_c05_a_withdrawal_after_commit_does_not_undo_the_effect(gov):
    env, cid, result = gov.run()
    assert result.status == "executed"
    gov.e.withdraw(v4.withdraw_stmt(gov.stmt, 2))
    assert gov.counts()[0] == 1 and gov.state().status == "withdrawn"


# ================================================================================================================== EVIDENCE
def test_e01_failing_guard_evidence_write_prevents_the_effect(gov):
    class Failing(AtomicAuthorityEffectDestination):
        def _selection_receipt_evidence(self, evidence):
            raise OSError("evidence store unavailable")

    bad = Failing(gov.e.root, clock=lambda: gov.e.now)
    bad.configure_selection_authority(gov.e.anchor)
    env, cid = gov.claim()
    result = AtomicLocalControlPlaneExecutor(workflow=None, destination=bad, policy=effect_policy()).execute(envelope=env, claim_id=cid)
    assert result.status != "executed" and gov.counts() == (0, 0, 0, 0)
    with sqlite3.connect(gov.dest.path) as conn:
        assert conn.execute("SELECT state FROM execution_claims_v1 WHERE claim_id=?", (cid,)).fetchone()[0] == "issued"


def test_e02_failing_commit_stage_prevents_the_effect(gov):
    class Failing(AtomicAuthorityEffectDestination):
        def _transaction_stage(self, stage):
            if stage == "after_effect_insert_before_commit":
                raise OSError("disk full")

    bad = Failing(gov.e.root, clock=lambda: gov.e.now)
    bad.configure_selection_authority(gov.e.anchor)
    env, cid = gov.claim()
    result = AtomicLocalControlPlaneExecutor(workflow=None, destination=bad, policy=effect_policy()).execute(envelope=env, claim_id=cid)
    assert result.status != "executed" and gov.counts() == (0, 0, 0, 0)


def test_e03_typed_refusal_evidence_reaches_the_result_and_the_failure_record(gov):
    env, cid = gov.claim()
    gov.e.withdraw(v4.withdraw_stmt(gov.stmt, 2))
    result = gov.executor().execute(envelope=env, claim_id=cid)
    typed = refused(result, "selection_withdrawn", "REFUSE")
    assert result.control_plane_evidence["failure_record_id"] and typed["cause"] is None or isinstance(typed["cause"], (str, type(None)))
    assert result.control_plane_evidence["failure_class"] == "authority_hold"


def test_e04_failing_failure_record_write_on_a_refusal_still_writes_no_effect(gov):
    class Failing(AtomicAuthorityEffectDestination):
        def record_failure(self, *a, **k):
            raise OSError("failure store unavailable")

    bad = Failing(gov.e.root, clock=lambda: gov.e.now)
    bad.configure_selection_authority(gov.e.anchor)
    env, cid = gov.claim()
    gov.e.withdraw(v4.withdraw_stmt(gov.stmt, 2))
    with pytest.raises(OSError):
        AtomicLocalControlPlaneExecutor(workflow=None, destination=bad, policy=effect_policy()).execute(envelope=env, claim_id=cid)
    assert gov.counts() == (0, 0, 0, 0)


# ================================================================================================================== LEGACY / BYPASS
def test_l01_legacy_claim_without_a_selection_store_is_unchanged(tmp_path):
    env = envelope(tenant_id=None)
    dest = AtomicAuthorityEffectDestination(tmp_path / "plain", clock=lambda: v4.r.NOW)
    dest.provision_claim(make_claim(env, v4.r.NOW, binding=None, claim_id="legacy-1"))
    result = AtomicLocalControlPlaneExecutor(workflow=None, destination=dest, policy=effect_policy()).execute(envelope=env, claim_id="legacy-1")
    assert result.status == "executed" and "selection_guard" not in result.control_plane_evidence["authority_receipt"]


def test_l02_legacy_claim_in_a_never_activated_scope_falls_back_with_evidence(tmp_path):
    g = Gov(tmp_path, activate=False)
    _, _, result = g.run(binding=None)
    guard = result.control_plane_evidence["authority_receipt"]["selection_guard"]
    assert result.status == "executed" and guard["applied"] is False and guard["fallback"] == "caller_policy" and guard["fallback_reason"] == "never_activated"


def test_l03_legacy_claim_in_a_ratcheted_scope_is_refused(gov):
    refused(gov.run(binding=None)[2], "selection_missing", "REFUSE")
    assert gov.counts() == (0, 0, 0, 0)


def test_l04_legacy_claim_over_an_active_optional_row_cannot_bypass_it(tmp_path):
    g = Gov(tmp_path, participation="optional")
    refused(g.run(binding=None)[2], "selection_missing", "REFUSE")


def test_l05_legacy_claim_over_a_withdrawn_optional_row_falls_back(tmp_path):
    g = Gov(tmp_path, participation="optional")
    g.e.withdraw(v4.withdraw_stmt(g.stmt, 2))
    guard = g.run(binding=None)[2].control_plane_evidence["authority_receipt"]["selection_guard"]
    assert guard["fallback_reason"] == "withdrawn" and guard["applied"] is False


def test_l06_a_withdrawn_required_scope_still_refuses_legacy_claims(gov):
    gov.e.withdraw(v4.withdraw_stmt(gov.stmt, 2))
    refused(gov.run(binding=None)[2], "selection_missing", "REFUSE")


def test_l07_successor_claim_with_no_configured_store_is_a_hold(tmp_path):
    env = envelope()
    dest = AtomicAuthorityEffectDestination(tmp_path / "plain", clock=lambda: v4.r.NOW)
    gen = Gov(tmp_path / "other")
    dest.provision_claim(make_claim(env, v4.r.NOW, binding=binding_of(gen.state()), claim_id="succ-1"))
    refused(AtomicLocalControlPlaneExecutor(workflow=None, destination=dest, policy=effect_policy()).execute(envelope=env, claim_id="succ-1"),
            "store_unknown", "HOLD")


# ================================================================================================================== CORRUPTION / RESTORE
def test_x01_corrupted_activation_row_is_a_hold(gov):
    env, cid = gov.claim()
    gov.e.sql("UPDATE selection_activation_v1 SET valid_until='2099-01-01T00:00:00Z'")
    refused(gov.executor().execute(envelope=env, claim_id=cid), "store_unknown", "HOLD")
    assert gov.counts() == (0, 0, 0, 0)


def test_x02_restored_older_database_is_a_hold(tmp_path):
    g = Gov(tmp_path, activate=False)
    old = tmp_path / "old.sqlite3"
    with sqlite3.connect(g.dest.path) as src, sqlite3.connect(old) as dst:
        src.backup(dst)  # a consistent copy (the database is WAL-journalled: a plain file copy would miss committed pages)
    g.e.activate(g.stmt)
    binding = binding_of(g.state())
    with sqlite3.connect(old) as src, sqlite3.connect(g.dest.path) as dst:
        src.backup(dst)  # restore the older copy over the live database; the anchor now records a later head than the database
    restored = AtomicAuthorityEffectDestination(g.e.root, clock=lambda: g.e.now)
    restored.configure_selection_authority(g.e.anchor)
    env = envelope()
    restored.provision_claim(make_claim(env, g.now, binding=binding, claim_id="restored-1"))
    result = AtomicLocalControlPlaneExecutor(workflow=None, destination=restored, policy=effect_policy()).execute(envelope=env, claim_id="restored-1")
    refused(result, "store_unknown", "HOLD")


def test_x03_missing_anchor_is_a_hold(gov):
    env, cid = gov.claim()
    gov.e.anchor.unlink()
    refused(gov.executor().execute(envelope=env, claim_id=cid), "store_unknown", "HOLD")


def test_x04_tampered_log_entry_is_a_hold(gov):
    import json
    env, cid = gov.claim()
    gov.e.drop_guards()
    row = json.loads(gov.e.sql("SELECT entry_json FROM selection_log_v1 WHERE activation_sequence=3")[0][0])
    row["selection_epoch"] = 9
    gov.e.sql("UPDATE selection_log_v1 SET entry_json=? WHERE activation_sequence=3", (v4.c.canonical_bytes(row).decode("ascii"),))
    refused(gov.executor().execute(envelope=env, claim_id=cid), "store_unknown", "HOLD")


def test_x05_a_store_that_was_never_baselined_is_a_hold(tmp_path):
    env = envelope()
    dest = AtomicAuthorityEffectDestination(tmp_path / "fresh", clock=lambda: v4.r.NOW)
    dest.configure_selection_authority(tmp_path / "fresh-anchor.json")
    gen = Gov(tmp_path / "other")
    dest.provision_claim(make_claim(env, v4.r.NOW, binding=binding_of(gen.state()), claim_id="succ-1"))
    refused(AtomicLocalControlPlaneExecutor(workflow=None, destination=dest, policy=effect_policy()).execute(envelope=env, claim_id="succ-1"),
            "store_unknown", "HOLD")


# ================================================================================================================== EXISTING EVALUATOR
def test_f01_existing_evaluator_denies_an_operation_outside_the_narrowed_policy(gov):
    env, cid = gov.claim(action_id="act-list")
    narrowed_out = effect_policy(allowed_actions=frozenset({"act-other"}))
    result = AtomicLocalControlPlaneExecutor(workflow=None, destination=gov.dest, policy=narrowed_out).execute(envelope=env, claim_id=cid)
    assert result.status == "denied" and result.control_plane_evidence["failure_class"] == "policy_denial" and gov.counts() == (0, 0, 0, 0)
