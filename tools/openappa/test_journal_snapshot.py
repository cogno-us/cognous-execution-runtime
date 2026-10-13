"""Snapshot export preserves evidence; no restore or retry is performed."""
import json
import os
import sqlite3
import stat

import pytest

from tools.openappa.feedback_journal import (
    JournaledReporter, export_journal_snapshot, read_journal_status, read_receipt,
)
from tools.openappa.test_feedback_journal import ack, fixture


@pytest.mark.parametrize('populated', [False, True])
def test_snapshot_preserves_receipts_and_private_mode(tmp_path, populated):
    source, binding, raw = fixture(tmp_path)
    other = json.loads(raw)
    other['context']['call_id'] = 'synthetic-unconfirmed'
    other = json.dumps(other).encode()
    if populated:
        JournaledReporter(source, lambda _, data: ack(data))(binding, raw)
        JournaledReporter(source, lambda *_: None)(binding, other)
    before = source.read_bytes()
    target = tmp_path / 'snapshot.sqlite'
    export_journal_snapshot(source, target)
    assert source.read_bytes() == before
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert read_journal_status(target) == read_journal_status(source)
    for request in (raw, other):
        assert read_receipt(target, binding, request) == read_receipt(source, binding, request)
    # Later claims are not retroactively included in the exported snapshot.
    request = json.loads(raw)
    request['context']['call_id'] = 'synthetic-after-snapshot'
    request = json.dumps(request).encode()
    JournaledReporter(source, lambda _, data: ack(data))(binding, request)
    assert read_receipt(target, binding, request)['outcome'] == 'absent'


@pytest.mark.parametrize('same_path', [False, True])
def test_export_never_overwrites_existing_destination(tmp_path, same_path):
    source, _, _ = fixture(tmp_path)
    target = source if same_path else tmp_path / 'existing.sqlite'
    if not same_path:
        target.write_bytes(b'synthetic-existing-evidence')
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        export_journal_snapshot(source, target)
    assert target.read_bytes() == before


@pytest.mark.parametrize('mutation', ['missing', 'public', 'corrupt'])
def test_unavailable_source_creates_no_snapshot(tmp_path, mutation):
    source, _, _ = fixture(tmp_path)
    if mutation == 'missing':
        source.unlink()
    elif mutation == 'public':
        source.chmod(0o644)
    else:
        source.write_bytes(b'synthetic-invalid-database')
    before = source.read_bytes() if source.exists() else None
    target = tmp_path / 'snapshot.sqlite'
    with pytest.raises((OSError, ValueError)):
        export_journal_snapshot(source, target)
    assert not target.exists()
    assert (source.read_bytes() if source.exists() else None) == before


def test_snapshot_reads_committed_state_with_active_writer(tmp_path):
    source, binding, raw = fixture(tmp_path)
    JournaledReporter(source, lambda *_: None)(binding, raw)
    target = tmp_path / 'snapshot.sqlite'
    with sqlite3.connect(source) as writer:
        writer.execute('BEGIN IMMEDIATE')
        writer.execute("UPDATE receipt SET state='acknowledged'")
        export_journal_snapshot(source, target)
    assert read_receipt(source, binding, raw)['outcome'] == 'ack'
    assert read_receipt(target, binding, raw)['outcome'] == 'unconfirmed'


def test_failed_flush_removes_only_new_snapshot(tmp_path, monkeypatch):
    source, _, _ = fixture(tmp_path)
    before = source.read_bytes()
    target = tmp_path / 'snapshot.sqlite'
    def failed_flush(*args):
        raise OSError('synthetic flush failure')
    monkeypatch.setattr(os, 'fsync', failed_flush)
    with pytest.raises(OSError, match='synthetic flush failure'):
        export_journal_snapshot(source, target)
    assert source.read_bytes() == before
    assert not target.exists()


def test_export_requires_private_destination_directory(tmp_path):
    source, _, _ = fixture(tmp_path)
    directory = tmp_path / 'public'
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    from tools.openappa.local_resume import ResumeRefused
    with pytest.raises(ResumeRefused):
        export_journal_snapshot(source, directory / 'snapshot.sqlite')
    assert list(directory.iterdir()) == []
