"""Regression checks for Security Cloud execution-admission findings."""
from dataclasses import replace
import os
import sqlite3
import sys

import pytest

from engine.safe_executor import (
    DurableRefundDestination, ExecutionEnvelope, EXECUTION_ENVELOPE_VERSION,
    LocalDestinationExecutor, snapshot_envelope, snapshot_or_record_rejection,
)
from engine.openshell_environment import _bounded_cli_output
from test_producer_contract import operation, policy


def envelope(op=None):
    return ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, 'decision-security', 'effect-security', op or operation())


class ForgedTarget(str):
    def startswith(self, *args):
        raise AssertionError('caller hook must not run')

    def __deepcopy__(self, memo):
        raise AssertionError('caller copy hook must not run')


def test_string_subclass_rejected_before_copy_or_authorization():
    with pytest.raises(ValueError, match='plain builtin'):
        snapshot_envelope(envelope(replace(operation(), target=ForgedTarget('outside'))))


@pytest.mark.parametrize('payload', [{'x': 'a' * 1048577}, list(range(10001))])
def test_input_budgets_before_snapshot(payload):
    with pytest.raises(ValueError, match='budget'):
        snapshot_envelope(envelope(replace(operation(), payload=payload)))


def test_cycle_rejected_without_recursive_copy():
    payload = {}
    payload['cycle'] = payload
    with pytest.raises(ValueError, match='budget'):
        snapshot_envelope(envelope(replace(operation(), payload=payload)))


def test_malformed_admission_is_durable_and_payload_free(tmp_path):
    dest = DurableRefundDestination(tmp_path / 'state')
    with pytest.raises(ValueError):
        snapshot_or_record_rejection(object(), dest)
    with dest._connect() as db:
        rows = db.execute('SELECT * FROM admission_rejections_v1').fetchall()
        assert len(rows) == 1
        assert rows[0]['reason_code'] == 'invalid_execution_envelope'
    assert dest.failure_history() == []


def test_early_policy_denial_is_durable_without_effect(tmp_path):
    op = operation()
    dest = DurableRefundDestination(tmp_path / 'state')
    executor = LocalDestinationExecutor(dest, replace(policy(op), allowed_actions=frozenset()))
    with pytest.raises(PermissionError):
        executor.execute_snapshot(snapshot_envelope(envelope(op)))
    records = dest.failure_history()
    assert len(records) == 1
    assert records[0]['failure_class'] == 'policy_denial'
    with dest._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM effects').fetchone()[0] == 0


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_cli_output_limit_during_read(stream):
    script = f'import sys; sys.{stream}.buffer.write(b"x" * 200000); sys.{stream}.flush()'
    with pytest.raises(ValueError, match='output exceeds'):
        _bounded_cli_output([sys.executable, '-c', script], stdin=None,
                            timeout=3, env={'PATH': os.environ['PATH']}, max_bytes=8192)


def test_cli_deadline_and_small_success():
    assert _bounded_cli_output([sys.executable, '-c', 'print("ok")'], stdin=None,
                              timeout=3, env={}) == 'ok\n'
    with pytest.raises(TimeoutError):
        _bounded_cli_output([sys.executable, '-c', 'import time;time.sleep(2)'],
                            stdin=None, timeout=0.1, env={})
