"""Actual destination execution and separately acknowledged flow feedback."""
import dataclasses
import json
import socket

import pytest

from tools.openappa.feedback_harness import execute_with_feedback
from tools.openappa.test_local_join import enrolled
from tools.openappa.test_persistent_join import run_probe, assert_destination


@pytest.mark.parametrize('case,expected_status,expected_feedback,delivered', [
    ('success', 'executed', 'acknowledged', True),
    ('unavailable', 'executed', 'unconfirmed', False),
    ('lost-receipt', 'executed', 'unconfirmed', True),
    ('wrong-receipt', 'executed', 'unconfirmed', True),
    ('wrong-result-id', 'executed', 'unconfirmed', False),
    ('flow-denied', 'flow-refused', 'not-reported', False),
    ('revoked', 'denied', 'not-reported', False),
    ('partial', 'partial', 'not-reported', False),
    ('lost_ack', 'unknown', 'not-reported', False),
])
def test_actual_execution_and_feedback_are_distinct(tmp_path, case, expected_status, expected_feedback, delivered):
    resolver, decision, destination, arguments, path = enrolled(tmp_path)
    if case == 'flow-denied':
        run_probe(path, 'restrict')
    if case == 'revoked':
        resolver.statuses[decision.binding.grant_id].status = 'revoked'
    before = run_probe(path, 'audit')
    original = arguments.pop('executor')
    executions = []
    reports = []
    class Executor:
        def execute(self, **kwargs):
            executions.append(kwargs)
            result = original.execute(**kwargs, simulate=case if case in ('partial', 'lost_ack') else None)
            return dataclasses.replace(result, effect_id='synthetic-other') if case == 'wrong-result-id' else result
    def status(binding):
        run_probe(binding.store_path, 'status', json.dumps({'root_id': binding.root_id}).encode())
    def report(binding, raw):
        reports.append(raw)
        assert binding.store_path == str(path)
        if case == 'unavailable':
            raise OSError('synthetic unavailable feedback process')
        receipt = run_probe(binding.store_path, 'feedback', raw)
        if case == 'lost-receipt':
            raise OSError('synthetic lost feedback receipt')
        if case == 'wrong-receipt':
            receipt['request_sha256'] = '0' * 64
        return receipt
    left, right = socket.socketpair()
    try:
        outcome = execute_with_feedback(peer=left, **arguments, executor=Executor(),
            read_existing=status, flow_check=lambda binding, raw: run_probe(binding.store_path, 'check', raw),
            report_success=report)
    finally:
        left.close()
        right.close()
    assert (outcome.execution_status, outcome.feedback_status) == (expected_status, expected_feedback)
    assert len(executions) == int(case != 'flow-denied')  # No automatic retry after feedback failure.
    assert len(reports) == int(case in ('success', 'unavailable', 'lost-receipt', 'wrong-receipt'))
    if expected_status == 'executed':
        assert_destination(destination, decision, True)
    elif case in ('flow-denied', 'revoked'):
        assert_destination(destination, decision)
    else:
        assert destination.observe(decision.effect_id)['state'] == ('partial' if case == 'partial' else 'applied')
        assert destination.effect_count(decision.binding.grant_id) == 1
    # Actual audit is reopened in a different process; receipt loss may still
    # follow persisted feedback. It must not rewrite execution as a failure.
    after = run_probe(path, 'audit')
    committed = lambda entries: sum('EffectsCommitted' in e['event'] for e in entries)
    assert committed(after) - committed(before) == int(delivered)
