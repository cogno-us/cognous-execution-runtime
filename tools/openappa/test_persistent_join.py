"""Actual persisted flow plus native authority/destination; hosted gate, no skips."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from tools.openappa.conjunction_harness import execute_joined
from tools.openappa.test_conjunction import setup


def run_probe(path, mode, raw=b''):
    binary = os.environ['COGNOUS_OPENAPPA_PERSISTENT_PROBE']
    assert Path(binary).is_file()
    result = subprocess.run([binary, mode, str(path)], input=raw,
                            capture_output=True, timeout=15, check=True)
    return json.loads(result.stdout) if mode == 'check' else None


def prepare(tmp_path):
    h, resolver, decision, destination, arguments = setup(tmp_path)
    arguments['host']['root_id'] = 'synthetic-persistent-root'
    path = tmp_path / 'persistent-flow.sqlite'
    run_probe(path, 'bootstrap')
    return h, resolver, decision, destination, arguments, path


def assert_destination(destination, decision, applied=False):
    assert destination.observe(decision.effect_id)['state'] == ('applied' if applied else 'absent')
    assert destination.effect_count(decision.binding.grant_id) == int(applied)


@pytest.mark.parametrize('restricted,revoked,expected', [
    (False, False, 'executed'), (True, False, 'flow-refused'),
    (False, True, 'denied'), (True, True, 'flow-refused'),
])
def test_persisted_flow_and_current_authority(tmp_path, restricted, revoked, expected):
    _, resolver, decision, destination, arguments, path = prepare(tmp_path)
    if restricted:
        run_probe(path, 'restrict')  # Separate process commits accumulated restriction.
    if revoked:
        resolver.statuses[decision.binding.grant_id].status = 'revoked'
    def flow(raw):
        response = run_probe(path, 'check', raw)  # Reopens existing SQLite each time.
        assert response['outcome'] == ('deny' if restricted else 'allow')
        return response
    assert execute_joined(**arguments, flow_check=flow) == expected
    assert_destination(destination, decision, expected == 'executed')
    if restricted:
        # Reopening again must retain denial and must not produce a destination row.
        assert execute_joined(**arguments, flow_check=flow) == 'flow-refused'
        assert_destination(destination, decision)


def test_revocation_after_resumed_allow_is_revalidated(tmp_path):
    _, resolver, decision, destination, arguments, path = prepare(tmp_path)
    def flow(raw):
        response = run_probe(path, 'check', raw)
        assert response['outcome'] == 'allow'
        resolver.statuses[decision.binding.grant_id].status = 'revoked'
        return response
    assert execute_joined(**arguments, flow_check=flow) == 'denied'
    assert_destination(destination, decision)


@pytest.mark.parametrize('mutation', ['missing-store', 'corrupt-store', 'wrong-root'])
def test_unavailable_persisted_flow_never_reaches_destination(tmp_path, mutation):
    _, _, decision, destination, arguments, path = prepare(tmp_path)
    if mutation == 'missing-store':
        path.unlink()
    elif mutation == 'corrupt-store':
        path.write_bytes(b'synthetic-invalid-database')
    else:
        arguments['host']['root_id'] = 'synthetic-other-root'
    assert execute_joined(**arguments,
        flow_check=lambda raw: run_probe(path, 'check', raw)) == 'flow-unavailable'
    assert_destination(destination, decision)
    if mutation == 'missing-store':
        assert not path.exists()
    elif mutation == 'corrupt-store':
        assert path.read_bytes() == b'synthetic-invalid-database'
