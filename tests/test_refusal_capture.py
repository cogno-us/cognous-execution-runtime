"""Real CP durable refusal -> per-call executor/export evidence; no new authority."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from datetime import datetime, timezone

import pytest

from engine.producer_contract import export_execution_artifacts
from test_safe_executor import _integrated


@pytest.fixture(autouse=True)
def fixture_service_clock(monkeypatch):
    import agent_control_plane.bounded as cp
    monkeypatch.setattr(cp, '_now', lambda: datetime(2026, 10, 5, 19, tzinfo=timezone.utc))


@pytest.mark.parametrize('change,reason', [
    ('grant', 'grant_not_active'), ('approval', 'approval_not_active'),
    ('policy', 'policy_version_changed'), ('evidence', 'required_evidence_not_current'),
])
def test_actual_durable_refusal_survives_export(tmp_path, monkeypatch, change, reason):
    h, p, resolver, workflow, decision, destination, executor, request = _integrated(tmp_path)
    # This suite is deliberately for the exact new CP interface, not older pins.
    import agent_control_plane.bounded as cp
    monkeypatch.setattr(cp, '_now', lambda: h.NOW)
    if change == 'grant': next(iter(resolver.statuses.values())).status = 'revoked'
    elif change == 'approval': next(iter(resolver.approvals.values())).status = 'revoked'
    elif change == 'policy': next(iter(resolver.policies.values())).version = 'changed'
    else: next(iter(resolver.evidence.values())).state = 'stale'
    result = executor.execute(envelope=request, proposal=p, decision=decision, now=h.NOW)
    actual = workflow.records.load().refusals[-1].model_dump(mode='json', exclude_none=False)
    assert result.status == 'denied' and not result.attempted
    assert actual['decision_id'] == decision.decision_id and actual['effect_id'] == decision.effect_id
    assert reason in actual['reasons']
    assert result.control_plane_evidence['refusal'] == actual
    exported = export_execution_artifacts(request, result, destination)
    assert exported['control_plane_evidence']['refusal'] == actual
    assert exported['execution_result']['control_plane_evidence']['refusal'] == actual
    assert destination.observe(request.effect_id)['state'] == 'absent'
    assert workflow.destination is not destination  # caller workflow never rebound


@pytest.mark.parametrize('error', [OSError('injected refusal disk failure'), PermissionError('injected refusal write refusal')])
def test_failed_write_never_claims_durable_refusal(tmp_path, monkeypatch, error):
    h,p,resolver,workflow,decision,destination,executor,request = _integrated(tmp_path)
    next(iter(resolver.statuses.values())).status = 'revoked'
    def fail(value): raise error
    monkeypatch.setattr(workflow.records, 'append_refusal', fail)
    if isinstance(error, PermissionError):
        result = executor.execute(envelope=request, proposal=p, decision=decision, now=h.NOW)
        assert result.status == 'denied' and 'refusal' not in result.control_plane_evidence
    else:
        with pytest.raises(OSError, match='injected refusal disk failure'):
            executor.execute(envelope=request, proposal=p, decision=decision, now=h.NOW)
    assert workflow.records.load().refusals == []
    assert destination.observe(request.effect_id)['state'] == 'absent'


def test_concurrent_calls_capture_only_their_own_durable_record(tmp_path, monkeypatch):
    h,p,resolver,workflow,decision,destination,executor,request = _integrated(tmp_path)
    other = workflow.decide(p, now=h.NOW)
    other_request = replace(request, decision_id=other.decision_id, effect_id=other.effect_id)
    next(iter(resolver.statuses.values())).status = 'revoked'
    original = workflow.records.append_refusal
    both_written = Barrier(2)
    def append(value):
        original(value)
        # Both store writes precede either executor's result, exposing any
        # shared-store "last refusal" scan or shared mutable capture.
        both_written.wait(timeout=10)
    monkeypatch.setattr(workflow.records, 'append_refusal', append)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(executor.execute, envelope=e, proposal=p, decision=d, now=h.NOW)
                   for e,d in [(request,decision),(other_request,other)]]
        results = [f.result(timeout=20) for f in futures]
    records = {r.decision_id:r.model_dump(mode='json', exclude_none=False) for r in workflow.records.load().refusals}
    assert len(records) == 2
    for result, d in zip(results, [decision,other]):
        assert result.control_plane_evidence['refusal'] == records[d.decision_id]
        assert result.status == 'denied' and not result.attempted
    assert destination.observe(request.effect_id)['state'] == 'absent'
