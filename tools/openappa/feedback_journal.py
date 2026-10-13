"""Opt-in trusted-host feedback receipts; no effect or delivery retry API."""
from contextlib import closing, contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from tools.openappa.local_resume import ResumeRefused, _open_owned, _private_directory, _text


def initialize(path):
    """Explicit operator creation only; never overwrite or migrate a journal."""
    path = Path(path).absolute()
    _private_directory(path.parent)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE receipt (call_key TEXT PRIMARY KEY, request_sha256 TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('unconfirmed','acknowledged')))")


@contextmanager
def _database(path, *, readonly=False):
    # Same trusted-directory assumption as local resume; no hostile-owner race claim.
    path = Path(path).absolute()
    try:
        fd, _ = _open_owned(path)
    except ResumeRefused as error:
        raise ValueError('feedback journal unavailable') from error
    try:
        with closing(sqlite3.connect(path.as_uri() + ('?mode=ro' if readonly else '?mode=rw'), uri=True)) as db:
            db.execute('PRAGMA synchronous=FULL')
            yield db
    except sqlite3.Error as error:
        raise ValueError('feedback journal unavailable') from error
    finally:
        os.close(fd)


def export_journal_snapshot(source, destination):
    """Explicit private snapshot export; never overwrite, restore or prune state.

    Uses SQLite's backup API, not a live database file copy. The directory owner
    is trusted. This is not rollback protection, a restore procedure, or proof
    that the snapshot includes claims committed after its snapshot boundary.
    Consume the destination only after successful return. Process interruption
    may leave an incomplete destination; no power-loss guarantee is made.
    """
    destination = Path(destination).absolute()
    _private_directory(destination.parent)
    with _database(source, readonly=True) as db:
        # Reject an unavailable journal before creating any destination file.
        db.execute('SELECT call_key,request_sha256,state FROM receipt LIMIT 0')
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_CLOEXEC, 0o600)
        identity = os.fstat(fd)
        complete = False
        try:
            def progress(status, remaining, total):
                if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise ValueError('feedback snapshot unavailable')
            with closing(sqlite3.connect(destination)) as target:
                db.backup(target, pages=256, progress=progress)
            os.fsync(fd)
            complete = True
        finally:
            os.close(fd)
            if not complete:
                # Remove only the incomplete file created by this invocation.
                try:
                    observed = destination.stat(follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if (observed.st_dev, observed.st_ino) == (identity.st_dev, identity.st_ino):
                        destination.unlink()


def _identity(binding, raw):
    context = json.loads(raw)['context']
    if (context['tenant_id'], context['principal_id'], context['root_id']) != (
        binding.tenant_id, binding.principal_id, binding.root_id
    ):
        raise ValueError('feedback binding mismatch')
    # Store only digests and state, never request payloads or result bodies.
    identity = json.dumps([asdict(binding), _text(context['call_id'])],
                          sort_keys=True, separators=(',', ':')).encode()
    key = hashlib.sha256(identity).hexdigest()
    digest = hashlib.sha256(raw).hexdigest()
    return key, digest


def read_receipt(path, binding, raw):
    """Inspect existing host bookkeeping without claiming or delivering feedback.

    Absent is not permission to retry execution or feedback. Acknowledged is a
    recorded host observation, not an independently verified remote receipt.
    """
    key, digest = _identity(binding, raw)
    with _database(path, readonly=True) as db:
        prior = db.execute('SELECT request_sha256,state FROM receipt WHERE call_key=?', (key,)).fetchone()
    if prior is None:
        outcome = 'absent'
    else:
        if prior[0] != digest:
            raise ValueError('feedback call identity was reused')
        if prior[1] not in ('acknowledged', 'unconfirmed'):
            raise ValueError('invalid feedback journal state')
        outcome = 'ack' if prior[1] == 'acknowledged' else 'unconfirmed'
    return {'request_sha256': digest, 'outcome': outcome}


def read_journal_status(path):
    """Return a read-only host-wide count snapshot, without receipt identifiers.

    Counts are bookkeeping observations, not remote delivery proof, a capacity
    reservation, or retry permission. This is not a tenant-facing endpoint.
    Unavailable state raises instead of being represented as an empty journal.
    """
    with _database(path, readonly=True) as db:
        # One statement gives a consistent snapshot across both state counts.
        rows = db.execute('SELECT state,count(*) FROM receipt GROUP BY state').fetchall()
    counts = {'acknowledged': 0, 'unconfirmed': 0}
    for state, count in rows:
        if state not in counts:
            raise ValueError('invalid feedback journal state')
        counts[state] = count
    return {'total': sum(counts.values()), **counts}


class JournalCapacityExceeded(ValueError):
    """No new feedback claim was admitted; existing evidence remains intact."""


class JournaledReporter:
    """A recorded claim prevents a second delivery after restart or receipt loss.

    The callback remains trusted. Acknowledged means its exact correlated Ack
    was observed and recorded, not independently verified remote settlement.
    Unconfirmed never becomes acknowledged through duplicate refusal.

    max_receipts optionally caps total journal rows, including acknowledged
    claims. It is trusted host configuration, not persisted policy or a byte
    quota. Capacity refusal never undoes an effect that already completed.
    """
    def __init__(self, path, report_success, *, max_receipts=None):
        if max_receipts is not None and (type(max_receipts) is not int or max_receipts < 1):
            raise ValueError('max_receipts must be a positive integer or None')
        self.path = path
        self.report_success = report_success
        # Trusted host configuration; all writers must use the same limit.
        self.max_receipts = max_receipts

    def __call__(self, binding, raw):
        key, digest = _identity(binding, raw)
        with _database(self.path) as db, db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT request_sha256,state FROM receipt WHERE call_key=?', (key,)).fetchone()
            if prior is not None:
                if prior[0] != digest:
                    raise ValueError('feedback call identity was reused')
                return {'request_sha256': digest,
                        'outcome': 'ack' if prior[1] == 'acknowledged' else 'unconfirmed'}
            # Count and claim share the writer reservation. Never evict receipts
            # to make room, including acknowledged rows used for deduplication.
            if self.max_receipts is not None:
                count = db.execute('SELECT count(*) FROM receipt').fetchone()[0]
                if count >= self.max_receipts:
                    raise JournalCapacityExceeded('feedback journal capacity reached')
            db.execute('INSERT INTO receipt VALUES (?,?,?)', (key, digest, 'unconfirmed'))
        # Commit the claim before delivery; a crash leaves it unconfirmed.
        expected = {'request_sha256': digest, 'outcome': 'ack'}
        receipt = self.report_success(binding, raw)
        if receipt != expected:
            return {'request_sha256': digest, 'outcome': 'unconfirmed'}
        with _database(self.path) as db, db:
            changed = db.execute("UPDATE receipt SET state='acknowledged' WHERE call_key=? AND request_sha256=? AND state='unconfirmed'", (key, digest)).rowcount
            if changed != 1:
                raise ValueError('feedback claim changed before acknowledgment')
        return expected
