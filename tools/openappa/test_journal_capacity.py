"""Optional receipt capacity must preserve evidence and refuse before feedback."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import socket
import sqlite3
import subprocess
import sys
import threading

import pytest

from tools.openappa.feedback_harness import execute_with_feedback
from tools.openappa.feedback_journal import JournalCapacityExceeded, JournaledReporter, read_receipt
from tools.openappa.test_feedback_journal import ack, fixture
from tools.openappa.test_local_join import enrolled
from tools.openappa.test_persistent_join import assert_destination, run_probe


def another(raw, call_id):
    request = json.loads(raw)
    request['context']['call_id'] = call_id
    return json.dumps(request).encode()


def forbidden(*args):
    pytest.fail('capacity refusal or existing claim reached delivery')


@pytest.mark.parametrize('limit', [0, -1, True, 1.5, '2'])
def test_invalid_capacity_does_not_create_state(tmp_path, limit):
    path = tmp_path / 'missing.sqlite'
    with pytest.raises(ValueError, match='positive integer'):
        JournaledReporter(path, forbidden, max_receipts=limit)
    assert not path.exists()


@pytest.mark.parametrize('state', ['ack', 'unconfirmed'])
def test_full_journal_preserves_existing_receipts(tmp_path, state):
    path, binding, raw = fixture(tmp_path)
    expected = {**ack(raw), 'outcome': state}
    reporter = JournaledReporter(path, lambda *_: expected, max_receipts=1)
    assert reporter(binding, raw) == expected
    before = path.read_bytes()
    full = JournaledReporter(path, forbidden, max_receipts=1)
    assert full(binding, raw) == expected
    assert read_receipt(path, binding, raw) == expected
    new = another(raw, 'synthetic-new')
    with pytest.raises(JournalCapacityExceeded):
        full(binding, new)
    assert read_receipt(path, binding, new)['outcome'] == 'absent'
    assert path.read_bytes() == before


def test_existing_identity_mismatch_still_refuses_when_full(tmp_path):
    path, binding, raw = fixture(tmp_path)
    reporter = JournaledReporter(path, lambda _, data: ack(data), max_receipts=1)
    reporter(binding, raw)
    request = json.loads(raw)
    request['arguments'] = {'marker': 'synthetic-changed'}
    with pytest.raises(ValueError, match='identity was reused'):
        reporter(binding, json.dumps(request).encode())
    assert read_receipt(path, binding, raw) == ack(raw)


def test_omitted_limit_preserves_existing_behavior(tmp_path):
    path, binding, raw = fixture(tmp_path)
    reporter = JournaledReporter(path, lambda _, data: ack(data))
    for i in range(3):
        request = another(raw, f'synthetic-{i}')
        assert reporter(binding, request) == ack(request)
    # Lowering a trusted host limit never deletes existing rows.
    with pytest.raises(JournalCapacityExceeded):
        JournaledReporter(path, forbidden, max_receipts=1)(binding, raw)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM receipt').fetchone()[0] == 3


def test_concurrent_distinct_claims_cannot_exceed_capacity(tmp_path):
    path, binding, raw = fixture(tmp_path)
    barrier = threading.Barrier(6)
    delivered = []
    def report(_, data):
        delivered.append(data)
        return ack(data)
    def claim(i):
        request = another(raw, f'synthetic-{i}')
        reporter = JournaledReporter(path, report, max_receipts=2)
        barrier.wait(timeout=5)
        try:
            return reporter(binding, request)['outcome']
        except JournalCapacityExceeded:
            return 'full'
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(claim, range(6)))
    assert results.count('ack') == 2
    assert results.count('full') == 4
    assert len(delivered) == 2
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM receipt').fetchone()[0] == 2
    for request in delivered:
        assert read_receipt(path, binding, request) == ack(request)


def test_capacity_reconstructed_from_rows_after_process_restart(tmp_path):
    path, binding, raw = fixture(tmp_path)
    JournaledReporter(path, lambda _, data: ack(data), max_receipts=1)(binding, raw)
    result = subprocess.run([sys.executable, '-c', '''
import json, sys
from tools.openappa.feedback_journal import JournaledReporter, JournalCapacityExceeded
from tools.openappa.local_resume import Binding
binding = Binding(**json.loads(sys.argv[2]))
def forbidden(*args):
    raise AssertionError('restart delivered feedback')
reporter = JournaledReporter(sys.argv[1], forbidden, max_receipts=1)
assert reporter(binding, sys.argv[3].encode())['outcome'] == 'ack'
try:
    reporter(binding, sys.argv[4].encode())
except JournalCapacityExceeded:
    print('full')
else:
    raise AssertionError('restart lost capacity')
''', str(path), json.dumps(asdict(binding)), raw.decode(),
        another(raw, 'synthetic-new').decode()], capture_output=True, text=True, timeout=10, check=True)
    assert result.stdout.strip() == 'full'


def test_actual_execution_stays_executed_when_feedback_capacity_is_full(tmp_path):
    journal, binding, raw = fixture(tmp_path)
    JournaledReporter(journal, lambda _, data: ack(data), max_receipts=1)(binding, raw)
    before = journal.read_bytes()
    _, decision, destination, arguments, _ = enrolled(tmp_path)
    left, right = socket.socketpair()
    try:
        outcome = execute_with_feedback(peer=left, **arguments,
            read_existing=lambda bound: run_probe(bound.store_path, 'status',
                json.dumps({'root_id': bound.root_id}).encode()),
            flow_check=lambda bound, data: run_probe(bound.store_path, 'check', data),
            report_success=JournaledReporter(journal, forbidden, max_receipts=1))
    finally:
        left.close()
        right.close()
    assert outcome.execution_status == 'executed'
    assert outcome.feedback_status == 'unconfirmed'
    assert_destination(destination, decision, True)
    assert journal.read_bytes() == before
