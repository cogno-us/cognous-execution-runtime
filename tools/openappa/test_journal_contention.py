"""Independent host processes must share one durable feedback claim."""
from dataclasses import asdict
import json
import select
import subprocess
import sqlite3
import sys

import pytest

from tools.openappa.feedback_journal import read_receipt
from tools.openappa.test_feedback_journal import ack, fixture, restart


CHILD = r'''
import hashlib, json, sys
from tools.openappa.feedback_journal import JournaledReporter, JournalCapacityExceeded
from tools.openappa.local_resume import Binding
path, binding, raw, deliveries, mode, limit = sys.argv[1:]
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
    result = JournaledReporter(path, report, max_receipts=json.loads(limit))(binding, raw)
except JournalCapacityExceeded:
    result = {'request_sha256': hashlib.sha256(raw).hexdigest(), 'outcome': 'full'}
except OSError:
    result = {'request_sha256': hashlib.sha256(raw).hexdigest(), 'outcome': 'unconfirmed'}
print(json.dumps(result), flush=True)
'''


def run_children(path, binding, requests, deliveries, mode, limit=None):
    children = []
    try:
        for raw in requests:
            children.append(subprocess.Popen(
                [sys.executable, '-c', CHILD, str(path), json.dumps(asdict(binding)),
                 raw.decode(), str(deliveries), mode, json.dumps(limit)],
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
        return results
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


@pytest.mark.parametrize('mode', ['ack', 'lost-receipt'])
def test_independent_processes_share_one_durable_claim(tmp_path, mode):
    path, binding, raw = fixture(tmp_path)
    deliveries = tmp_path / 'synthetic-deliveries.txt'
    results = run_children(path, binding, [raw] * 6, deliveries, mode)
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


@pytest.mark.parametrize('mode', ['ack', 'lost-receipt'])
def test_independent_processes_share_capacity_after_restart(tmp_path, mode):
    path, binding, raw = fixture(tmp_path)
    deliveries = tmp_path / 'synthetic-deliveries.txt'
    requests = []
    for i in range(6):
        request = json.loads(raw)
        request['context']['call_id'] = f'synthetic-call-{i}'
        requests.append(json.dumps(request).encode())
    results = run_children(path, binding, requests, deliveries, mode, limit=2)
    expected = 'ack' if mode == 'ack' else 'unconfirmed'
    assert [result['outcome'] for result in results].count(expected) == 2
    assert [result['outcome'] for result in results].count('full') == 4
    assert deliveries.read_text().splitlines() == ['delivered'] * 2
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM receipt').fetchone()[0] == 2
    for request, result in zip(requests, results, strict=True):
        assert result['request_sha256'] == ack(request)['request_sha256']
        assert read_receipt(path, binding, request) == {
            **ack(request),
            'outcome': 'absent' if result['outcome'] == 'full' else expected,
        }
    # New processes must retain both claims, including lost receipts, and must
    # not deliver either old or previously refused requests into the full journal.
    before = path.read_bytes()
    assert run_children(path, binding, requests, deliveries, mode, limit=2) == results
    assert path.read_bytes() == before
    assert deliveries.read_text().splitlines() == ['delivered'] * 2
