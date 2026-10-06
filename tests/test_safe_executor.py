from __future__ import annotations

import importlib.util
import json
import multiprocessing
import os
from pathlib import Path

import pytest

from engine.control_plane_adapter import PinnedControlPlaneExecutor
from engine.producer_contract import export_execution_artifacts
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION,
    DurableRefundDestination,
    ExecutionEnvelope,
    ExecutionOperation,
    LocalDestinationExecutor,
    LocalExecutionPolicy,
    commitment,
    snapshot_envelope,
)


def make_operation(**overrides):
    payload = overrides.pop("payload", {"refund_reason": "synthetic-test"})
    base = dict(
        actor="urn:cognous:identity:agent-1",
        principal="urn:cognous:principal:service",
        institution_id="urn:cognous:institution:demo",
        authority_domain="customer-refunds",
        manifest_id="refund-integration-v1-1",
        manifest_version="1.1",
        manifest_digest="sha256:" + "a" * 64,
        proposal_commitment="sha256:" + "b" * 64,
        action_id="refund.issue",
        adapter_id="urn:cognous:adapter:synthetic-refund",
        target="urn:cognous:synthetic-account:customer-1",
        payload=payload,
        payload_commitment=commitment(payload),
        requested_permissions=("refund.issue",),
        amount=50.0,
        unit="USD",
        effects=1,
        authority_context_id="urn:cognous:authority-context:refund-demo",
        requirement_id="urn:cognous:requirement:refund-t1",
        grant_id="urn:cognous:grant:refund-1",
        grant_revision="1",
        effective_max_effects=1,
    )
    base.update(overrides)
    return ExecutionOperation(**base)


def envelope(op=None, decision_id="decision-1", effect_id="effect-1", attempt_id=None):
    return ExecutionEnvelope(
        EXECUTION_ENVELOPE_VERSION,
        decision_id,
        effect_id,
        op or make_operation(),
        attempt_id,
    )


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


def test_deep_snapshot_is_immune_to_nested_mutation(tmp_path):
    original = make_operation(payload={"nested": {"value": "approved"}})
    request = envelope(original)
    frozen = snapshot_envelope(request)
    original.payload["nested"]["value"] = "mutated"
    assert frozen.operation.payload() == {"nested": {"value": "approved"}}
    assert frozen.operation.payload_commitment == commitment(
        {"nested": {"value": "approved"}}
    )


@pytest.mark.parametrize("effects", [0, -1, 1.5, True, False, 2])
def test_single_refund_requires_exact_integer_one_effect(effects):
    with pytest.raises(ValueError):
        snapshot_envelope(envelope(make_operation(effects=effects)))


@pytest.mark.parametrize("amount", [float("inf"), float("-inf"), float("nan"), True, "50"])
def test_amount_must_be_strict_finite_number(amount):
    with pytest.raises(ValueError):
        snapshot_envelope(envelope(make_operation(amount=amount)))



@pytest.mark.parametrize(
    "overrides",
    [
        {"actor": 123},
        {"unit": 123},
        {"requested_permissions": ("refund.issue", "")},
    ],
)
def test_malformed_identifier_unit_and_permission_types_fail_closed(overrides):
    with pytest.raises(ValueError):
        snapshot_envelope(envelope(make_operation(**overrides)))


def test_malformed_attempt_id_is_rejected():
    with pytest.raises(ValueError):
        snapshot_envelope(envelope(make_operation(), attempt_id=123))

def test_attempt_id_reuse_never_overwrites_success(tmp_path):
    op = make_operation()
    destination = DurableRefundDestination(tmp_path / "state")
    executor = LocalDestinationExecutor(destination, policy(op))
    first = executor.execute_snapshot(snapshot_envelope(envelope(op, attempt_id="same")))
    assert first.status == "executed"

    bad = make_operation(payload={"refund_reason": "other"})
    bad_request = envelope(bad, effect_id="effect-1", attempt_id="same")
    denied = executor.execute_snapshot(snapshot_envelope(bad_request))
    assert denied.status == "denied"
    assert denied.attempt_id != "same"
    assert [event["status"] for event in destination.attempt_history("same")] == [
        "attempted",
        "executed",
    ]
    assert destination.attempt_history(denied.attempt_id)[-1]["status"] == "denied"


def test_reconciliation_rejects_different_operation_content(tmp_path):
    op = make_operation()
    destination = DurableRefundDestination(tmp_path / "state")
    executor = LocalDestinationExecutor(destination, policy(op))
    assert executor.execute_snapshot(snapshot_envelope(envelope(op))).status == "executed"
    changed = make_operation(payload={"refund_reason": "changed"})
    with pytest.raises(PermissionError):
        executor.observe_historical(envelope(changed))


def test_symlinked_root_rejected_before_resolution(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(PermissionError):
        DurableRefundDestination(link)


def test_lost_ack_restart_preserves_attempt_history(tmp_path):
    op = make_operation()
    state = tmp_path / "state"
    destination = DurableRefundDestination(state)
    executor = LocalDestinationExecutor(destination, policy(op))
    result = executor.execute_snapshot(
        snapshot_envelope(envelope(op, attempt_id="lost")),
        simulate="crash_after_commit",
    )
    assert result.status == "unknown"
    assert destination.observe("effect-1")["state"] == "applied"
    assert [x["status"] for x in destination.attempt_history("lost")] == [
        "attempted",
        "unknown",
    ]

    restarted = DurableRefundDestination(state)
    observed = LocalDestinationExecutor(restarted, policy(op)).observe_historical(
        envelope(op)
    )
    assert observed.status == "observed"
    assert observed.newly_executed is False
    assert restarted.effect_count(op.grant_id) == 1



def test_legacy_attempt_schema_migrates_without_losing_outcome(tmp_path):
    import sqlite3

    root = tmp_path / "state"
    root.mkdir()
    db = root / "refunds.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE effects (
                effect_id TEXT PRIMARY KEY,
                operation_digest TEXT NOT NULL,
                grant_id TEXT NOT NULL,
                target TEXT NOT NULL,
                amount REAL NOT NULL,
                unit TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL
            );
            CREATE TABLE attempts (
                attempt_id TEXT PRIMARY KEY,
                effect_id TEXT NOT NULL,
                decision_id TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT
            );
            INSERT INTO attempts(attempt_id,effect_id,decision_id,status,error)
            VALUES('legacy-attempt','legacy-effect','legacy-decision','executed',NULL);
            """
        )

    destination = DurableRefundDestination(root)
    history = destination.attempt_history("legacy-attempt")
    assert [event["status"] for event in history] == ["executed"]

def _mp_commit(root: str, effect_id: str, target: str, queue):
    op = make_operation(
        proposal_commitment="sha256:" + effect_id[-1] * 64,
        target=target,
    )
    snap = snapshot_envelope(envelope(op, decision_id=effect_id, effect_id=effect_id))
    result = LocalDestinationExecutor(
        DurableRefundDestination(root), policy(op)
    ).execute_snapshot(snap)
    queue.put(result.status)


def test_separate_processes_share_transactional_limit(tmp_path):
    root = str(tmp_path / "shared")
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    processes = [
        ctx.Process(
            target=_mp_commit,
            args=(
                root,
                "effect-1",
                "urn:cognous:synthetic-account:customer-1",
                queue,
            ),
        ),
        ctx.Process(
            target=_mp_commit,
            args=(
                root,
                "effect-2",
                "urn:cognous:synthetic-account:customer-2",
                queue,
            ),
        ),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    statuses = sorted(queue.get(timeout=2) for _ in processes)
    assert statuses == ["executed", "failed"]
    assert DurableRefundDestination(root).effect_count("urn:cognous:grant:refund-1") == 1


def _load_pinned_helpers():
    cp_root = os.environ.get("MOLTBOT_SAFE_CONTROL_PLANE_ROOT")
    manifest_path = os.environ.get("MOLTBOT_SAFE_MANIFEST_FIXTURE")
    if not cp_root or not manifest_path:
        pytest.skip("pinned Control Plane checkout/Manifest fixture not configured")
    helper_path = Path(cp_root) / "tests" / "test_bounded_authorization.py"
    spec = importlib.util.spec_from_file_location("pinned_cp_helpers", helper_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.FIXTURE = Path(manifest_path)
    return module


def _integrated(tmp_path):
    h = _load_pinned_helpers()
    p = h.proposal()
    resolver = h.resolver_for(p)
    cp_destination = h.LocalRefundDestination(tmp_path / "cp-placeholder.json")
    records = h.BoundedRecordStore(tmp_path / "cp-run.json", "run-1")
    workflow = h.BoundedAuthorizationWorkflow(
        manifest=h.manifest(),
        resolver=resolver,
        destination=cp_destination,
        records=records,
    )
    decision = workflow.decide(p, now=h.NOW)
    assert decision.result == "authorized"
    context = resolver.authority_context(p.authority_context_ref)
    binding = decision.binding
    assert binding is not None
    op = ExecutionOperation(
        actor=p.actor,
        principal=p.principal,
        institution_id=context["institution"]["institution_id"],
        authority_domain=context["institution"]["authority_domain"],
        manifest_id=p.manifest_id,
        manifest_version=p.manifest_version,
        manifest_digest=p.manifest_digest,
        proposal_commitment=h.commitment(
            p.model_dump(mode="json", exclude_none=False)
        ),
        action_id=p.action_id,
        adapter_id=p.adapter_id,
        target=p.target,
        payload=json.loads(json.dumps(p.payload)),
        payload_commitment=p.payload_commitment,
        requested_permissions=tuple(p.requested_permissions),
        amount=p.amount,
        unit=p.unit,
        effects=p.effects,
        authority_context_id=p.authority_context_ref,
        requirement_id=p.requirement_id,
        grant_id=binding.grant_id,
        grant_revision=binding.grant_revision,
        effective_max_effects=binding.effective_max_effects,
    )
    destination = DurableRefundDestination(tmp_path / "moltbot-state")
    executor = PinnedControlPlaneExecutor(
        workflow=workflow,
        destination=destination,
        policy=policy(op),
    )
    request = ExecutionEnvelope(
        EXECUTION_ENVELOPE_VERSION,
        decision.decision_id,
        decision.effect_id,
        op,
    )
    return h, p, resolver, workflow, decision, destination, executor, request


def test_pinned_manifest_fixture_is_actual_manifest_repo_fixture():
    h = _load_pinned_helpers()
    manifest_path = Path(os.environ["MOLTBOT_SAFE_MANIFEST_FIXTURE"])
    control_fixture = Path(os.environ["MOLTBOT_SAFE_CONTROL_PLANE_ROOT"]) / "tests" / "fixtures" / "refund_integration_v1_1.manifest.json"
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == json.loads(
        control_fixture.read_text(encoding="utf-8")
    )
    assert h.manifest()["manifest_version"] == "1.1"


def test_pinned_control_plane_ordinary_execution(tmp_path):
    h, p, resolver, workflow, decision, destination, executor, request = _integrated(
        tmp_path
    )
    result = executor.execute(
        envelope=request, proposal=p, decision=decision, now=h.NOW
    )
    assert result.status == "executed"
    assert result.newly_executed is True
    assert destination.observe(decision.effect_id)["state"] == "applied"
    assert destination.effect_count(decision.binding.grant_id) == 1


@pytest.mark.parametrize(
    "mutation",
    ["revoked", "expired", "policy", "stale_evidence"],
)
def test_pinned_control_plane_current_revalidation_blocks_changed_authority(
    tmp_path, mutation
):
    h, p, resolver, workflow, decision, destination, executor, request = _integrated(
        tmp_path
    )
    grant = resolver.contexts[h.PROFILE]["grant"]
    if mutation == "revoked":
        resolver.statuses[grant["grant_id"]].status = "revoked"
    elif mutation == "expired":
        grant["expires_at"] = (h.NOW.replace(microsecond=0) - h.timedelta(seconds=1)).isoformat()
    elif mutation == "policy":
        resolver.policies[h.POLICY_REF].version = "2.0"
    else:
        resolver.evidence["urn:cognous:evidence:refund-entitlement"].observed_at = (
            h.NOW - h.timedelta(minutes=10)
        ).isoformat()

    result = executor.execute(
        envelope=request, proposal=p, decision=decision, now=h.NOW
    )
    assert result.status == "denied"
    assert destination.observe(decision.effect_id)["state"] == "absent"


def test_pinned_control_plane_operation_substitution_has_no_effect(tmp_path):
    h, p, resolver, workflow, decision, destination, executor, request = _integrated(
        tmp_path
    )
    changed = ExecutionOperation(
        **{
            **request.operation.__dict__,
            "target": "urn:cognous:synthetic-account:attacker",
        }
    )
    result = executor.execute(
        envelope=ExecutionEnvelope(
            request.version, request.decision_id, request.effect_id, changed
        ),
        proposal=p,
        decision=decision,
        now=h.NOW,
    )
    assert result.status == "denied"
    assert destination.observe(decision.effect_id)["state"] == "absent"


def test_mutation_during_trusted_context_lookup_cannot_change_snapshot(tmp_path):
    h, p, resolver, workflow, decision, destination, executor, request = _integrated(
        tmp_path
    )
    original_lookup = resolver.authority_context

    def mutating_lookup(ref):
        request.operation.payload["refund_reason"] = "mutated-after-snapshot"
        return original_lookup(ref)

    resolver.authority_context = mutating_lookup
    result = executor.execute(
        envelope=request, proposal=p, decision=decision, now=h.NOW
    )
    assert result.status == "executed"
    observed = destination.observe(decision.effect_id)
    assert observed["destination_state"]["payload"]["refund_reason"] == "duplicate"



def test_mutation_during_lookup_of_shared_proposal_payload_fails_closed(tmp_path):
    h, p, resolver, workflow, decision, destination, executor, request = _integrated(
        tmp_path
    )
    # Rebuild the request so the caller operation and RuntimeProposal share the
    # same nested payload object, matching the reproduced mutation class.
    shared = p.payload
    shared_operation = ExecutionOperation(
        **{
            **request.operation.__dict__,
            "payload": shared,
            "payload_commitment": h.commitment(shared),
        }
    )
    shared_request = ExecutionEnvelope(
        request.version,
        request.decision_id,
        request.effect_id,
        shared_operation,
    )
    original_lookup = resolver.authority_context

    def mutating_lookup(ref):
        shared["refund_reason"] = "mutated-during-lookup"
        return original_lookup(ref)

    resolver.authority_context = mutating_lookup
    result = executor.execute(
        envelope=shared_request,
        proposal=p,
        decision=decision,
        now=h.NOW,
    )
    assert result.status == "denied"
    assert destination.observe(decision.effect_id)["state"] == "absent"

def test_caller_institution_label_cannot_satisfy_trusted_boundary(tmp_path):
    h, p, resolver, workflow, decision, destination, executor, request = _integrated(
        tmp_path
    )
    changed = ExecutionOperation(
        **{
            **request.operation.__dict__,
            "institution_id": "urn:cognous:institution:caller-claims-this",
        }
    )
    result = executor.execute(
        envelope=ExecutionEnvelope(
            request.version, request.decision_id, request.effect_id, changed
        ),
        proposal=p,
        decision=decision,
        now=h.NOW,
    )
    assert result.status == "denied"
    assert destination.observe(decision.effect_id)["state"] == "absent"



def test_export_historical_absence_observation_without_effect_row(tmp_path):
    op = make_operation()
    request = envelope(op, decision_id="decision-absent", effect_id="effect-absent")
    destination = DurableRefundDestination(tmp_path / "empty-state")
    observed = LocalDestinationExecutor(
        destination, policy(op)
    ).observe_historical(request)

    assert observed.status == "observed"
    assert observed.observed_state == "absent"

    exported = export_execution_artifacts(request, observed, destination)

    assert exported["execution_result"]["status"] == "observed"
    assert exported["execution_result"]["observed_state"] == "absent"
    assert exported["effects"] == []
    assert exported["attempts"] == []
    assert exported["attempt_events"] == []
    assert exported["attempt_identity"] is None
    assert exported["control_plane_attempts"] == []
    assert exported["observations"][0]["effect_id"] == "effect-absent"
    assert exported["observations"][0]["state"] == "absent"
    assert exported["observations"][0]["destination_state"] == {}


def test_export_historical_unknown_observation_without_fabricated_effect(tmp_path):
    op = make_operation()
    request = envelope(op, decision_id="decision-unknown", effect_id="effect-unknown")
    destination = DurableRefundDestination(tmp_path / "empty-state")
    unknown = ExecutionResult(
        status="observed",
        decision_id=request.decision_id,
        effect_id=request.effect_id,
        attempt_id=None,
        attempted=False,
        acknowledged=False,
        observed_state="unknown",
        newly_executed=False,
        observation={
            "effect_id": request.effect_id,
            "state": "unknown",
            "destination_state": {},
        },
    )

    exported = export_execution_artifacts(request, unknown, destination)

    assert exported["effects"] == []
    assert exported["attempts"] == []
    assert exported["observations"][0]["state"] == "unknown"
    assert exported["observations"][0]["destination_state"] == {}


def test_pinned_control_plane_reconciliation_export_preserves_attempt_namespaces(tmp_path):
    h, p, resolver, workflow, decision, destination, executor, request = _integrated(
        tmp_path
    )

    first = executor.execute(
        envelope=request, proposal=p, decision=decision, now=h.NOW
    )
    assert first.status == "executed"
    assert first.attempt_id is not None
    first_executor_attempt_id = first.attempt_id
    assert destination.effect_count(decision.binding.grant_id) == 1

    reconciled = executor.execute(
        envelope=request, proposal=p, decision=decision, now=h.NOW
    )
    assert reconciled.status == "reconciled"
    assert reconciled.newly_executed is False
    assert reconciled.attempt_id is not None
    assert reconciled.attempt_id != first_executor_attempt_id

    exported = export_execution_artifacts(request, reconciled, destination)

    assert exported["attempt_identity"] == {
        "namespace": "control_plane",
        "attempt_id": reconciled.attempt_id,
        "owner": "cogno-us/cognous-agent-control-plane",
    }
    assert len(exported["control_plane_attempts"]) == 1
    cp_attempt = exported["control_plane_attempts"][0]
    assert cp_attempt["attempt_id"] == reconciled.attempt_id
    assert cp_attempt["decision_id"] == decision.decision_id
    assert cp_attempt["effect_id"] == decision.effect_id

    executor_attempt_ids = {row["attempt_id"] for row in exported["attempts"]}
    assert first_executor_attempt_id in executor_attempt_ids
    assert reconciled.attempt_id not in executor_attempt_ids
    assert len(exported["effects"]) == 1
    assert exported["effects"][0]["effect_id"] == decision.effect_id
    assert destination.effect_count(decision.binding.grant_id) == 1


def test_export_rejects_fabricated_control_plane_attempt_reference(tmp_path):
    h, p, resolver, workflow, decision, destination, executor, request = _integrated(
        tmp_path
    )
    first = executor.execute(
        envelope=request, proposal=p, decision=decision, now=h.NOW
    )
    assert first.status == "executed"

    reconciled = executor.execute(
        envelope=request, proposal=p, decision=decision, now=h.NOW
    )
    assert reconciled.status == "reconciled"

    fabricated = copy.deepcopy(reconciled)
    fabricated.observation["control_plane_attempt_evidence"]["attempt_id"] = (
        "fabricated-control-plane-attempt"
    )

    with pytest.raises(ValueError, match="Control Plane attempt evidence identity mismatch"):
        export_execution_artifacts(request, fabricated, destination)


def test_export_rejects_dangling_unattributed_attempt_reference(tmp_path):
    op = make_operation()
    request = envelope(
        op,
        decision_id="decision-dangling",
        effect_id="effect-dangling",
    )
    destination = DurableRefundDestination(tmp_path / "empty-state")
    dangling = ExecutionResult(
        status="unknown",
        decision_id=request.decision_id,
        effect_id=request.effect_id,
        attempt_id="dangling-attempt",
        attempted=True,
        acknowledged=False,
        observed_state="unknown",
        newly_executed=False,
        observation={
            "effect_id": request.effect_id,
            "state": "unknown",
            "destination_state": {},
        },
    )

    with pytest.raises(ValueError, match="no retained bound attempt evidence"):
        export_execution_artifacts(request, dangling, destination)
