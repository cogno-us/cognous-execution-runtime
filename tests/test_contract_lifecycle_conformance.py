"""PRODUCER conformance of the runtime executors to the ODES execution-attempt-lifecycle contract (P2).

Contract: execution-attempt-lifecycle 0.1.0-draft (a draft, not frozen), vendored under
tests/contracts/execution-attempt-lifecycle/. Role: producer. Level: P (structure, rules and a real producer run).
See docs/contract-conformance-execution-attempt-lifecycle.md.

Every case drives the runtime's own executors with synthetic ids and data against synthetic SQLite
destinations, maps the real outcome faithfully into a contract document (producer "runtime-executor") and validates it
with the vendored validator. Nothing is reshaped to fit: where the runtime's output fails the contract, the case is a
strict xfail with a finding id (and a second, non-xfail test pins the exact violation codes as evidence), so a runtime
fix flips the xfail visibly. After the P3 runtime change only F-P2-RT-3 was left; the contract now admits attempt event
'denied' (ODES e33d541a) and that case is a plain conformance assertion too.

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


_NEW_INVALID_FIXTURES = (
    "executor-ghost-result-without-attempts-member",
    "executor-events-out-of-order-without-attempts-member",
    "control-plane-empty-attempts-ghost-result",
)


@pytest.mark.parametrize("name", _NEW_INVALID_FIXTURES)
def test_missing_attempts_member_does_not_skip_result_and_event_rules(name):
    """B-F1: a missing `attempts` member is an empty row list; the result and event rules still run."""
    fixture = json.loads((CONTRACT_DIR / "fixtures" / "invalid" / f"{name}.json").read_text(encoding="utf-8"))
    assert set(codes(fixture["document"])) == {fixture["expected_refusal"]}


def test_missing_attempts_member_valid_documents_without_rows_stay_valid():
    doc = {"contract": CONTRACT_NAME, "contract_version": CONTRACT_VERSION, "producer": "runtime-executor",
           "resolution_journal": []}
    assert codes(doc) == []


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
        **({"recovery_observations": exported["recovery_observations"]}
           if "recovery_observations" in exported else {}),
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
    assert result.status == "reconciled"
    return to_document(result, dest)


def case_a_unknown_n1_malformed_ack(tmp_path):
    _, dest, result = run_a(tmp_path, dest=_MalformedAckAfterCommit(tmp_path / "state"))
    assert result.status == "reconciled" and result.observation["state"] == "applied"
    return to_document(result, dest)


def case_a_unknown_ack_unobserved(tmp_path):
    _AckLostObservationDown.broken = False
    _, dest, result = run_a(tmp_path, dest=_AckLostObservationDown(tmp_path / "state"))
    assert result.status == "unknown"
    return to_document(result, dest)


def _case_timeout(tmp_path, state):
    _, dest, result = run_a(tmp_path, dest=_timeout_dest(tmp_path, state))
    assert (result.status, result.observed_state) == ("unknown", state)
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
    "route-a-failed": case_a_failed,
    "route-a-denied-attempt-event": case_a_denied,
    "route-a-lost-ack-observed-applied-reports-reconciled": case_a_unknown_n1_lost_ack,
    "route-a-malformed-ack-after-commit-openshell-style-reports-reconciled": case_a_unknown_n1_malformed_ack,
    "route-a-ack-unobserved-unknown-null-observation": case_a_unknown_ack_unobserved,
    "route-a-timeout-validated-absent-carried": lambda p: _case_timeout(p, "absent"),
    "route-a-timeout-validated-unknown-carried": lambda p: _case_timeout(p, "unknown"),
    "route-b-executed": case_b_executed,
    "route-b-partial": case_b_partial,
    "route-b-denied": case_b_denied,
    "route-b-post-commit-observation-failure-recovery-applied": case_b_post_commit_recovery_applied,
    "route-b-lost-ack-reconciliation-down-unknown-placeholder": case_b_lost_ack_reconcile_down,
    "route-b-effect-free-reconcile-observed": case_b_effect_free_reconcile,
    "export-route-a-normal": case_export_a_normal,
    "export-route-b-normal": case_export_b_normal,
    "export-route-b-post-commit-recovery": case_export_b_post_commit_recovery,
    "export-route-b-effect-free-reconcile": case_export_b_effect_free_reconcile,
}


@pytest.mark.parametrize("builder", CONFORMANT.values(), ids=CONFORMANT.keys())
def test_runtime_output_conforms(tmp_path, builder):
    assert codes(builder(tmp_path)) == []


def test_unknown_with_null_observation_is_emitted_when_ack_unobserved(tmp_path):
    """Genuine 'unknown' (no validated observation) carries observation null, never a fabricated one (F-P2-RT-1)."""
    doc = case_a_unknown_ack_unobserved(tmp_path)
    result = doc["execution_result"]
    assert (result["status"], result["observed_state"], result["observation"]) == ("unknown", "unknown", None)
    assert doc["attempt_events"][-1]["status"] == "unknown"


# --------------------------------------------------------------------------- behaviour added by the P3 change

def test_lost_ack_with_validated_observation_reports_reconciled_and_terminal_event_agrees(tmp_path):
    _, dest, result = run_a(tmp_path, simulate="lost_ack")
    assert (result.status, result.observed_state, result.acknowledged, result.newly_executed) == (
        "reconciled", "applied", False, False)
    assert result.observation["state"] == "applied" and result.error
    assert [e["status"] for e in dest.attempt_history(result.attempt_id)] == ["attempted", "reconciled"]


def test_partial_observed_after_lost_ack_reports_partial(tmp_path):
    class _PartialThenLost(DurableRefundDestination):
        def commit(self, snapshot, **kwargs):
            super().commit(snapshot, simulate="partial")
            raise TimeoutError("synthetic lost acknowledgement")

    _, dest, result = run_a(tmp_path, dest=_PartialThenLost(tmp_path / "state"))
    assert (result.status, result.observed_state) == ("partial", "partial")
    assert [e["status"] for e in dest.attempt_history(result.attempt_id)][-1] == "partial"
    assert codes(to_document(result, dest)) == []


class _TimeoutThenReads(DurableRefundDestination):
    """Commit times out before any effect; the pre-commit read is real, the follow-up read returns `state`."""
    state = "absent"

    def commit(self, snapshot, **kwargs):
        raise TimeoutError("synthetic timeout before any effect")

    def observe_bound(self, snapshot):
        if getattr(self, "armed", False):
            return {"effect_id": snapshot.effect_id, "state": self.state, "destination_state": {}}
        self.armed = True
        return super().observe_bound(snapshot)


def _timeout_dest(tmp_path, state):
    dest = _TimeoutThenReads(tmp_path / "state")
    dest.state = state
    return dest


@pytest.mark.parametrize("state", ["absent", "unknown"])
def test_non_establishing_observation_after_timeout_stays_unknown_and_carries_the_observation(tmp_path, state):
    """Decision 14: an 'absent'/'unknown' observation right after an ambiguous dispatch never becomes failed or
    reconciled; the 'unknown' result carries that validated observation and observed_state equals its state."""
    _, dest, result = run_a(tmp_path, dest=_timeout_dest(tmp_path, state))
    assert (result.status, result.observed_state) == ("unknown", state)
    assert result.observation == {"effect_id": result.effect_id, "state": state, "destination_state": {}}
    assert dest.attempt_history(result.attempt_id)[-1]["status"] == "unknown"
    assert codes(to_document(result, dest)) == []


@pytest.mark.parametrize("state", ["absent", "unknown"])
def test_export_after_timeout_carries_validated_observation_in_observations(tmp_path, state):
    env, dest, result = run_a(tmp_path, dest=_timeout_dest(tmp_path, state))
    exported = export_execution_artifacts(env, result, dest, repository_revision="test")
    assert exported["observations"] == [exported["execution_result"]["observation"]]
    assert exported["observations"][0]["state"] == state == exported["execution_result"]["observed_state"]
    assert "recovery_observations" not in exported
    assert codes(export_to_document(exported)) == []


def test_pre_commit_existing_unknown_observation_is_carried(tmp_path):
    class _ExistingUnknown(DurableRefundDestination):
        def observe_bound(self, snapshot):
            return {"effect_id": snapshot.effect_id, "state": "unknown", "destination_state": {}}

    _, dest, result = run_a(tmp_path, dest=_ExistingUnknown(tmp_path / "state"))
    assert (result.status, result.observed_state, result.observation["state"]) == ("unknown", "unknown", "unknown")
    assert codes(to_document(result, dest)) == []


def test_follow_up_observation_failure_after_timeout_is_unknown_null(tmp_path):
    _AckLostObservationDown.broken = False
    _, dest, result = run_a(tmp_path, dest=_AckLostObservationDown(tmp_path / "state"))
    assert (result.status, result.observed_state, result.observation) == ("unknown", "unknown", None)


def test_failed_and_denied_results_carry_null_observation_and_unknown_state(tmp_path):
    """Decision 15: whenever observation is null, observed_state is 'unknown' for every status; and `{}` is never emitted."""
    for doc in (case_a_failed(tmp_path / "a"), case_a_denied(tmp_path / "b"), case_b_denied(tmp_path / "c")):
        result = doc["execution_result"]
        assert result["observation"] is None and result["observed_state"] == "unknown"


def test_export_puts_recovery_report_only_in_recovery_observations(tmp_path):
    env, dest, _, result = run_b(tmp_path / "a", mode="post_commit_fail")
    exported = export_execution_artifacts(env, result, dest, repository_revision="test")
    assert exported["observations"] == []
    assert exported["recovery_observations"] == [exported["execution_result"]["observation"]]
    assert exported["producer_profile"]["profile_version"] == "2.0.0"
    env, dest, _, result = run_b(tmp_path / "b")
    exported = export_execution_artifacts(env, result, dest, repository_revision="test")
    assert exported["observations"] == [exported["execution_result"]["observation"]]
    assert "recovery_observations" not in exported


def test_export_with_null_observation_has_no_observations(tmp_path):
    env, dest, result = run_a(tmp_path, dest=_AckLostObservationDown(tmp_path / "state"))
    exported = export_execution_artifacts(env, result, dest, repository_revision="test")
    assert exported["execution_result"]["observation"] is None
    assert exported["observations"] == [] and "recovery_observations" not in exported
