"""Opt-in destination that dedupes on the stable operation id (operation-identity/0.1).

The legacy ``effect_id`` keeps its meaning. When the control plane runs the opt-in
identity profile it passes the issuer-supplied operation id with the dispatch, and this
destination refuses a second effect for the same operation under a different
``effect_id`` (a renewal, a re-plan or a delayed first attempt), inside the same
``BEGIN IMMEDIATE`` transaction as the effect write. The key is the sidecar
``OperationDiscriminator`` (principal, authority domain, operation id), so an id reused
by another principal or domain is a different operation.

A destination that does not subclass this one refuses an operation id outright
(``operation_identity_capability_unavailable``) before any effect: where the destination cannot store our id,
the refusal is a ``PermissionError`` under rollback, reported as a definitive refusal, rather than silently losing the dedupe.
"""
from __future__ import annotations

import time

from .effective_grant import EffectiveGrantDestination
from .operation_admission import OperationDiscriminator
from .safe_executor import DurableRefundDestination, FrozenEnvelope

CAPABILITY = "operation-identity/0.1"


class OperationIdentityDestination(DurableRefundDestination):
    operation_identity_capability = CAPABILITY

    def __init__(self, root, database_name: str = "refunds.sqlite3"):
        super().__init__(root, database_name)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS operation_effects_v1 ("
                "principal_id TEXT NOT NULL, authority_domain TEXT NOT NULL, operation_id TEXT NOT NULL, "
                "effect_id TEXT NOT NULL, committed_at REAL NOT NULL, "
                "PRIMARY KEY(principal_id, authority_domain, operation_id))")

    @staticmethod
    def _key(snapshot: FrozenEnvelope, operation_id: str) -> tuple[str, str, str]:
        op = snapshot.operation
        return OperationDiscriminator(op.principal, op.authority_domain, operation_id).key

    def _operation_guard(self, conn, snapshot: FrozenEnvelope, operation_id: str) -> None:
        row = conn.execute(
            "SELECT effect_id FROM operation_effects_v1 WHERE principal_id=? AND authority_domain=? AND operation_id=?",
            self._key(snapshot, operation_id)).fetchone()
        if row is not None and row["effect_id"] != snapshot.effect_id:
            conn.execute("ROLLBACK")
            raise PermissionError("operation_already_committed")

    def _operation_record(self, conn, snapshot: FrozenEnvelope, operation_id: str) -> None:
        conn.execute(
            "INSERT INTO operation_effects_v1(principal_id,authority_domain,operation_id,effect_id,committed_at) "
            "VALUES(?,?,?,?,?)", (*self._key(snapshot, operation_id), snapshot.effect_id, time.time()))

    def operation_effect(self, snapshot_or_key) -> str | None:
        """The effect id that committed this operation, or None (audit helper)."""
        key = snapshot_or_key
        with self._connect() as conn:
            row = conn.execute(
                "SELECT effect_id FROM operation_effects_v1 WHERE principal_id=? AND authority_domain=? AND operation_id=?",
                key).fetchone()
        return row["effect_id"] if row else None


class OperationIdentityEffectiveGrantDestination(OperationIdentityDestination, EffectiveGrantDestination):
    """Both opt-in profiles on one database (operation dedupe plus effective-grant precondition)."""
