from __future__ import annotations

import dataclasses
import json
import multiprocessing as mp
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

try:
    import agent_control_plane.local_authority_effect  # noqa: F401
except ModuleNotFoundError:
    pytestmark = pytest.mark.skip(
        reason="Worker21 atomic profile requires proposed Control Plane local-authority-effect contract"
    )

from engine.local_authority_effect import (
    AtomicAuthorityEffectDestination,
    AtomicLocalControlPlaneExecutor,
)
from engine.safe_executor import (
    DurableRefundDestination,
    ExecutionEnvelope,
    ExecutionOperation,
    LocalExecutionPolicy,
    snapshot_envelope,
)

BASE = datetime(2026, 10, 7, 16, 0, tzinfo=timezone.utc)
INSTITUTION = "urn:cognous:institution:test"
DOMAIN = "refunds"
GRANT = "urn:cognous:grant:atomic"
APPROVAL = "urn:cognous:approval:atomic"
POLICY_REF = "urn:cognous:policy:atomic"
EVIDENCE = "urn:cognous:evidence:atomic"


class MutableClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def operation(*, effect_no=1, grant=GRANT):
    payload = {"reason": "duplicate", "effect_no": effect_no}
    from engine.safe_executor import commitment
    return ExecutionOperation(
        actor="urn:cognous:identity:agent",
        principal="urn:cognous:principal:service",
        institution_id=INSTITUTION,
        authority_domain=DOMAIN,
        manifest_id="manifest-1",
        manifest_version="1.1",
        manifest_digest="sha256:" + "1" * 64,
        proposal_commitment="sha256:" + "2" * 64,
        action_id="urn:cognous:action:refund",
        adapter_id="urn:cognous:adapter:refund",
        target=f"urn:cognous:synthetic-account:{effect_no}",
        payload=payload,
        payload_commitment=commitment(payload),
        requested_permissions=("refund.issue",),
        amount=50.0,
        unit="USD",
        effects=1,
        authority_context_id="urn:cognous:authority-context:test",
        requirement_id="urn:cognous:requirement:test",
        grant_id=grant,
        grant_revision="1",
        effective_max_effects=1,
    )


def envelope(*, effect_no=1, effect_id=None, grant=GRANT):
    op = operation(effect_no=effect_no, grant=grant)
    return ExecutionEnvelope(
        version="0.2.0",
        decision_id=f"decision-{effect_no}",
        effect_id=effect_id or f"effect-{effect_no}",
        operation=op,
    )


def make_claim(env, *, claim_id="claim-1", budget_id="budget-1", max_effects=1,
               expires_at=None):
    from agent_control_plane.bounded import commitment
    from agent_control_plane.local_authority_effect import (
        LOCAL_AUTHORITY_EFFECT_PROFILE,
        LocalExecutionClaim,
    )

    snap = snapshot_envelope(env)
    approval_state = [{
        "approval_ref": APPROVAL,
        "role_id": "urn:cognous:role:reviewer",
        "approver": "urn:cognous:principal:human",
        "status": "active",
        "grant_id": snap.operation.grant_id,
        "grant_revision": snap.operation.grant_revision,
        "proposal_commitment": snap.operation.proposal_commitment,
        "policy_versions": [{"ref": POLICY_REF, "version": "1"}],
        "observed_at": BASE.isoformat(),
    }]
    policy_state = [{
        "ref": POLICY_REF,
        "version": "1",
        "status": "active",
        "observed_at": BASE.isoformat(),
    }]
    evidence_state = [{
        "obligation_id": EVIDENCE,
        "source_ref": "urn:cognous:source:entitlement",
        "required": True,
        "kind": "authorization",
        "max_age_seconds": 300,
        "unknown_behavior": "hold_effect",
        "state": "current",
        "observed_at": BASE.isoformat(),
    }]
    authority_state = {
        "grant_id": snap.operation.grant_id,
        "grant_revision": snap.operation.grant_revision,
        "grant_status": "active",
        "requirement_commitment": "sha256:" + "3" * 64,
        "approvals": approval_state,
        "policies": policy_state,
        "evidence": evidence_state,
    }
    raw = {
        "profile": LOCAL_AUTHORITY_EFFECT_PROFILE,
        "claim_id": claim_id,
        "decision_id": env.decision_id,
        "effect_id": env.effect_id,
        "institution_id": snap.operation.institution_id,
        "authority_domain": snap.operation.authority_domain,
        "actor": snap.operation.actor,
        "principal": snap.operation.principal,
        "grant_id": snap.operation.grant_id,
        "grant_revision": snap.operation.grant_revision,
        "grant_status": "active",
        "manifest_id": snap.operation.manifest_id,
        "manifest_version": snap.operation.manifest_version,
        "manifest_digest": snap.operation.manifest_digest,
        "action_id": snap.operation.action_id,
        "adapter_id": snap.operation.adapter_id,
        "target": snap.operation.target,
        "payload_commitment": snap.operation.payload_commitment,
        "requested_permissions": list(snap.operation.requested_permissions),
        "amount": snap.operation.amount,
        "unit": snap.operation.unit,
        "effects": snap.operation.effects,
        "authority_context_id": snap.operation.authority_context_id,
        "requirement_id": snap.operation.requirement_id,
        "requirement_commitment": "sha256:" + "3" * 64,
        "operation_commitment": AtomicAuthorityEffectDestination._operation_commitment(snap),
        "approval_state": approval_state,
        "approval_state_commitment": commitment(approval_state),
        "policy_state": policy_state,
        "policy_state_commitment": commitment(policy_state),
        "evidence_state": evidence_state,
        "evidence_state_commitment": commitment(evidence_state),
        "authority_state_commitment": commitment(authority_state),
        "budget_id": budget_id,
        "max_effects": max_effects,
        "not_before": (BASE - timedelta(minutes=1)).isoformat(),
        "expires_at": (expires_at or (BASE + timedelta(hours=1))).isoformat(),
        "issued_at": BASE.isoformat(),
        "decision_input_commitment": None,
        "decision_input_profile_version": None,
        "source": "trusted_control_plane_workflow",
        "authorizing_by_possession": False,
    }
    raw["claim_commitment"] = commitment(raw)
    return LocalExecutionClaim.model_validate(raw)


def policy(op):
    return LocalExecutionPolicy(
        allowed_institutions=frozenset({op.institution_id}),
        allowed_authority_domains=frozenset({op.authority_domain}),
        allowed_adapters=frozenset({op.adapter_id}),
        allowed_actions=frozenset({op.action_id}),
        allowed_target_prefixes=("urn:cognous:synthetic-account:",),
        allowed_units=frozenset({"USD"}),
        max_amount=1000.0,
        max_effects=1,
    )


def setup_atomic(tmp_path, *, env=None, claim_id="claim-1", budget_id="budget-1",
                 max_effects=1, clock=None):
    clock = clock or MutableClock(BASE)
    env = env or envelope()
    destination = AtomicAuthorityEffectDestination(tmp_path, clock=clock)
    claim = make_claim(
        env, claim_id=claim_id, budget_id=budget_id, max_effects=max_effects
    )
    destination.provision_claim(claim)
    return clock, env, destination, claim


def effect_rows(destination):
    with sqlite3.connect(destination.path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM effects ORDER BY effect_id")]


@pytest.mark.parametrize(
    ("mutator", "needle"),
    [
        (lambda d, c: d.set_grant_status(c.grant_id, status="revoked"), "grant state invalid"),
        (lambda d, c: d.set_approval_status(APPROVAL, status="revoked"), "approval state invalid"),
        (lambda d, c: d.set_policy_state(POLICY_REF, version="2"), "policy state invalid"),
        (lambda d, c: d.set_evidence_state(EVIDENCE, state="stale"), "evidence state invalid"),
    ],
)
def test_invalidation_before_transaction_prevents_effect(tmp_path, mutator, needle):
    _, env, destination, claim = setup_atomic(tmp_path)
    mutator(destination, claim)
    result = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=policy(env.operation)
    ).execute(envelope=env, claim_id=claim.claim_id)
    assert result.status == "denied"
    assert needle in result.error
    assert destination.claim_state(claim.claim_id) == "issued"
    assert effect_rows(destination) == []


def test_execution_first_then_revocation_preserves_history(tmp_path):
    _, env, destination, claim = setup_atomic(tmp_path)
    result = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=policy(env.operation)
    ).execute(envelope=env, claim_id=claim.claim_id)
    assert result.status == "executed"
    assert destination.claim_state(claim.claim_id) == "consumed"
    before = effect_rows(destination)
    destination.set_grant_status(claim.grant_id, status="revoked")
    assert effect_rows(destination) == before
    assert destination.claim_state(claim.claim_id) == "consumed"


def test_grant_expiry_and_evidence_expiry_are_checked_after_lock_acquisition(tmp_path):
    clock, env, destination, claim = setup_atomic(tmp_path)
    clock.value = datetime.fromisoformat(claim.expires_at) + timedelta(microseconds=1)
    denied = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=policy(env.operation)
    ).execute(envelope=env, claim_id=claim.claim_id)
    assert denied.status == "denied"
    assert "validity" in denied.error
    assert effect_rows(destination) == []

    second = tmp_path / "evidence"
    clock2, env2, destination2, claim2 = setup_atomic(second)
    clock2.value = BASE + timedelta(seconds=301)
    denied2 = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination2, policy=policy(env2.operation)
    ).execute(envelope=env2, claim_id=claim2.claim_id)
    assert denied2.status == "denied"
    assert "evidence expired" in denied2.error
    assert effect_rows(destination2) == []


def test_unchanged_authority_commits_claim_budget_and_effect_together(tmp_path):
    _, env, destination, claim = setup_atomic(tmp_path)
    result = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=policy(env.operation)
    ).execute(envelope=env, claim_id=claim.claim_id)
    assert result.status == "executed"
    assert destination.claim_state(claim.claim_id) == "consumed"
    assert destination.budget_state(claim.budget_id) == {"max_effects": 1, "used_effects": 1}
    assert len(effect_rows(destination)) == 1


def test_operation_substitution_fails_without_consumption(tmp_path):
    _, env, destination, claim = setup_atomic(tmp_path)
    changed = dataclasses.replace(env, operation=dataclasses.replace(env.operation, target="urn:cognous:synthetic-account:other"))
    result = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=policy(env.operation)
    ).execute(envelope=changed, claim_id=claim.claim_id)
    assert result.status == "denied"
    assert "operation mismatch" in result.error
    assert destination.claim_state(claim.claim_id) == "issued"
    assert effect_rows(destination) == []


def test_missing_authoritative_state_fails_closed(tmp_path):
    _, env, destination, claim = setup_atomic(tmp_path)
    destination.remove_authoritative_state("evidence", EVIDENCE)
    result = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=policy(env.operation)
    ).execute(envelope=env, claim_id=claim.claim_id)
    assert result.status == "denied"
    assert "evidence state unavailable" in result.error
    assert effect_rows(destination) == []


def test_legacy_write_path_is_blocked_only_on_opted_in_destination(tmp_path):
    _, env, destination, _ = setup_atomic(tmp_path / "atomic")
    snap = snapshot_envelope(env)
    legacy_view = DurableRefundDestination(tmp_path / "atomic")
    with pytest.raises(PermissionError, match="requires atomic claim execution"):
        legacy_view.commit(snap)
    assert effect_rows(destination) == []

    legacy = DurableRefundDestination(tmp_path / "legacy")
    ack = legacy.commit(snap)
    assert ack["duplicate"] is False
    assert legacy.observe(env.effect_id)["state"] == "applied"


def test_lost_ack_reconciles_original_effect_and_never_reopens_claim(tmp_path):
    _, env, destination, claim = setup_atomic(tmp_path)
    executor = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=policy(env.operation)
    )
    result = executor.execute(envelope=env, claim_id=claim.claim_id, simulate="lost_ack")
    assert result.status == "unknown"
    assert destination.claim_state(claim.claim_id) == "consumed"
    assert len(effect_rows(destination)) == 1

    reopened = AtomicAuthorityEffectDestination(tmp_path, clock=lambda: BASE)
    recovery = reopened.reconcile_claim(claim.claim_id, snapshot_envelope(env))
    assert recovery["status"] == "applied"
    assert recovery["retry_eligible"] is False
    again = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=reopened, policy=policy(env.operation)
    ).execute(envelope=env, claim_id=claim.claim_id)
    assert again.status == "denied"
    assert len(effect_rows(reopened)) == 1



def test_reconcile_rejects_effect_from_another_claim_and_wrong_operation(tmp_path):
    _, env_a, destination, claim_a = setup_atomic(
        tmp_path, claim_id="claim-a", budget_id="budget-a"
    )
    executor = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=policy(env_a.operation)
    )
    first = executor.execute(envelope=env_a, claim_id=claim_a.claim_id)
    assert first.status == "executed"

    # Retain an unrelated effect B in the same destination. Recovery for claim A
    # must not classify B as A's applied effect.
    with sqlite3.connect(destination.path) as conn:
        conn.execute(
            """INSERT INTO effects
            (effect_id,operation_digest,grant_id,target,amount,unit,payload_json,state)
            VALUES(?,?,?,?,?,?,?,?)""",
            (
                "effect-b",
                "sha256:" + "b" * 64,
                "urn:cognous:grant:b",
                "urn:cognous:synthetic-account:b",
                1.0,
                "USD",
                '{"reason":"other"}',
                "applied",
            ),
        )

    wrong_effect = destination.reconcile_claim(claim_a.claim_id, snapshot_envelope(dataclasses.replace(env_a, effect_id="effect-b")))
    assert wrong_effect["status"] == "hold"
    assert wrong_effect["retry_eligible"] is False
    assert wrong_effect["reason"] == "claim_effect_binding_mismatch"

    correct = destination.reconcile_claim(claim_a.claim_id, snapshot_envelope(env_a))
    assert correct["status"] == "applied"
    assert correct["effect_id"] == env_a.effect_id
    assert correct["operation_digest"] == snapshot_envelope(env_a).operation.digest

    # A retained row with the right effect ID but wrong operation digest also
    # cannot establish applied/partial for the claim.
    with sqlite3.connect(destination.path) as conn:
        conn.execute(
            "UPDATE effects SET operation_digest=? WHERE effect_id=?",
            ("sha256:" + "c" * 64, env_a.effect_id),
        )
    wrong_operation = destination.reconcile_claim(claim_a.claim_id, snapshot_envelope(env_a))
    assert wrong_operation["status"] == "hold"
    assert wrong_operation["retry_eligible"] is False
    assert wrong_operation["reason"] == "retained_effect_operation_binding_mismatch"


@pytest.mark.parametrize("mutation", ["decision_id", "target", "amount", "payload"])
def test_executor_reconcile_rejects_substituted_envelope_with_original_effect_id(tmp_path, mutation):
    _, env, destination, claim = setup_atomic(tmp_path)
    executor = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=policy(env.operation)
    )
    executed = executor.execute(envelope=env, claim_id=claim.claim_id)
    assert executed.status == "executed"

    if mutation == "decision_id":
        substituted = dataclasses.replace(env, decision_id="decision-substituted")
        expected = "claim_decision_binding_mismatch"
    elif mutation == "target":
        substituted = dataclasses.replace(
            env,
            operation=dataclasses.replace(
                env.operation, target="urn:cognous:synthetic-account:substituted"
            ),
        )
        expected = "claim_operation_binding_mismatch"
    elif mutation == "amount":
        substituted = dataclasses.replace(
            env, operation=dataclasses.replace(env.operation, amount=51.0)
        )
        expected = "claim_operation_binding_mismatch"
    else:
        from engine.safe_executor import commitment
        payload = {"reason": "substituted", "effect_no": 1}
        substituted = dataclasses.replace(
            env,
            operation=dataclasses.replace(
                env.operation,
                payload=payload,
                payload_commitment=commitment(payload),
            ),
        )
        expected = "claim_operation_binding_mismatch"

    recovered = executor.reconcile(claim_id=claim.claim_id, envelope=substituted)
    assert recovered.status == "observed"
    assert recovered.observed_state == "unknown"
    assert recovered.observation["status"] == "hold"
    assert recovered.observation["reason"] == expected
    assert recovered.observation["retry_eligible"] is False


def test_executor_reconcile_preserves_exact_original_envelope_recovery(tmp_path):
    _, env, destination, claim = setup_atomic(tmp_path)
    executor = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=policy(env.operation)
    )
    executed = executor.execute(envelope=env, claim_id=claim.claim_id)
    assert executed.status == "executed"

    recovered = executor.reconcile(claim_id=claim.claim_id, envelope=env)
    assert recovered.status == "observed"
    assert recovered.observed_state == "applied"
    assert recovered.observation["status"] == "applied"
    assert recovered.observation["effect_id"] == env.effect_id
    assert recovered.observation["operation_digest"] == snapshot_envelope(env).operation.digest


def test_provision_rejects_non_active_projected_statuses(tmp_path):
    env = envelope()
    destination = AtomicAuthorityEffectDestination(tmp_path, clock=lambda: BASE)

    approval_claim = make_claim(env, claim_id="approval-bad")
    raw = approval_claim.model_dump(mode="json", exclude_none=False)
    raw["approval_state"][0]["status"] = "revoked"
    from agent_control_plane.bounded import commitment
    raw["approval_state_commitment"] = commitment(raw["approval_state"])
    authority = {
        "grant_id": raw["grant_id"],
        "grant_revision": raw["grant_revision"],
        "grant_status": raw["grant_status"],
        "requirement_commitment": raw["requirement_commitment"],
        "approvals": raw["approval_state"],
        "policies": raw["policy_state"],
        "evidence": raw["evidence_state"],
    }
    raw["authority_state_commitment"] = commitment(authority)
    protected = {k: v for k, v in raw.items() if k != "claim_commitment"}
    raw["claim_commitment"] = commitment(protected)
    with pytest.raises(PermissionError, match="approval projection is not active"):
        destination.provision_claim(raw)

    policy_claim = make_claim(env, claim_id="policy-bad")
    raw2 = policy_claim.model_dump(mode="json", exclude_none=False)
    raw2["policy_state"][0]["status"] = "superseded"
    raw2["policy_state_commitment"] = commitment(raw2["policy_state"])
    authority2 = {
        "grant_id": raw2["grant_id"],
        "grant_revision": raw2["grant_revision"],
        "grant_status": raw2["grant_status"],
        "requirement_commitment": raw2["requirement_commitment"],
        "approvals": raw2["approval_state"],
        "policies": raw2["policy_state"],
        "evidence": raw2["evidence_state"],
    }
    raw2["authority_state_commitment"] = commitment(authority2)
    protected2 = {k: v for k, v in raw2.items() if k != "claim_commitment"}
    raw2["claim_commitment"] = commitment(protected2)
    with pytest.raises(PermissionError, match="policy projection is not active"):
        destination.provision_claim(raw2)


def _execute_process(root, env, claim_id, start, queue, attempt_id):
    destination = AtomicAuthorityEffectDestination(root, clock=lambda: BASE)
    start.wait()
    try:
        value = destination.execute_claim_atomic(
            claim_id, snapshot_envelope(env), attempt_id=attempt_id
        )
        queue.put(("ok", value["observation"]["state"]))
    except BaseException as exc:
        queue.put(("error", str(exc)))


def test_same_claim_concurrent_processes_commit_at_most_once(tmp_path):
    _, env, destination, claim = setup_atomic(tmp_path)
    ctx = mp.get_context("spawn")
    start = ctx.Event()
    queue = ctx.Queue()
    procs = [
        ctx.Process(
            target=_execute_process,
            args=(str(tmp_path), env, claim.claim_id, start, queue, f"attempt-{i}"),
        )
        for i in (1, 2)
    ]
    for proc in procs:
        proc.start()
    start.set()
    for proc in procs:
        proc.join(10)
        assert proc.exitcode == 0
    outcomes = sorted(queue.get(timeout=2)[0] for _ in procs)
    assert outcomes == ["error", "ok"]
    assert destination.claim_state(claim.claim_id) == "consumed"
    assert len(effect_rows(destination)) == 1


def test_competing_effects_share_atomic_budget_across_processes(tmp_path):
    clock = MutableClock(BASE)
    env1 = envelope(effect_no=1, effect_id="effect-a")
    destination = AtomicAuthorityEffectDestination(tmp_path, clock=clock)
    claim1 = make_claim(env1, claim_id="claim-a", budget_id="shared", max_effects=1)
    destination.provision_claim(claim1)

    env2 = envelope(effect_no=2, effect_id="effect-b")
    claim2 = make_claim(env2, claim_id="claim-b", budget_id="shared", max_effects=1)
    destination.provision_claim(claim2)

    ctx = mp.get_context("spawn")
    start = ctx.Event()
    queue = ctx.Queue()
    procs = [
        ctx.Process(target=_execute_process, args=(str(tmp_path), env1, "claim-a", start, queue, "attempt-a")),
        ctx.Process(target=_execute_process, args=(str(tmp_path), env2, "claim-b", start, queue, "attempt-b")),
    ]
    for proc in procs:
        proc.start()
    start.set()
    for proc in procs:
        proc.join(10)
        assert proc.exitcode == 0
    outcomes = sorted(queue.get(timeout=2)[0] for _ in procs)
    assert outcomes == ["error", "ok"]
    assert len(effect_rows(destination)) == 1
    assert destination.budget_state("shared") == {"max_effects": 1, "used_effects": 1}


def _crash_worker(root, env, claim_id, stage, reached):
    class CrashBarrierDestination(AtomicAuthorityEffectDestination):
        def _transaction_stage(self, current):
            if current == stage:
                reached.set()
                while True:
                    reached.wait(60)

    destination = CrashBarrierDestination(root, clock=lambda: BASE)
    destination.execute_claim_atomic(
        claim_id, snapshot_envelope(env), attempt_id=f"crash-{stage}"
    )


@pytest.mark.parametrize(
    ("stage", "expected_claim", "expected_effects"),
    [
        ("after_begin", "issued", 0),
        ("after_effect_insert_before_commit", "issued", 0),
        ("after_commit", "consumed", 1),
    ],
)
def test_process_termination_transaction_boundaries(tmp_path, stage, expected_claim, expected_effects):
    _, env, destination, claim = setup_atomic(tmp_path)
    ctx = mp.get_context("spawn")
    reached = ctx.Event()
    proc = ctx.Process(
        target=_crash_worker,
        args=(str(tmp_path), env, claim.claim_id, stage, reached),
    )
    proc.start()
    assert reached.wait(10)
    proc.terminate()
    proc.join(10)
    assert proc.exitcode is not None

    reopened = AtomicAuthorityEffectDestination(tmp_path, clock=lambda: BASE)
    assert reopened.claim_state(claim.claim_id) == expected_claim
    assert len(effect_rows(reopened)) == expected_effects
    recovery = reopened.reconcile_claim(claim.claim_id, snapshot_envelope(env))
    assert recovery["retry_eligible"] is False
    if expected_effects:
        assert recovery["status"] == "applied"
    else:
        assert recovery["status"] == "hold"


def _expiry_wait_worker(root, env, claim_id, initialized, start, attempting, clock_value, queue):
    class AttemptSignalDestination(AtomicAuthorityEffectDestination):
        def _transaction_stage(self, stage):
            if stage == "before_begin":
                attempting.set()

    destination = AttemptSignalDestination(
        root,
        clock=lambda: datetime.fromtimestamp(clock_value.value, tz=timezone.utc),
    )
    initialized.set()
    start.wait()
    try:
        destination.execute_claim_atomic(
            claim_id, snapshot_envelope(env), attempt_id="expiry-wait"
        )
        queue.put("executed")
    except BaseException as exc:
        queue.put(str(exc))


def test_expiry_is_evaluated_after_waiting_for_transaction_lock(tmp_path):
    expires = BASE + timedelta(seconds=30)
    clock = MutableClock(BASE)
    env = envelope()
    destination = AtomicAuthorityEffectDestination(tmp_path, clock=clock)
    claim = make_claim(env, expires_at=expires)
    destination.provision_claim(claim)

    ctx = mp.get_context("spawn")
    initialized = ctx.Event()
    start = ctx.Event()
    attempting = ctx.Event()
    queue = ctx.Queue()
    clock_value = ctx.Value("d", BASE.timestamp())
    proc = ctx.Process(
        target=_expiry_wait_worker,
        args=(
            str(tmp_path), env, claim.claim_id, initialized, start, attempting,
            clock_value, queue,
        ),
    )
    proc.start()
    assert initialized.wait(10)

    # Acquire the competing transaction only after child initialization is done.
    lock_conn = sqlite3.connect(destination.path, timeout=10, isolation_level=None)
    lock_conn.execute("BEGIN IMMEDIATE")
    start.set()
    assert attempting.wait(10)  # child has reached execute_claim_atomic before BEGIN
    clock_value.value = (expires + timedelta(seconds=1)).timestamp()
    lock_conn.execute("COMMIT")
    lock_conn.close()

    proc.join(10)
    assert proc.exitcode == 0
    outcome = queue.get(timeout=2)
    assert "validity" in outcome
    assert destination.claim_state(claim.claim_id) == "issued"
    assert effect_rows(destination) == []


# ---------------------------------------------------------------------------
# W1 tenant-aware local SQLite refund qualification
# ---------------------------------------------------------------------------

TENANT = "tenant-alpha"


def tenant_envelope(*, tenant_id=TENANT, effect_id="effect-tenant"):
    base = envelope(effect_id=effect_id)
    return dataclasses.replace(
        base,
        decision_id="decision-tenant",
        operation=dataclasses.replace(base.operation, tenant_id=tenant_id),
    )


def make_tenant_claim(env, *, claim_id="claim-tenant", budget_id="budget-tenant"):
    from agent_control_plane.bounded import commitment
    from agent_control_plane.local_authority_effect import TenantLocalExecutionClaim

    historical = make_claim(env, claim_id=claim_id, budget_id=budget_id)
    raw = historical.model_dump(mode="json", exclude_none=False)
    raw["authorization_generation"] = "bounded-authorization-effect/0.2"
    raw["tenant_id"] = env.operation.tenant_id
    for item in raw["approval_state"]:
        item["tenant_id"] = env.operation.tenant_id
    for item in raw["policy_state"]:
        item["tenant_id"] = env.operation.tenant_id
    raw["approval_state_commitment"] = commitment(raw["approval_state"])
    raw["policy_state_commitment"] = commitment(raw["policy_state"])
    authority = {
        "grant_id": raw["grant_id"],
        "grant_revision": raw["grant_revision"],
        "grant_status": raw["grant_status"],
        "requirement_commitment": raw["requirement_commitment"],
        "approvals": raw["approval_state"],
        "policies": raw["policy_state"],
        "evidence": raw["evidence_state"],
    }
    raw["authority_state_commitment"] = commitment(authority)
    raw.pop("claim_commitment", None)
    raw["claim_commitment"] = commitment(raw)
    return TenantLocalExecutionClaim.model_validate(raw)


def setup_tenant_atomic(tmp_path):
    env = tenant_envelope()
    destination = AtomicAuthorityEffectDestination(tmp_path, clock=lambda: BASE)
    claim = make_tenant_claim(env)
    destination.provision_claim(claim)
    return env, destination, claim


def test_tenant_refund_commits_under_exact_tenant(tmp_path):
    env, destination, claim = setup_tenant_atomic(tmp_path)
    result = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=policy(env.operation)
    ).execute(envelope=env, claim_id=claim.claim_id)
    assert result.status == "executed"
    assert destination.claim_state(claim.claim_id) == "consumed"
    assert len(effect_rows(destination)) == 1


@pytest.mark.parametrize("tenant_id", [None, "tenant-beta"])
def test_missing_or_substituted_effect_tenant_is_denied(tmp_path, tenant_id):
    env, destination, claim = setup_tenant_atomic(tmp_path)
    changed = dataclasses.replace(
        env,
        operation=dataclasses.replace(env.operation, tenant_id=tenant_id),
    )
    result = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=policy(env.operation)
    ).execute(envelope=changed, claim_id=claim.claim_id)
    assert result.status == "denied"
    assert "tenant_id" in result.error or "operation mismatch" in result.error
    assert destination.claim_state(claim.claim_id) == "issued"
    assert effect_rows(destination) == []


@pytest.mark.parametrize(
    ("table", "key_column", "key_value"),
    [
        ("authority_grants_v1", "grant_id", GRANT),
        ("authority_approvals_v1", "approval_ref", APPROVAL),
        ("authority_policies_v1", "ref", POLICY_REF),
    ],
)
def test_wrong_tenant_authoritative_state_is_denied_at_effect_time(
    tmp_path, table, key_column, key_value
):
    env, destination, claim = setup_tenant_atomic(tmp_path)
    with sqlite3.connect(destination.path) as conn:
        conn.execute(
            f"UPDATE {table} SET tenant_id=? WHERE {key_column}=?",
            ("tenant-beta", key_value),
        )
    result = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=policy(env.operation)
    ).execute(envelope=env, claim_id=claim.claim_id)
    assert result.status == "denied"
    assert "state invalid" in result.error
    assert destination.claim_state(claim.claim_id) == "issued"
    assert effect_rows(destination) == []


def test_tenant_lost_ack_restart_recovery_preserves_original_tenant_effect(tmp_path):
    env, destination, claim = setup_tenant_atomic(tmp_path)
    executor = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=policy(env.operation)
    )
    result = executor.execute(envelope=env, claim_id=claim.claim_id, simulate="lost_ack")
    assert result.status == "unknown"
    assert destination.claim_state(claim.claim_id) == "consumed"
    assert len(effect_rows(destination)) == 1

    reopened = AtomicAuthorityEffectDestination(tmp_path, clock=lambda: BASE)
    exact = reopened.reconcile_claim(claim.claim_id, snapshot_envelope(env))
    assert exact["status"] == "applied"
    assert exact["retry_eligible"] is False

    wrong = dataclasses.replace(
        env,
        operation=dataclasses.replace(env.operation, tenant_id="tenant-beta"),
    )
    substituted = reopened.reconcile_claim(claim.claim_id, snapshot_envelope(wrong))
    assert substituted["status"] == "hold"
    assert substituted["retry_eligible"] is False


def test_historical_tenant_unaware_claim_remains_supported_without_assurance_upgrade(tmp_path):
    env = envelope(effect_id="effect-historical")
    destination = AtomicAuthorityEffectDestination(tmp_path, clock=lambda: BASE)
    claim = make_claim(env, claim_id="claim-historical", budget_id="budget-historical")
    destination.provision_claim(claim)

    with sqlite3.connect(destination.path) as conn:
        conn.row_factory = sqlite3.Row
        stored = conn.execute(
            "SELECT tenant_id FROM execution_claims_v1 WHERE claim_id=?",
            (claim.claim_id,),
        ).fetchone()
        grant = conn.execute(
            "SELECT tenant_id FROM authority_grants_v1 WHERE grant_id=?",
            (claim.grant_id,),
        ).fetchone()
    assert stored["tenant_id"] is None
    assert grant["tenant_id"] is None

    result = AtomicLocalControlPlaneExecutor(
        workflow=object(), destination=destination, policy=policy(env.operation)
    ).execute(envelope=env, claim_id=claim.claim_id)
    assert result.status == "executed"


def test_w1_tenant_claim_sample_bytes_match_live_model():
    sample_path = Path(__file__).parent / "fixtures" / "w1_tenant_execution_claim_v0_2.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    live = make_tenant_claim(tenant_envelope()).model_dump(mode="json", exclude_none=False)
    assert sample == live
