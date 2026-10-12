"""Independent host processes must share one durable feedback claim."""
from dataclasses import asdict
import json
import select
import subprocess
import sys

import pytest

from tools.openappa.test_feedback_journal import ack, fixture, restart


CHILD = r'''
import hashlib, json, sys
from tools.openappa.feedback_journal import JournaledReporter
from tools.openappa.local_resume import Binding
path, binding, raw, deliveries, mode = sys.argv[1:]
binding = Binding(**json.loads(binding))
raw = raw.encode()
def report(_, data):
    with open(deliveries, 'a') as output:
        output.write('delivered\n')
        output.flush()
    if mode == 'lost-receipt':
        raise OSError('synthetic receipt loss')
    return {'request_sha256': hashlib.sha256(data).hexdigest(), 'outcome': 'ack'}
print('ready', flush=True)
assert sys.stdin.readline() == 'go\n'
try:
    result = JournaledReporter(path, report)(binding, raw)
except OSError:
    result = {'request_sha256': hashlib.sha256(raw).hexdigest(), 'outcome': 'unconfirmed'}
print(json.dumps(result), flush=True)
'''


@pytest.mark.parametrize('mode', ['ack', 'lost-receipt'])
def test_independent_processes_share_one_durable_claim(tmp_path, mode):
    path, binding, raw = fixture(tmp_path)
    deliveries = tmp_path / 'synthetic-deliveries.txt'
    children = []
    try:
        for _ in range(6):
            children.append(subprocess.Popen(
                [sys.executable, '-c', CHILD, str(path), json.dumps(asdict(binding)),
                 raw.decode(), str(deliveries), mode],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            ))
        # Each child completes imports before release; readiness and completion
        # are bounded, and only these owned children are killed on failure.
        for child in children:
            readable, _, _ = select.select([child.stdout], [], [], 15)
            assert readable, 'child did not become ready'
            assert child.stdout.readline() == 'ready\n'
        for child in children:
            child.stdin.write('go\n')
            child.stdin.flush()
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=15)
            assert child.returncode == 0, stderr
            results.append(json.loads(stdout))
        assert deliveries.read_text().splitlines() == ['delivered']
        assert all(result['request_sha256'] == ack(raw)['request_sha256'] for result in results)
        assert all(result['outcome'] in {'ack', 'unconfirmed'} for result in results)
        if mode == 'ack':
            assert any(result['outcome'] == 'ack' for result in results)
            assert restart(path, binding, raw) == ack(raw)
        else:
            assert all(result['outcome'] == 'unconfirmed' for result in results)
            assert restart(path, binding, raw) == {**ack(raw), 'outcome': 'unconfirmed'}
        assert deliveries.read_text().splitlines() == ['delivered']
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
