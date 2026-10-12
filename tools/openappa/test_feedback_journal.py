"""Durable host bookkeeping; unit cases plus actual pinned feedback integration."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading

import pytest

from tools.openappa.feedback_harness import execute_with_feedback
from tools.openappa.feedback_journal import initialize, JournaledReporter
from tools.openappa.local_resume import Binding, ResumeRefused
from tools.openappa.test_local_join import enrolled
from tools.openappa.test_persistent_join import run_probe, assert_destination


def fixture(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / 'receipts.sqlite'
    initialize(path)
    binding = Binding(os.getuid(), 'synthetic-tenant', 'synthetic-principal',
                      'synthetic-session', 'synthetic-root', str(tmp_path / 'store.sqlite'), 1, 2)
    raw = json.dumps({'context': dict(tenant_id=binding.tenant_id, principal_id=binding.principal_id,
        root_id=binding.root_id, call_id='synthetic-call'), 'arguments': {'marker': 'synthetic-payload-not-for-journal'}}).encode()
    return path, binding, raw


def ack(raw):
    return {'request_sha256': hashlib.sha256(raw).hexdigest(), 'outcome': 'ack'}


def restart(path, binding, raw):
    result = subprocess.run([sys.executable, '-c', '''
import json, sys
from tools.openappa.feedback_journal import JournaledReporter
from tools.openappa.local_resume import Binding
def forbidden(*args):
    raise AssertionError('restart attempted another delivery')
r = JournaledReporter(sys.argv[1], forbidden)(Binding(**json.loads(sys.argv[2])), sys.argv[3].encode())
print(json.dumps(r))
''', str(path), json.dumps(asdict(binding)), raw.decode()], capture_output=True, text=True, check=True, timeout=10)
    return json.loads(result.stdout)


def test_acknowledgment_survives_process_reopen_without_payload_storage(tmp_path):
    path, binding, raw = fixture(tmp_path)
    assert JournaledReporter(path, lambda _, data: ack(data))(binding, raw) == ack(raw)
    assert restart(path, binding, raw) == ack(raw)
    with sqlite3.connect(path) as db:
        rows = db.execute('SELECT * FROM receipt').fetchall()
    assert len(rows) == 1
    assert all(len(value) == 64 for value in rows[0][:2])
    assert rows[0][2] == 'acknowledged'
    assert b'synthetic-payload-not-for-journal' not in path.read_bytes()
    with pytest.raises(FileExistsError):
        initialize(path)


def test_interrupted_delivery_remains_unconfirmed_after_process_reopen(tmp_path):
    path, binding, raw = fixture(tmp_path)
    def unavailable(*args):
        raise OSError('synthetic transport unavailable')
    with pytest.raises(OSError):
        JournaledReporter(path, unavailable)(binding, raw)
    assert restart(path, binding, raw) == {**ack(raw), 'outcome': 'unconfirmed'}


@pytest.mark.parametrize('response', ['refuse', 'wrong-digest'])
def test_invalid_receipt_does_not_confirm(tmp_path, response):
    path, binding, raw = fixture(tmp_path)
    receipt = {**ack(raw), 'outcome': 'refuse'} if response == 'refuse' else {**ack(raw), 'request_sha256': '0' * 64}
    assert JournaledReporter(path, lambda *_: receipt)(binding, raw)['outcome'] == 'unconfirmed'
    assert restart(path, binding, raw)['outcome'] == 'unconfirmed'


def test_reused_call_with_changed_request_is_refused(tmp_path):
    path, binding, raw = fixture(tmp_path)
    reporter = JournaledReporter(path, lambda _, data: ack(data))
    reporter(binding, raw)
    changed = json.loads(raw)
    changed['arguments'] = {'marker': 'synthetic-other'}
    with pytest.raises(ValueError, match='reused'):
        reporter(binding, json.dumps(changed).encode())
    assert restart(path, binding, raw) == ack(raw)


def test_concurrent_callers_claim_one_delivery(tmp_path):
    path, binding, raw = fixture(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def report(_, data):
        calls.append(data)
        entered.set()
        assert release.wait(5)
        return ack(data)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(JournaledReporter(path, report), binding, raw)
        try:
            assert entered.wait(5)
            second = pool.submit(JournaledReporter(path, report), binding, raw)
            assert second.result(timeout=5)['outcome'] == 'unconfirmed'
        finally:
            release.set()
        assert first.result(timeout=5) == ack(raw)
    assert calls == [raw]
    assert restart(path, binding, raw) == ack(raw)


@pytest.mark.parametrize('mutation', ['missing', 'public', 'corrupt'])
def test_unavailable_journal_never_sends(tmp_path, mutation):
    path, binding, raw = fixture(tmp_path)
    if mutation == 'missing':
        path.unlink()
    elif mutation == 'public':
        path.chmod(0o644)
    else:
        path.write_bytes(b'synthetic-invalid-database')
    def forbidden(*args):
        pytest.fail('unavailable journal reached reporter')
    with pytest.raises((ValueError, OSError, ResumeRefused)):
        JournaledReporter(path, forbidden)(binding, raw)
    if mutation == 'missing':
        assert not path.exists()


@pytest.mark.parametrize('delivery', ['success', 'lost-receipt', 'not-sent', 'unavailable-journal'])
def test_actual_feedback_journal_preserves_destination_and_receipt_status(tmp_path, delivery):
    _, decision, destination, arguments, store = enrolled(tmp_path)
    journal = tmp_path / 'receipts.sqlite'
    initialize(journal)
    if delivery == 'unavailable-journal':
        journal.chmod(0o644)
    captured = {}
    def status(binding):
        run_probe(binding.store_path, 'status', json.dumps({'root_id': binding.root_id}).encode())
    def report(binding, raw):
        captured.update(binding=binding, raw=raw)
        if delivery == 'not-sent':
            raise OSError('synthetic unsent feedback')
        receipt = run_probe(binding.store_path, 'feedback', raw)
        assert receipt == ack(raw)
        if delivery == 'lost-receipt':
            raise OSError('synthetic receipt loss after actual feedback')
        return receipt
    left, right = socket.socketpair()
    try:
        outcome = execute_with_feedback(peer=left, **arguments, read_existing=status,
            flow_check=lambda binding, raw: run_probe(binding.store_path, 'check', raw),
            report_success=JournaledReporter(journal, report))
    finally:
        left.close()
        right.close()
    assert outcome.execution_status == 'executed'
    expected = 'acknowledged' if delivery == 'success' else 'unconfirmed'
    assert outcome.feedback_status == expected
    if delivery == 'unavailable-journal':
        assert captured == {}
        assert_destination(destination, decision, True)
        return
    before = run_probe(store, 'audit')
    persisted = restart(journal, captured['binding'], captured['raw'])
    assert persisted['outcome'] == ('ack' if delivery == 'success' else 'unconfirmed')
    assert run_probe(store, 'audit') == before
    assert_destination(destination, decision, True)
