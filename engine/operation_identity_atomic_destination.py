"""Opt-in atomic-route destination that enforces one commit per operation across renewals (operation-identity/0.1).

``AtomicAuthorityEffectDestination`` keeps its meaning and its bytes. This subclass adds the same operation guard the
adapter route gets from ``OperationIdentityDestination``: inside the ``BEGIN IMMEDIATE`` transaction that consumes the
claim and writes the effect, a second effect for an already committed operation under a different ``effect_id`` (a renewal
with a new approval, for example) is refused with ``operation_already_committed`` and the transaction is rolled back.

The key is the sidecar ``OperationDiscriminator`` (principal, authority domain, operation id). The operation id is never
caller free text: ``AtomicLocalControlPlaneExecutor(operation_identity=True)`` derives it from the persisted, issuer-approved
Control Plane decision and validates it against the Control Plane operation ledger before it reaches this destination.
Without a key the destination behaves exactly as the base class.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable
from datetime import datetime

from .local_authority_effect import AtomicAuthorityEffectDestination
from .operation_identity_destination import CAPABILITY, OperationIdentityDestination


class OperationIdentityAtomicAuthorityEffectDestination(AtomicAuthorityEffectDestination):
    operation_identity_capability = CAPABILITY

    def __init__(self, root: str | Path, *, clock: Callable[[], datetime], database_name: str = "refunds.sqlite3"):
        super().__init__(root, clock=clock, database_name=database_name)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS operation_effects_v1 ("
                "principal_id TEXT NOT NULL, authority_domain TEXT NOT NULL, operation_id TEXT NOT NULL, "
                "effect_id TEXT NOT NULL, committed_at REAL NOT NULL, "
                "PRIMARY KEY(principal_id, authority_domain, operation_id))")

    # One implementation of the key, the guard and the record: the adapter-route destination's.
    _key = staticmethod(OperationIdentityDestination._key)
    _operation_guard = OperationIdentityDestination._operation_guard
    _operation_record = OperationIdentityDestination._operation_record
    operation_effect = OperationIdentityDestination.operation_effect
