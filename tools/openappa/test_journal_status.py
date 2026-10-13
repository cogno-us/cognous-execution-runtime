"""Host-only aggregate status must not alter claims or imply delivery permission."""
import json
import sqlite3

import pytest

from tools.openappa.feedback_journal import JournaledReporter, read_journal_status
from tools.openappa.test_feedback_journal import ack, fixture


@pytest.mark.parametrize('populated', [False, True])
def test_status_counts_without_identifiers_or_writes(tmp_path, populated):
    path, binding, raw = fixture(tmp_path)
    if populated:
        JournaledReporter(path, lambda _, data: ack(data))(binding, raw)
        for i in range(2):
            request = json.loads(raw)
            request['context']['call_id'] = f'synthetic-unconfirmed-{i}'
            JournaledReporter(path, lambda *_: None)(binding, json.dumps(request).encode())
    before = path.read_bytes()
    files = set(tmp_path.iterdir())
    expected = {'total': 3, 'acknowledged': 1, 'unconfirmed': 2} if populated else {
        'total': 0, 'acknowledged': 0, 'unconfirmed': 0,
    }
    assert read_journal_status(path) == expected
    assert read_journal_status(path) == expected
    assert path.read_bytes() == before
    assert set(tmp_path.iterdir()) == files


@pytest.mark.parametrize('mutation', ['missing', 'public', 'corrupt'])
def test_unavailable_status_is_not_empty(tmp_path, mutation):
    path, _, _ = fixture(tmp_path)
    if mutation == 'missing':
        path.unlink()
    elif mutation == 'public':
        path.chmod(0o644)
    else:
        path.write_bytes(b'synthetic-invalid-journal')
    before = path.read_bytes() if path.exists() else None
    with pytest.raises((ValueError, OSError)):
        read_journal_status(path)
    assert (path.read_bytes() if path.exists() else None) == before


def test_status_rejects_unknown_persisted_state(tmp_path):
    path, binding, raw = fixture(tmp_path)
    JournaledReporter(path, lambda *_: None)(binding, raw)
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA ignore_check_constraints=ON')
        db.execute("UPDATE receipt SET state='synthetic-invalid'")
    before = path.read_bytes()
    with pytest.raises(ValueError, match='invalid feedback journal state'):
        read_journal_status(path)
    assert path.read_bytes() == before


def test_status_observes_committed_snapshot_while_writer_is_active(tmp_path):
    path, binding, raw = fixture(tmp_path)
    JournaledReporter(path, lambda *_: None)(binding, raw)
    with sqlite3.connect(path) as writer:
        writer.execute('BEGIN IMMEDIATE')
        writer.execute("UPDATE receipt SET state='acknowledged'")
        assert read_journal_status(path) == {
            'total': 1, 'acknowledged': 0, 'unconfirmed': 1,
        }
    assert read_journal_status(path) == {
        'total': 1, 'acknowledged': 1, 'unconfirmed': 0,
    }
