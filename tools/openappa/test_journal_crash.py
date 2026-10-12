"""Owned child-process termination at explicit feedback journal checkpoints."""
from dataclasses import asdict
import json
import signal
import socket
import subprocess
import sys

import pytest

from tools.openappa.feedback_harness import execute_with_feedback
from tools.openappa.feedback_journal import initialize
from tools.openappa.test_feedback_journal import fixture, restart
from tools.openappa.test_local_join import enrolled
from tools.openappa.test_persistent_join import run_probe, assert_destination


CHILD = '''
import hashlib, json, os, signal, sys
from tools.openappa.feedback_journal import JournaledReporter
from tools.openappa.local_resume import Binding
path, binding_json, request, point, actual = sys.argv[1:]
binding = Binding(**json.loads(binding_json))
raw = request.encode()
def terminate():
    os.kill(os.getpid(), signal.SIGKILL)
def report(binding, raw):
    if point == 'claimed':
        terminate()
    if actual == 'yes':
        from tools.openappa.test_persistent_join import run_probe
        receipt = run_probe(binding.store_path, 'feedback', raw)
    else:
        receipt = {'request_sha256': hashlib.sha256(raw).hexdigest(), 'outcome': 'ack'}
    assert receipt == {'request_sha256': hashlib.sha256(raw).hexdigest(), 'outcome': 'ack'}
    if point == 'delivered':
        terminate()
    return receipt
result = JournaledReporter(path, report)(binding, raw)
assert result['outcome'] == 'ack'
assert point == 'recorded'
terminate()
'''


def killed_reporter(path, binding, raw, point, *, actual=False):
    child = subprocess.run([sys.executable, '-c', CHILD, str(path), json.dumps(asdict(binding)),
                            raw.decode(), point, 'yes' if actual else 'no'],
                           capture_output=True, text=True, timeout=30)
    assert child.returncode == -signal.SIGKILL, child.stderr


@pytest.mark.parametrize('point,expected', [('claimed', 'unconfirmed'), ('recorded', 'ack')])
def test_journal_survives_owned_process_sigkill(tmp_path, point, expected):
    path, binding, raw = fixture(tmp_path)
    killed_reporter(path, binding, raw, point)
    # The restarted process has a callback that fails if any delivery is attempted.
    assert restart(path, binding, raw)['outcome'] == expected


@pytest.mark.parametrize('point', ['claimed', 'delivered', 'recorded'])
def test_actual_feedback_sigkill_keeps_effect_and_receipt_distinct(tmp_path, point):
    _, decision, destination, arguments, store = enrolled(tmp_path)
    journal = tmp_path / 'receipts.sqlite'
    initialize(journal)
    captured = {}
    executions = []
    original = arguments.pop('executor')
    class Executor:
        def execute(self, **kwargs):
            executions.append(kwargs)
            return original.execute(**kwargs)
    def status(binding):
        run_probe(binding.store_path, 'status', json.dumps({'root_id': binding.root_id}).encode())
    def report(binding, raw):
        captured.update(binding=binding, raw=raw)
        killed_reporter(journal, binding, raw, point, actual=True)
        raise OSError('synthetic host child terminated before returning receipt')
    before = run_probe(store, 'audit')
    left, right = socket.socketpair()
    try:
        outcome = execute_with_feedback(peer=left, **arguments, executor=Executor(),
            read_existing=status, flow_check=lambda binding, raw: run_probe(binding.store_path, 'check', raw),
            report_success=report)
    finally:
        left.close()
        right.close()
    assert (outcome.execution_status, outcome.feedback_status) == ('executed', 'unconfirmed')
    persisted = restart(journal, captured['binding'], captured['raw'])
    assert persisted['outcome'] == ('ack' if point == 'recorded' else 'unconfirmed')
    after = run_probe(store, 'audit')
    closed = lambda entries: sum(
        e['trajectory'] == 'synthetic-persistent-root'
        and e['event'] == {'Closed': {'outcome': {'Ran': {'effects': ['egress']}}}}
        for e in entries)
    assert closed(after) - closed(before) == int(point != 'claimed')
    assert restart(journal, captured['binding'], captured['raw']) == persisted
    assert run_probe(store, 'audit') == after
    assert len(executions) == 1
    assert_destination(destination, decision, True)
