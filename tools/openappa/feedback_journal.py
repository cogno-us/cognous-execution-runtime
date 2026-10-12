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
def _database(path):
    # Same trusted-directory assumption as local resume; no hostile-owner race claim.
    path = Path(path).absolute()
    try:
        fd, _ = _open_owned(path)
    except ResumeRefused as error:
        raise ValueError('feedback journal unavailable') from error
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True)) as db:
            db.execute('PRAGMA synchronous=FULL')
            yield db
    except sqlite3.Error as error:
        raise ValueError('feedback journal unavailable') from error
    finally:
        os.close(fd)


class JournaledReporter:
    """A recorded claim prevents a second delivery after restart or receipt loss.

    The callback remains trusted. Acknowledged means its exact correlated Ack
    was observed and recorded, not independently verified remote settlement.
    Unconfirmed never becomes acknowledged through duplicate refusal.
    """
    def __init__(self, path, report_success):
        self.path = path
        self.report_success = report_success

    def __call__(self, binding, raw):
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
        with _database(self.path) as db, db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT request_sha256,state FROM receipt WHERE call_key=?', (key,)).fetchone()
            if prior is not None:
                if prior[0] != digest:
                    raise ValueError('feedback call identity was reused')
                return {'request_sha256': digest,
                        'outcome': 'ack' if prior[1] == 'acknowledged' else 'unconfirmed'}
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
