"""PinnedControlPlaneExecutor: failed and denied results report observed_state 'unknown' and carry no observed effect.

Lifecycle contract (and `_validate_result_state`): denied and failed carry no acknowledgement, no fresh execution and
`observed_state` `unknown`. The commit-time grant-change race (`effective_grant_changed_at_commit`) is a definitive
destination rejection; the Control Plane then reconciles and returns a validated `absent` observation. The result is
`failed`; the validated reconciliation record stays in `control_plane_evidence` and is not attached as an observed effect.
A result with status `unknown` keeps carrying a validated `absent`/`unknown` observation (lifecycle contract decision 14).

Component-local and synthetic: a stub workflow and a stub of the Control Plane types the adapter imports lazily are used,
so no Control Plane package is required. The stub follows the order of the real workflow: observe, apply, reconcile.
"""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from engine.control_plane_adapter import PinnedControlPlaneExecutor
from engine.producer_contract import export_execution_artifacts
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION,
    DurableRefundDestination,
    ExecutionEnvelope,
    snapshot_envelope,
)
from test_contract_lifecycle_conformance import codes, export_to_document
from test_producer_contract import operation, policy
from test_route_a_tenant_binding import _Proposal, _Resolver

NOW = "2026-10-19T12:00:00+00:00"


class _Rejection(Exception):
    """Stand-in for agent_control_plane.bounded.DefinitiveDestinationRejection."""


class _Observation:
    """Stand-in for agent_control_plane.bounded.EffectObservation."""

    def __init__(self, *, effect_id, observed_at, state, destination_state):
        self.effect_id, self.observed_at, self.state, self.destination_state = (
            effect_id, observed_at, state, destination_state)

    def model_dump(self, **_):
        return {"effect_id": self.effect_id, "observed_at": self.observed_at, "state": self.state,
                "destination_state": dict(self.destination_state)}


class _Record:
    def __init__(self, value):
        self.value = value

    def model_dump(self, **_):
        return dict(self.value)


class _Records:
    def __init__(self):
        self.reconciliations = []

    def append_reconciliation(self, value):
        self.reconciliations.append(value)


class _Workflow:
    """Stub of the bounded workflow: observe, apply, then reconcile. `__class__(...)` builds the per-call view."""

    resolver = _Resolver()
    manifest = {}
    observation_policy = None
    status_max_age_seconds = identity_max_age_seconds = mandate_max_age_seconds = approval_max_age_seconds = 300
    clock_tolerance_seconds = 5
    runtime_authority_profile = None
    rule_revision_profile = None
    allow_test_time = False

    def __init__(self, *, destination=None, records=None, **_):
        self.destination = destination
        self.records = records if records is not None else _Records()

    def execute(self, *, proposal, decision, adapter_id, now=None, **_):
        dest = self.destination
        op = dest.snapshot.operation
        dest.observe(dest.snapshot.effect_id)
        error = None
        try:
            dest.apply(effect_id=dest.snapshot.effect_id, grant_id=op.grant_id, max_effects=op.effective_max_effects,
                       target=op.target, amount=op.amount, unit=op.unit, payload=op.payload())
            status = "acknowledged"
        except _Rejection as exc:
            status, error = "failed", str(exc)
        except TimeoutError as exc:
            status, error = "unknown", str(exc)
        observation = dest.observe(dest.snapshot.effect_id)
        self.records.append_reconciliation(_Record({
            "effect_id": observation.effect_id, "reconciled_at": NOW,
            "result": "observed_" + observation.state, "observation": observation.model_dump(),
            "observation_accepted": True, "retry_eligible": False, "reasons": []}))
        attempt = SimpleNamespace(attempt_id="cp-attempt-1", status=status, error=error)
        attempt.model_dump = lambda **_: {
            "attempt_id": "cp-attempt-1", "effect_id": dest.snapshot.effect_id,
            "decision_id": dest.snapshot.decision_id, "started_at": NOW, "status": status,
            "acknowledgement": {}, "error": error}
        return attempt, observation


class _RaceDestination(DurableRefundDestination):
    """The grant changed between the Control Plane read and the commit: the destination refuses before any effect."""

    def commit(self, snapshot, **kwargs):
        raise PermissionError("effective_grant_changed_at_commit")


class _TimeoutBeforeEffect(DurableRefundDestination):
    """Commit times out before any effect; the follow-up observation is a validated 'absent'."""

    def commit(self, snapshot, **kwargs):
        raise TimeoutError("synthetic timeout before any effect")


@pytest.fixture
def control_plane_stub(monkeypatch):
    bounded = types.ModuleType("agent_control_plane.bounded")
    bounded.EffectObservation = _Observation
    bounded._iso = lambda value: NOW
    bounded.commitment = lambda value: operation().proposal_commitment
    bounded.DefinitiveDestinationRejection = _Rejection
    monkeypatch.setitem(sys.modules, "agent_control_plane", types.ModuleType("agent_control_plane"))
    monkeypatch.setitem(sys.modules, "agent_control_plane.bounded", bounded)


def _run(tmp_path, destination_class):
    op = operation()
    env = ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)
    frozen = snapshot_envelope(env)
    dest = destination_class(tmp_path / "state")
    workflow = _Workflow()
    workflow.records = _Records()
    executor = PinnedControlPlaneExecutor(
        workflow=workflow, destination=dest, policy=policy(op), observation_clock=lambda: None)
    decision = SimpleNamespace(
        decision_id=frozen.decision_id, effect_id=frozen.effect_id,
        binding=SimpleNamespace(grant_id=op.grant_id, grant_revision=op.grant_revision,
                                effective_max_effects=op.effective_max_effects))
    proposal = _Proposal(op)
    result = executor.execute(envelope=env, proposal=proposal, decision=decision)
    return env, dest, workflow, result


def test_commit_time_grant_change_race_reports_failed_with_observed_state_unknown(tmp_path, control_plane_stub):
    env, dest, workflow, result = _run(tmp_path, _RaceDestination)
    assert result.status == "failed" and result.error == "effective_grant_changed_at_commit"
    assert (result.attempted, result.acknowledged, result.newly_executed) == (True, False, False)
    assert result.observed_state == "unknown"
    assert result.observation is None  # no observed effect is attached
    assert dest.effect_count(env.operation.grant_id) == 0
    # The validated reconciliation observation is retained as evidence, not hidden.
    reconciliation = result.control_plane_evidence["reconciliation"]
    assert reconciliation["observation_accepted"] is True and reconciliation["retry_eligible"] is False
    assert reconciliation["observation"]["state"] == "absent"
    assert reconciliation["observation"]["effect_id"] == result.effect_id
    assert result.control_plane_evidence["attempt"]["status"] == "failed"
    assert result.control_plane_evidence["attempt"]["error"] == "effective_grant_changed_at_commit"
    assert [r.model_dump()["observation"]["state"] for r in workflow.records.reconciliations] == ["absent"]


def test_failed_race_result_is_exported_with_attempt_identity_and_reconciliation_evidence(tmp_path, control_plane_stub):
    env, dest, _, result = _run(tmp_path, _RaceDestination)
    exported = export_execution_artifacts(env, result, dest, repository_revision="test")  # does not raise
    assert exported["execution_result"]["status"] == "failed"
    assert exported["execution_result"]["observed_state"] == "unknown"
    assert exported["execution_result"]["observation"] is None
    assert exported["execution_result"]["acknowledged"] is False
    assert exported["observations"] == [] and exported["rejected_observations"] == []
    assert exported["attempt_identity"] == {
        "namespace": "executor", "attempt_id": result.attempt_id, "owner": "cogno-us/moltbot-safe"}
    assert [a["attempt_id"] for a in exported["attempts"]] == [result.attempt_id]
    assert [e["status"] for e in exported["attempt_events"]][-1] == "failed"
    assert exported["control_plane_attempts"] == [result.control_plane_evidence["attempt"]]
    kept = exported["control_plane_evidence"]["reconciliation"]
    assert kept == result.control_plane_evidence["reconciliation"]
    assert kept["observation"]["state"] == "absent" and kept["observation_accepted"] is True
    assert codes(export_to_document(exported)) == []


def test_unknown_result_still_carries_the_validated_absent_observation(tmp_path, control_plane_stub):
    env, dest, _, result = _run(tmp_path, _TimeoutBeforeEffect)
    assert result.status == "unknown" and result.observed_state == "absent"
    assert result.observation["state"] == "absent"
    assert result.control_plane_evidence["reconciliation"]["observation"] == result.observation
    exported = export_execution_artifacts(env, result, dest, repository_revision="test")
    assert exported["observations"] == [exported["execution_result"]["observation"]]
    assert exported["observations"][0]["state"] == exported["execution_result"]["observed_state"] == "absent"
    assert codes(export_to_document(exported)) == []
