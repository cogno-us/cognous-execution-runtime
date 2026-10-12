"""Requires actual pinned Control Plane and compiled upstream runtime; no skips."""
import dataclasses
import importlib.util
import os
from pathlib import Path

import pytest

from tools.openappa.conjunction_harness import execute_joined, probe


def setup(tmp_path):
    for name in ('MOLTBOT_SAFE_CONTROL_PLANE_ROOT', 'MOLTBOT_SAFE_MANIFEST_FIXTURE',
                 'COGNOUS_OPENAPPA_PROBE'):
        assert os.environ.get(name), f'{name} is required; this gate must not skip'
        assert Path(os.environ[name]).exists()
    source = Path(__file__).parents[2] / 'tests' / 'test_safe_executor.py'
    spec = importlib.util.spec_from_file_location('native_executor_tests', source)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    h, p, resolver, workflow, decision, destination, executor, request = helper._integrated(tmp_path)
    request = dataclasses.replace(request, attempt_id='synthetic-attempt')
    context = resolver.authority_context(p.authority_context_ref)['institution']
    host = dict(tenant_id='synthetic-test-tenant', principal_id=p.principal,
                institution_id=context['institution_id'], authority_domain=context['authority_domain'],
                operation_id='synthetic-operation', attempt_id=request.attempt_id,
                effect_id=request.effect_id, root_id='synthetic-root', call_id='synthetic-call')
    arguments = dict(request=request, host=host, executor=executor, proposal=p,
                     decision=decision, now=h.NOW)
    return h, resolver, decision, destination, arguments


@pytest.mark.parametrize('flow,revoked,expected', [
    ('allow', False, 'executed'), ('deny', False, 'flow-refused'),
    ('allow', True, 'denied'), ('deny', True, 'flow-refused'),
])
def test_actual_two_sided_decisions_and_destination(tmp_path, flow, revoked, expected):
    h, resolver, decision, destination, arguments = setup(tmp_path)
    if revoked:
        resolver.statuses[decision.binding.grant_id].status = 'revoked'
    result = execute_joined(**arguments,
        flow_check=lambda raw: probe(os.environ['COGNOUS_OPENAPPA_PROBE'], flow, raw))
    assert result == expected
    applied = expected == 'executed'
    assert destination.observe(decision.effect_id)['state'] == ('applied' if applied else 'absent')
    assert destination.effect_count(decision.binding.grant_id) == int(applied)


@pytest.mark.parametrize('mutation', ['digest', 'unknown', 'malformed', 'unavailable'])
def test_invalid_actual_response_never_writes(tmp_path, mutation):
    _, _, decision, destination, arguments = setup(tmp_path)
    def flow_check(raw):
        response = probe(os.environ['COGNOUS_OPENAPPA_PROBE'], 'allow', raw)
        assert response['outcome'] == 'allow'
        if mutation == 'digest':
            response['request_sha256'] = '0' * 64
        elif mutation == 'unknown':
            response['outcome'] = 'unknown'
        elif mutation == 'malformed':
            return []
        else:
            raise OSError('synthetic unavailable process')
        return response
    assert execute_joined(**arguments, flow_check=flow_check) in ('flow-refused', 'flow-unavailable')
    assert destination.observe(decision.effect_id)['state'] == 'absent'
    assert destination.effect_count(decision.binding.grant_id) == 0


def test_revocation_during_real_flow_check_is_revalidated(tmp_path):
    _, resolver, decision, destination, arguments = setup(tmp_path)
    def flow_check(raw):
        response = probe(os.environ['COGNOUS_OPENAPPA_PROBE'], 'allow', raw)
        assert response['outcome'] == 'allow'
        resolver.statuses[decision.binding.grant_id].status = 'revoked'
        return response
    assert execute_joined(**arguments, flow_check=flow_check) == 'denied'
    assert destination.observe(decision.effect_id)['state'] == 'absent'
    assert destination.effect_count(decision.binding.grant_id) == 0


@pytest.mark.parametrize('field', ['target', 'tenant_id', 'operation_id', 'attempt_id', 'root_id', 'call_id'])
def test_response_for_another_operation_is_rejected(tmp_path, field):
    import json
    _, _, decision, destination, arguments = setup(tmp_path)
    def flow_check(raw):
        changed = json.loads(raw)
        if field == 'target':
            changed['arguments']['target'] = 'urn:cognous:synthetic-account:other'
        else:
            changed['context'][field] = 'synthetic-other'
        response = probe(os.environ['COGNOUS_OPENAPPA_PROBE'], 'allow', json.dumps(changed).encode())
        assert response['outcome'] == 'allow'
        return response
    assert execute_joined(**arguments, flow_check=flow_check) == 'flow-refused'
    assert destination.observe(decision.effect_id)['state'] == 'absent'
    assert destination.effect_count(decision.binding.grant_id) == 0


@pytest.mark.parametrize('field', ['principal_id', 'authority_domain', 'institution_id', 'attempt_id', 'effect_id'])
def test_host_native_identity_mismatch_never_reaches_flow_or_destination(tmp_path, field):
    _, _, decision, destination, arguments = setup(tmp_path)
    arguments['host'][field] = 'synthetic-other'
    def unexpected_flow(raw):
        pytest.fail('mismatched native identity reached flow check')
    with pytest.raises(ValueError, match='host .* mismatch'):
        execute_joined(**arguments, flow_check=unexpected_flow)
    assert destination.observe(decision.effect_id)['state'] == 'absent'
    assert destination.effect_count(decision.binding.grant_id) == 0
