"""PRODUCER conformance of the runtime executors to the ODES execution-attempt-lifecycle contract (P2).

Contract: execution-attempt-lifecycle 0.1.0-draft (a draft, not frozen), vendored under
tests/contracts/execution-attempt-lifecycle/. Role: producer. Level: P (structure, rules and a real producer run).
See docs/contract-conformance-execution-attempt-lifecycle.md.

Test-only. Every case drives the runtime's own executors with synthetic ids and data against synthetic SQLite
destinations, maps the real outcome faithfully into a contract document (producer "runtime-executor") and validates it
with the vendored validator. Nothing is reshaped to fit: where the runtime's output fails the contract, the case is a
strict xfail with a finding id (and a second, non-xfail test pins the exact violation codes as evidence), so a runtime
fix flips the xfail visibly.

Route B note: the real AtomicAuthorityEffectDestination.execute_claim_atomic needs the Control Plane package, which is
not a dependency of this repository. The route B cases therefore use a synthetic subclass of the real destination class
(real schema, real executor, real reconcile_claim) whose atomic claim step writes the same attempts/attempt_events/
effects/claims rows as the real one. Only the authority validation is omitted. The real destination is covered by the
cross-component scenarios (see CROSS_COMPONENT_TESTS.md).
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from engine.atomic_local_control_plane_executor import AtomicLocalControlPlaneExecutor
from engine.local_authority_effect import AtomicAuthorityEffectDestination, PostCommitObservationUnavailable
from engine.producer_contract import export_execution_artifacts
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION,
    DurableRefundDestination,
    ExecutionEnvelope,
    LocalDestinationExecutor,
    snapshot_envelope,
)
from test_producer_contract import operation, policy

CONTRACT_DIR = Path(__file__).parent / "contracts" / "execution-attempt-lifecycle"
CONTRACT_NAME = "execution-attempt-lifecycle"
CONTRACT_VERSION = "0.1.0-draft"
FINDING_N1 = "finding N-1: owner decided the runtime changes to report reconciled/partial (P3)"


# --------------------------------------------------------------------------- 1. drift check (no ODES dependency)

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_vendored_contract_has_not_drifted():
    manifest = json.loads((CONTRACT_DIR / "CONTRACT.json").read_text(encoding="utf-8"))
    assert manifest["contract"] == CONTRACT_NAME
    assert manifest["contract_version"] == CONTRACT_VERSION
    assert manifest["role"] == "producer"
    listed = manifest["files"]
    problems = []
    for rel, expected in sorted(listed.items()):
        target = CONTRACT_DIR / rel
        if not target.is_file():
            problems.append(f"missing: {rel}")
        elif _sha256(target) != expected:
            problems.append(f"modified: {rel}")
    on_disk = {
        p.relative_to(CONTRACT_DIR).as_posix()
        for p in CONTRACT_DIR.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    } - {"CONTRACT.json"}
    problems += [f"unlisted: {rel}" for rel in sorted(on_disk - set(listed))]
    assert not problems, "vendored contract drifted; refresh it (see docs): " + "; ".join(problems)


# --------------------------------------------------------------------------- 2. vendored validator

def _load_validator():
    name = "_vendored_execution_attempt_lifecycle_validate"
    spec = importlib.util.spec_from_file_location(name, CONTRACT_DIR / "validate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # registered before exec: validate.py defines a dataclass
    spec.loader.exec_module(module)
    return module


VALIDATOR = _load_validator()


def codes(doc) -> list[str]:
    return [v.code for v in VALIDATOR.validate(doc)]


def test_vendored_valid_fixtures_are_accepted_by_vendored_validator():
    """Sanity that the loaded validator behaves: every vendored valid fixture validates to []."""
    for path in sorted((CONTRACT_DIR / "fixtures" / "valid").glob("*.json")):
        assert codes(json.loads(path.read_text(encoding="utf-8"))["document"]) == [], path.name


# --------------------------------------------------------------------------- mapping helpers

def _table(path, table: str) -> list[dict]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
    finally:
        conn.close()


def to_document(result, dest, *, attempt_ids=None) -> dict:
    """Map what the runtime emits today: rows as stored, result as the dataclass fields. Nothing is repaired.

    `observations` / `recovery_observations` are omitted (both optional): the runtime result carries the report in
    execution_result.observation, which is the member the contract rules read. The export-based cases below map the
    runtime's own `observations` member as emitted.
    """
    attempts = _table(dest.path, "attempts")
    events = _table(dest.path, "attempt_events")
    if attempt_ids is not None:
        attempts = [r for r in attempts if r["attempt_id"] in attempt_ids]
        events = [r for r in events if r["attempt_id"] in attempt_ids]
    return {
        "contract": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "producer": "runtime-executor",
        "attempts": attempts,
        "attempt_events": events,
        "execution_result": dataclasses.asdict(result),
    }


def export_to_document(exported: dict) -> dict:
    """Map engine/producer_contract.export_execution_artifacts output; the export has no contract/producer markers."""
    return {
        "contract": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "producer": "runtime-executor",
        "attempts": exported["attempts"],
        "attempt_events": exported["attempt_events"],
        "execution_result": exported["execution_result"],
        "observations": exported["observations"],
    }


def envelope(effect_id="effect-1", decision_id="decision-1", op=None, attempt_id=None) -> ExecutionEnvelope:
    return ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, decision_id, effect_id, op or operation(), attempt_id)


# --------------------------------------------------------------------------- route A (PinnedControlPlaneExecutor's
# destination executor: engine/safe_executor.py LocalDestinationExecutor)

def run_a(tmp_path, *, simulate=None, dest=None, env=None):
    env = env or envelope()
    dest = dest or DurableRefundDestination(tmp_path / "state")
    result = LocalDestinationExecutor(dest, policy(env.operation)).execute_snapshot(
        snapshot_envelope(env), simulate=simulate)
    return env, dest, result


class _CommitFails(DurableRefundDestination):
    def commit(self, snapshot, **kwargs):
        raise RuntimeError("synthetic destination failure before commit")


class _MalformedAckAfterCommit(DurableRefundDestination):
    """Fake of the OpenShell commit path (engine/openshell_environment.py:375-386) without OpenShell.

    The effect is durably committed and observable as applied; the acknowledgement is then rejected as malformed,
    which OpenShellRefundDestination.commit turns into TimeoutError. observe_bound stays real and returns applied.
    """

    def commit(self, snapshot, **kwargs):
        super().commit(snapshot, **kwargs)
        raise TimeoutError("invalid destination acknowledgement")


class _AckLostObservationDown(DurableRefundDestination):
    """Effect committed, ack lost, and the follow-up observation also fails (safe_executor.py:892-897)."""

    broken = False

    def commit(self, snapshot, **kwargs):
        super().commit(snapshot, **kwargs)
        type(self).broken = True
        raise TimeoutError("synthetic acknowledgement unavailable after durable commit")

    def observe_bound(self, snapshot):
        if self.broken:
            raise ConnectionError("synthetic observation outage")
        return super().observe_bound(snapshot)


def case_a_executed(tmp_path):
    _, dest, result = run_a(tmp_path)
    return to_document(result, dest)


def case_a_partial(tmp_path):
    _, dest, result = run_a(tmp_path, simulate="partial")
    return to_document(result, dest)


def case_a_failed(tmp_path):
    _, dest, result = run_a(tmp_path, dest=_CommitFails(tmp_path / "state"))
    assert result.status == "failed"
    return to_document(result, dest)


def case_a_denied(tmp_path):
    """Second request reuses effect_id with different operation content: observe_bound refuses, attempt is 'denied'."""
    first_env, dest, _ = run_a(tmp_path)
    changed = envelope(op=dataclasses.replace(operation(), amount=60.0))
    _, _, result = run_a(tmp_path, dest=dest, env=changed)
    assert result.status == "denied" and result.attempt_id
    return to_document(result, dest, attempt_ids={result.attempt_id})


def case_a_unknown_n1_lost_ack(tmp_path):
    _, dest, result = run_a(tmp_path, simulate="lost_ack")
    assert result.status == "unknown"
    return to_document(result, dest)


def case_a_unknown_n1_malformed_ack(tmp_path):
    _, dest, result = run_a(tmp_path, dest=_MalformedAckAfterCommit(tmp_path / "state"))
    assert result.status == "unknown" and result.observation["state"] == "applied"
    return to_document(result, dest)


def case_a_unknown_ack_unobserved(tmp_path):
    _AckLostObservationDown.broken = False
    _, dest, result = run_a(tmp_path, dest=_AckLostObservationDown(tmp_path / "state"))
    assert result.status == "unknown"
    return to_document(result, dest)


# --------------------------------------------------------------------------- route B (AtomicLocalControlPlaneExecutor)

class _SyntheticAtomicDestination(AtomicAuthorityEffectDestination):
    """Real destination class, real schema and real reconcile_claim; synthetic atomic claim step (see module docs)."""

    mode = "ok"            # ok | partial | post_commit_fail | lost_ack
    reconcile_down = False

    @staticmethod
    def _operation_commitment(snapshot):
        return "sha256:" + hashlib.sha256(snapshot.operation.digest.encode()).hexdigest()

    def seed_claim(self, claim_id, snapshot):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO execution_claims_v1(claim_id,claim_commitment,claim_json,effect_id,decision_id,grant_id,"
                "grant_revision,operation_commitment,effect_operation_digest,budget_id,max_effects,not_before,"
                "expires_at,tenant_id,state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (claim_id, "sha256:c", "{}", snapshot.effect_id, snapshot.decision_id, snapshot.operation.grant_id,
                 "1", self._operation_commitment(snapshot), None, "budget-1", 1,
                 "2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00", None, "issued"))

    def execute_claim_atomic(self, claim_id, snapshot, *, attempt_id, simulate=None):
        stamp = self._trusted_now().timestamp()
        state = "partial" if self.mode == "partial" else "applied"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO attempts(attempt_id,effect_id,decision_id,operation_digest,created_at) "
                         "VALUES(?,?,?,?,?)",
                         (attempt_id, snapshot.effect_id, snapshot.decision_id, snapshot.operation.digest, stamp))
            conn.execute("INSERT INTO attempt_events(attempt_id,status,error,created_at) VALUES(?,?,?,?)",
                         (attempt_id, "attempted", None, stamp))
            conn.execute("UPDATE execution_claims_v1 SET state='consumed', effect_operation_digest=? WHERE claim_id=?",
                         (snapshot.operation.digest, claim_id))
            op = snapshot.operation
            conn.execute("INSERT INTO effects(effect_id,operation_digest,grant_id,target,amount,unit,payload_json,state)"
                         " VALUES(?,?,?,?,?,?,?,?)",
                         (snapshot.effect_id, op.digest, op.grant_id, op.target, float(op.amount), op.unit,
                          op.payload_json, state))
            conn.execute("INSERT INTO attempt_events(attempt_id,status,error,created_at) VALUES(?,?,?,?)",
                         (attempt_id, "partial" if state == "partial" else "executed", None, stamp))
            conn.execute("COMMIT")
        if self.mode == "post_commit_fail":
            raise PostCommitObservationUnavailable(
                "post-commit observation unavailable after atomic commit: ConnectionError")
        if self.mode == "lost_ack":
            raise TimeoutError("synthetic acknowledgement unavailable after atomic commit")
        return {"claim_id": claim_id, "attempt_id": attempt_id, "newly_executed": True,
                "observation": self.observe_bound(snapshot), "linearized_at": "2026-10-07T00:00:00+00:00",
                "authority_receipt": {"attempt_id": attempt_id, "claim_id": claim_id}}

    def reconcile_claim(self, claim_id, snapshot):
        if self.reconcile_down:
            raise ConnectionError("synthetic reconciliation outage")
        return super().reconcile_claim(claim_id, snapshot)


def run_b(tmp_path, *, mode="ok", reconcile_down=False, env=None):
    env = env or envelope()
    dest = _SyntheticAtomicDestination(
        tmp_path / "state", clock=lambda: datetime(2026, 10, 7, tzinfo=timezone.utc))
    dest.mode, dest.reconcile_down = mode, reconcile_down
    dest.seed_claim("claim-1", snapshot_envelope(env))
    executor = AtomicLocalControlPlaneExecutor(workflow=None, destination=dest, policy=policy(env.operation))
    result = executor.execute(envelope=env, claim_id="claim-1")
    return env, dest, executor, result


def case_b_executed(tmp_path):
    _, dest, _, result = run_b(tmp_path)
    assert result.status == "executed"
    return to_document(result, dest)


def case_b_partial(tmp_path):
    _, dest, _, result = run_b(tmp_path, mode="partial")
    assert result.status == "partial"
    return to_document(result, dest)


def case_b_denied(tmp_path):
    """Local policy refuses before any claim step: no attempt rows exist."""
    bad = envelope(op=dataclasses.replace(operation(), amount=99999.0))
    dest = _SyntheticAtomicDestination(tmp_path / "state", clock=lambda: datetime(2026, 10, 7, tzinfo=timezone.utc))
    executor = AtomicLocalControlPlaneExecutor(workflow=None, destination=dest, policy=policy(operation()))
    result = executor.execute(envelope=bad, claim_id="claim-1")
    assert result.status == "denied"
    return to_document(result, dest)


def case_b_post_commit_recovery_applied(tmp_path):
    _, dest, _, result = run_b(tmp_path, mode="post_commit_fail")
    assert (result.status, result.observed_state, result.observation["status"]) == ("unknown", "applied", "applied")
    return to_document(result, dest)


def case_b_lost_ack_reconcile_down(tmp_path):
    _, dest, _, result = run_b(tmp_path, mode="lost_ack", reconcile_down=True)
    assert result.observation == {"status": "unknown", "retry_eligible": False, "reason": "reconciliation_unavailable"}
    return to_document(result, dest)


def case_b_effect_free_reconcile(tmp_path):
    env, dest, executor, _ = run_b(tmp_path)
    result = executor.reconcile(claim_id="claim-1", envelope=env)
    assert result.status == "observed" and result.attempt_id is None
    return to_document(result, dest)


# --------------------------------------------------------------------------- export (engine/producer_contract.py)

def case_export_a_normal(tmp_path):
    env, dest, result = run_a(tmp_path)
    return export_to_document(export_execution_artifacts(env, result, dest, repository_revision="test"))


def case_export_b_normal(tmp_path):
    env, dest, _, result = run_b(tmp_path)
    return export_to_document(export_execution_artifacts(env, result, dest, repository_revision="test"))


def case_export_b_post_commit_recovery(tmp_path):
    env, dest, _, result = run_b(tmp_path, mode="post_commit_fail")
    return export_to_document(export_execution_artifacts(env, result, dest, repository_revision="test"))


def case_export_b_effect_free_reconcile(tmp_path):
    env, dest, executor, _ = run_b(tmp_path)
    result = executor.reconcile(claim_id="claim-1", envelope=env)
    return export_to_document(export_execution_artifacts(env, result, dest, repository_revision="test"))


# --------------------------------------------------------------------------- 3. conformant cases (must be [])

CONFORMANT = {
    "route-a-executed": case_a_executed,
    "route-a-partial": case_a_partial,
    "route-b-executed": case_b_executed,
    "route-b-partial": case_b_partial,
    "route-b-post-commit-observation-failure-recovery-applied": case_b_post_commit_recovery_applied,
    "route-b-lost-ack-reconciliation-down-unknown-placeholder": case_b_lost_ack_reconcile_down,
    "route-b-effect-free-reconcile-observed": case_b_effect_free_reconcile,
    "export-route-a-normal": case_export_a_normal,
    "export-route-b-normal": case_export_b_normal,
}


@pytest.mark.parametrize("builder", CONFORMANT.values(), ids=CONFORMANT.keys())
def test_runtime_output_conforms(tmp_path, builder):
    assert codes(builder(tmp_path)) == []


# --------------------------------------------------------------------------- 4/5. findings (strict xfail + pinned evidence)
# id -> (builder, reason, exact violation codes the vendored validator reports today)

FINDINGS = {
    "N-1-route-a-lost-ack-observed-applied": (
        case_a_unknown_n1_lost_ack, FINDING_N1, ["unknown_with_validated_observation"]),
    "N-1-route-a-malformed-ack-after-commit-openshell-style": (
        case_a_unknown_n1_malformed_ack, FINDING_N1, ["unknown_with_validated_observation"]),
    "F-P2-RT-1-route-a-ack-unobserved-fabricates-validated-unknown": (
        case_a_unknown_ack_unobserved,
        "finding F-P2-RT-1: safe_executor timeout handler emits a validated observation with state 'unknown' instead "
        "of null when observation fails (safe_executor.py:893-896); not covered by the N-1 reconciled/partial change",
        ["unknown_with_validated_observation"]),
    "F-P2-RT-2-route-a-failed-observation-empty-dict": (
        case_a_failed,
        "finding F-P2-RT-2: ExecutionResult.observation defaults to {} (safe_executor.py:399), so failed/denied "
        "results carry {} where the contract allows null, a validated or a recovery observation",
        ["schema_violation"]),
    "F-P2-RT-2-route-b-denied-observation-empty-dict": (
        case_b_denied,
        "finding F-P2-RT-2: route B _denied builds ExecutionResult without observation, so it is {} "
        "(atomic_local_control_plane_executor.py:104-137, safe_executor.py:399)",
        ["schema_violation"]),
    "F-P2-RT-3-route-a-denied-attempt-event-not-in-enum": (
        case_a_denied,
        "finding F-P2-RT-3: route A records attempt event 'denied' (safe_executor.py:827 and 843), which is "
        "not in the contract's closed attempt-event enum, and the result observation is {} (F-P2-RT-2)",
        ["unknown_enum_value", "schema_violation"]),
    "F-P2-RT-4-export-route-b-recovery-report-in-legacy-observations": (
        case_export_b_post_commit_recovery,
        "finding F-P2-RT-4: export_execution_artifacts puts the recovery report in `observations` and emits no "
        "`recovery_observations` (producer_contract.py:247-249 and 286); the contract refuses the legacy layout (runtime P3)",
        ["schema_violation"]),
    "F-P2-RT-4-export-route-b-effect-free-reconcile-legacy-observations": (
        case_export_b_effect_free_reconcile,
        "finding F-P2-RT-4: same legacy layout for the effect-free reconcile export (producer_contract.py:247-249 and 286)",
        ["schema_violation"]),
}


def _finding_params(with_xfail: bool):
    return [
        pytest.param(fid, marks=pytest.mark.xfail(strict=True, reason=reason)) if with_xfail else pytest.param(fid)
        for fid, (_, reason, _) in FINDINGS.items()
    ]


@pytest.mark.parametrize("finding_id", _finding_params(True))
def test_runtime_output_conforms_known_non_conformance(tmp_path, finding_id):
    """Strict xfail: becomes a visible failure (XPASS) the moment the runtime starts conforming."""
    builder, _, _ = FINDINGS[finding_id]
    assert codes(builder(tmp_path)) == []


@pytest.mark.parametrize("finding_id", _finding_params(False))
def test_finding_evidence_pins_current_violation_codes(tmp_path, finding_id):
    """Evidence, not a green light: what the vendored validator reports for each finding today."""
    builder, _, expected = FINDINGS[finding_id]
    assert codes(builder(tmp_path)) == expected


def test_null_observation_unknown_is_not_emitted_by_runtime(tmp_path):
    """Contract case 'unknown with null observation (ack unobserved)' cannot be produced by the runtime today.

    Route A returns a validated observation (state 'unknown', F-P2-RT-1) and route B returns a recovery record; no
    executor path emits result 'unknown' with observation None.
    """
    for builder in (case_a_unknown_ack_unobserved, case_b_lost_ack_reconcile_down, case_b_post_commit_recovery_applied):
        assert builder(tmp_path / builder.__name__)["execution_result"]["observation"] is not None
