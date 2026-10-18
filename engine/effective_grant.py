"""Optional same-database effective grant precondition; no remote atomicity."""
from __future__ import annotations
import json
import re
import sqlite3
from .safe_executor import DurableRefundDestination, FrozenEnvelope

CAPABILITY = "sqlite-effective-grant/0.1"
DERIVATION = "effective-grant/0.1"


def effective_grant_digest(*, principal: str, grant_id: str, revision: str,
                           permissions: list[str], status: str) -> str:
    """Commit semantic permission state with a fixed versioned derivation."""
    import hashlib
    value={"version":DERIVATION,"principal":principal,"grant_id":grant_id,
           "revision":revision,"permissions":sorted(set(permissions)),"status":status}
    return 'sha256:'+hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


class EffectiveGrantDestination(DurableRefundDestination):
    """Activate a local authority table; every effect writer must honor it.

    Authority writers require trusted host credentials/access. SQLite does not
    separate a caller holding the database file from that authority writer.
    """
    runtime_authority_capability = CAPABILITY

    def __init__(self, root, database_name='refunds.sqlite3'):
        super().__init__(root,database_name)
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='authority_effect_profile_v1'").fetchone():
                # The atomic authority/effect route does not evaluate the effective-grant precondition, so the
                # two profiles are never combined on one database (the atomic profile refuses the reverse order).
                conn.execute('ROLLBACK')
                raise PermissionError('effective-grant profile cannot share an authority-effect database')
            conn.execute('COMMIT')
        with self._connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS effective_grants_v1 ('
                         'grant_id TEXT PRIMARY KEY,principal TEXT NOT NULL,digest TEXT NOT NULL,'
                         'derivation TEXT NOT NULL,status TEXT NOT NULL,permissions_json TEXT)')
            # Databases created before the permission set was retained: add the column, leave rows NULL.
            # A NULL row cannot be checked against a request and therefore fails closed at commit.
            if 'permissions_json' not in {r[1] for r in conn.execute('PRAGMA table_info(effective_grants_v1)')}:
                try:
                    conn.execute('ALTER TABLE effective_grants_v1 ADD COLUMN permissions_json TEXT')
                except sqlite3.OperationalError:
                    if 'permissions_json' not in {r[1] for r in conn.execute('PRAGMA table_info(effective_grants_v1)')}:
                        raise

    def set_effective_grant(self, *, principal: str, grant_id: str, revision: str,
                            permissions: list[str], status: str = 'active') -> str:
        """Trusted authority writer; serializes with the effect transaction."""
        if status not in {'active','revoked','suspended'}:
            raise ValueError('invalid effective grant status')
        digest=effective_grant_digest(principal=principal,grant_id=grant_id,revision=revision,
                                      permissions=permissions,status=status)
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing=conn.execute('SELECT principal FROM effective_grants_v1 WHERE grant_id=?',(grant_id,)).fetchone()
            if existing is not None and existing['principal']!=principal:
                # The row is keyed by grant id alone, so a grant id is bound to one principal for the life of the
                # database; reusing it for another principal would silently reassign the authority row.
                conn.execute('ROLLBACK')
                raise PermissionError('effective grant id is already bound to another principal')
            conn.execute('INSERT INTO effective_grants_v1(grant_id,principal,digest,derivation,status,permissions_json) '
                         'VALUES(?,?,?,?,?,?) '
                         'ON CONFLICT(grant_id) DO UPDATE SET principal=excluded.principal,'
                         'digest=excluded.digest,derivation=excluded.derivation,status=excluded.status,'
                         'permissions_json=excluded.permissions_json',
                         (grant_id,principal,digest,DERIVATION,status,
                          json.dumps(sorted(set(permissions)),separators=(',',':'),ensure_ascii=False)))
            conn.execute('COMMIT')
        return digest


def check_effective_grant(conn, snapshot: FrozenEnvelope, expected: str | None,
                          derivation: str | None) -> None:
    """Called under the actual BEGIN IMMEDIATE effect transaction."""
    active=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='effective_grants_v1'").fetchone()
    if not active:
        if expected is not None:raise PermissionError('atomic_authority_capability_unavailable')
        return
    if not isinstance(expected,str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',expected) or derivation!=DERIVATION:
        raise PermissionError('effective_grant_precondition_required')
    row=conn.execute('SELECT principal,digest,derivation,status FROM effective_grants_v1 WHERE grant_id=?',
                     (snapshot.operation.grant_id,)).fetchone()
    if (row is None or row['principal']!=snapshot.operation.principal or row['digest']!=expected
            or row['derivation']!=derivation or row['status']!='active'):
        raise PermissionError('effective_grant_changed_at_commit')
    # The digest only proves the caller read this row. The row's own permission set must also cover
    # the request, so a caller holding the stored digest cannot commit beyond a narrowed grant.
    try:  # same effect transaction; a legacy table without the column fails closed
        stored=conn.execute('SELECT permissions_json FROM effective_grants_v1 WHERE grant_id=?',
                            (snapshot.operation.grant_id,)).fetchone()[0]
        permissions=json.loads(stored) if stored is not None else None
    except (sqlite3.OperationalError,ValueError):
        permissions=None
    if not isinstance(permissions,list) or any(type(x) is not str for x in permissions):
        raise PermissionError('effective_grant_permissions_unavailable')
    if not set(snapshot.operation.requested_permissions)<=set(permissions):
        raise PermissionError('effective_grant_permissions_insufficient')
