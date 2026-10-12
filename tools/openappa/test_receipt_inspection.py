"""Receipt inspection must never create claims, deliver, or authorize retries."""
from dataclasses import replace
import json
import sqlite3

import pytest

from tools.openappa.feedback_journal import JournaledReporter, read_receipt
from tools.openappa.test_feedback_journal import ack, fixture


@pytest.mark.parametrize('state', ['absent', 'unconfirmed', 'acknowledged'])
def test_inspection_preserves_journal_and_status(tmp_path, state):
    path, binding, raw = fixture(tmp_path)
    if state != 'absent':
        JournaledReporter(path, lambda *_: ack(raw) if state == 'acknowledged' else None)(binding, raw)
    before = path.read_bytes()
    files = set(tmp_path.iterdir())
    expected = {**ack(raw), 'outcome': 'ack' if state == 'acknowledged' else state}
    assert read_receipt(path, binding, raw) == expected
    assert read_receipt(path, binding, raw) == expected
    assert path.read_bytes() == before
    assert set(tmp_path.iterdir()) == files
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM receipt').fetchone()[0] == int(state != 'absent')


@pytest.mark.parametrize('mutation', ['missing', 'public', 'corrupt'])
def test_unavailable_inspection_is_not_absent(tmp_path, mutation):
    path, binding, raw = fixture(tmp_path)
    if mutation == 'missing':
        path.unlink()
    elif mutation == 'public':
        path.chmod(0o644)
    else:
        path.write_bytes(b'synthetic-invalid-journal')
    before = path.read_bytes() if path.exists() else None
    with pytest.raises((ValueError, OSError)):
        read_receipt(path, binding, raw)
    assert (path.read_bytes() if path.exists() else None) == before


def test_inspection_refuses_changed_request_for_existing_call(tmp_path):
    path, binding, raw = fixture(tmp_path)
    JournaledReporter(path, lambda *_: ack(raw))(binding, raw)
    changed = json.loads(raw)
    changed['arguments'] = {'marker': 'synthetic-other'}
    with pytest.raises(ValueError, match='reused'):
        read_receipt(path, binding, json.dumps(changed).encode())
    assert read_receipt(path, binding, raw) == ack(raw)


def test_inspection_refuses_mismatched_binding(tmp_path):
    path, binding, raw = fixture(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='binding mismatch'):
        read_receipt(path, replace(binding, tenant_id='synthetic-other'), raw)
    assert path.read_bytes() == before


def test_inspection_does_not_acquire_writer_reservation(tmp_path):
    path, binding, raw = fixture(tmp_path)
    JournaledReporter(path, lambda *_: ack(raw))(binding, raw)
    with sqlite3.connect(path) as writer:
        writer.execute('BEGIN IMMEDIATE')
        assert read_receipt(path, binding, raw) == ack(raw)
