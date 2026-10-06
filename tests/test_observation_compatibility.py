"""Accepted CP contract against the real SQLite executor; faults at observation seam."""
from datetime import timedelta

import pytest

from engine.control_plane_adapter import ControlPlaneRefundDestinationAdapter, PinnedControlPlaneExecutor
from engine.producer_contract import export_execution_artifacts
from engine.safe_executor import DurableRefundDestination, snapshot_envelope
from test_safe_executor import _integrated


@pytest.mark.parametrize('fault,reason', [
    ('wrong', 'observation_effect_id_mismatch'),
    ('stale', 'observation_stale'),
    ('malformed', 'observation_time_malformed'),
    ('contradictory', 'destination_state_contradiction'),
    ('unavailable', 'observation_unavailable:OSError'),
])
def test_post_dispatch_rejection_and_restart(tmp_path, monkeypatch, fault, reason):
    h,p,_,workflow,decision,destination,executor,request = _integrated(tmp_path)
    original = ControlPlaneRefundDestinationAdapter.observe

    def observe(adapter, effect_id):
        value = original(adapter, effect_id)
        # Fault only the CP post-dispatch observation, not apply/ack or preflight.
        if adapter.outcome.result is not None:
            if fault == 'unavailable':
                raise OSError('injected destination observation outage')
            if fault == 'wrong':
                value.effect_id = 'different-effect'
            elif fault == 'stale':
                value.observed_at = (h.NOW - timedelta(seconds=61)).isoformat()
            elif fault == 'malformed':
                value.observed_at = 'not-a-time'
            else:
                value.destination_state['state'] = 'absent'
        return value

    monkeypatch.setattr(ControlPlaneRefundDestinationAdapter, 'observe', observe)
    assert destination.effect_count(request.operation.grant_id) == 0
    result = executor.execute(envelope=request, proposal=p, decision=decision, now=h.NOW)
    assert result.status == 'unknown' and result.observation is None
    assert result.acknowledged and result.newly_executed and result.attempted
    assert result.observed_state == 'unknown'
    evidence = result.control_plane_evidence
    assert evidence['attempt']['status'] == 'acknowledged'
    assert evidence['attempt']['acknowledgement']['attempt_id'] == result.attempt_id
    assert reason in evidence['reconciliation']['reasons']
    assert not evidence['reconciliation']['observation_accepted']
    assert not evidence['reconciliation']['retry_eligible']
    exported = export_execution_artifacts(request, result, destination)
    assert exported['observations'] == []
    assert len(exported['rejected_observations']) == (fault != 'unavailable')
    assert exported['attempt_identity']['namespace'] == 'executor'
    assert exported['control_plane_attempts'][0]['attempt_id'] != result.attempt_id
    assert exported['execution_result']['observation'] is None
    _assert_effect(destination, request, 'applied')

    monkeypatch.setattr(ControlPlaneRefundDestinationAdapter, 'observe', original)
    # Reopen both actual durable stores; no replacement identity or dispatch.
    workflow.records = h.BoundedRecordStore(workflow.records.path, workflow.records.run_id)
    restarted = PinnedControlPlaneExecutor(workflow=workflow,
        destination=DurableRefundDestination(destination.root), policy=executor.policy,
        observation_clock=lambda: h.NOW)
    recovered = restarted.execute(envelope=request, proposal=p, decision=decision, now=h.NOW)
    assert recovered.status == 'reconciled' and not recovered.newly_executed
    assert recovered.effect_id == result.effect_id == decision.effect_id
    assert recovered.decision_id == result.decision_id == decision.decision_id
    after = export_execution_artifacts(request, recovered, restarted.destination)
    assert len(after['attempts']) == 1
    assert after['attempts'][0]['attempt_id'] == result.attempt_id
    assert after['attempt_identity']['namespace'] == 'control_plane'
    _assert_effect(restarted.destination, request, 'applied')


def _assert_effect(destination, request, state):
    assert destination.effect_count(request.operation.grant_id) == 1
    actual = destination.observe(request.effect_id)
    assert actual['state'] == state
    op = request.operation
    assert actual['destination_state'] == {
        'effect_id': request.effect_id, 'state': state, 'grant_id': op.grant_id,
        'target': op.target, 'amount': op.amount, 'unit': op.unit,
        'payload': op.payload, 'operation_digest': snapshot_envelope(request).operation.digest,
    }


def test_policy_and_explicit_reconciliation_time(tmp_path):
    h,p,_,workflow,decision,destination,executor,request = _integrated(tmp_path)
    workflow.observation_policy = h.ObservationPolicy(max_age_seconds=17, clock_tolerance_seconds=0)
    result = executor.execute(envelope=request, proposal=p, decision=decision, now=h.NOW)
    assert result.status == 'executed'
    rec = result.control_plane_evidence['reconciliation']
    assert rec['observation_max_age_seconds'] == 17
    assert rec['observation_clock_tolerance_seconds'] == 0
    assert rec['evaluation_time'] == h.NOW.isoformat()
    assert executor.reconcile(request, now=h.NOW).result == 'applied'
    assert executor.reconcile(request, now=h.NOW.replace(tzinfo=None)).result == 'hold'
    with pytest.raises(TypeError):
        executor.reconcile(request)
    _assert_effect(destination, request, 'applied')
    export_execution_artifacts(request, result, destination)


def test_missing_policy_fails_closed(tmp_path):
    h,p,_,workflow,decision,destination,executor,request = _integrated(tmp_path)
    workflow.observation_policy = None
    result = executor.execute(envelope=request, proposal=p, decision=decision, now=h.NOW)
    assert result.status == 'denied' and not result.attempted
    assert 'observation_policy_missing' in result.control_plane_evidence['reconciliation']['reasons']
    assert destination.effect_count(request.operation.grant_id) == 0
    assert export_execution_artifacts(request, result, destination)['observations'] == []


def test_absence_after_attempt_never_resubmits(tmp_path, monkeypatch):
    h,p,_,workflow,decision,destination,executor,request = _integrated(tmp_path)
    def timeout(*args, **kwargs):
        raise TimeoutError('injected interruption before local commit')
    with monkeypatch.context() as patch:
        patch.setattr(destination, 'commit', timeout)
        first = executor.execute(envelope=request, proposal=p, decision=decision, now=h.NOW)
    assert first.status == 'unknown' and not first.acknowledged
    export_execution_artifacts(request, first, destination)
    rec = executor.reconcile(request, now=h.NOW)
    assert rec.result == 'observed_absent' and rec.observation_accepted
    assert not rec.retry_eligible
    second = executor.execute(envelope=request, proposal=p, decision=decision, now=h.NOW)
    assert second.status == 'denied' and not second.attempted
    assert 'prior attempt' in second.error
    exported = export_execution_artifacts(request, second, destination)
    assert len(exported['attempts']) == 1 and exported['effects'] == []
    assert destination.observe(request.effect_id)['state'] == 'absent'
    assert destination.effect_count(request.operation.grant_id) == 0
    # Legacy parsing is readability, never permission from current reconciliation.
    legacy = rec.model_copy(update={'result': 'safe_to_retry'})
    assert type(rec).model_validate_json(legacy.model_dump_json()).result == 'safe_to_retry'
    assert not executor.reconcile(request, now=h.NOW).retry_eligible


@pytest.mark.parametrize('simulate,state,status', [('lost_ack','applied','unknown'), ('partial','partial','partial')])
def test_lost_ack_and_partial_export(tmp_path, simulate, state, status):
    h,p,_,_,decision,destination,executor,request = _integrated(tmp_path)
    result = executor.execute(envelope=request, proposal=p, decision=decision, now=h.NOW, simulate=simulate)
    assert result.status == status
    assert result.acknowledged == (simulate == 'partial')
    assert result.observed_state == state
    _assert_effect(destination, request, state)
    exported = export_execution_artifacts(request, result, destination)
    assert exported['observations'][0]['state'] == state
    assert exported['rejected_observations'] == []
    recovered = executor.execute(envelope=request, proposal=p, decision=decision, now=h.NOW)
    assert not recovered.newly_executed
    assert len(export_execution_artifacts(request, recovered, destination)['attempts']) == 1
    _assert_effect(destination, request, state)


def test_export_rejects_promoted_rejected_observation(tmp_path):
    import copy
    h,p,_,_,decision,destination,executor,request = _integrated(tmp_path)
    result = executor.execute(envelope=request, proposal=p, decision=decision, now=h.NOW)
    promoted = copy.deepcopy(result)
    promoted.control_plane_evidence['reconciliation']['observation_accepted'] = False
    with pytest.raises(ValueError, match='rejected or substituted'):
        export_execution_artifacts(request, promoted, destination)
    substituted = copy.deepcopy(result)
    substituted.control_plane_evidence['reconciliation']['effect_id'] = 'other'
    with pytest.raises(ValueError, match='reconciliation effect binding'):
        export_execution_artifacts(request, substituted, destination)


@pytest.fixture(autouse=True)
def retain_optional_evidence(request, monkeypatch):
    """Optional durable scenario transcript from the same exports tests assert."""
    import json
    import os
    import sys
    from pathlib import Path
    target = os.environ.get('MOLTBOT_SAFE_COMPAT_EVIDENCE')
    if not target:
        yield
        return
    exports = []
    original = export_execution_artifacts
    def capture(*args, **kwargs):
        value = original(*args, **kwargs)
        exports.append({key: value[key] for key in (
            'execution_result', 'attempt_identity', 'control_plane_attempts',
            'observations', 'rejected_observations', 'effects',
        )})
        return value
    monkeypatch.setattr(sys.modules[__name__], 'export_execution_artifacts', capture)
    yield
    path = Path(target)
    data = json.loads(path.read_text()) if path.exists() else {}
    data[request.node.name] = exports
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
