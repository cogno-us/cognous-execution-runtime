"""Optional same-database effective grant precondition; no remote atomicity."""
from __future__ import annotations
import json
import re
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
            conn.execute('CREATE TABLE IF NOT EXISTS effective_grants_v1 ('
                         'grant_id TEXT PRIMARY KEY,principal TEXT NOT NULL,digest TEXT NOT NULL,'
                         'derivation TEXT NOT NULL,status TEXT NOT NULL)')

    def set_effective_grant(self, *, principal: str, grant_id: str, revision: str,
                            permissions: list[str], status: str = 'active') -> str:
        """Trusted authority writer; serializes with the effect transaction."""
        if status not in {'active','revoked','suspended'}:
            raise ValueError('invalid effective grant status')
        digest=effective_grant_digest(principal=principal,grant_id=grant_id,revision=revision,
                                      permissions=permissions,status=status)
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('INSERT INTO effective_grants_v1 VALUES(?,?,?,?,?) '
                         'ON CONFLICT(grant_id) DO UPDATE SET principal=excluded.principal,'
                         'digest=excluded.digest,derivation=excluded.derivation,status=excluded.status',
                         (grant_id,principal,digest,DERIVATION,status))
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
