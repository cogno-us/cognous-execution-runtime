"""Opt-in dedupe-ttl/0.1 destination capability for the CI/local synthetic SQLite destination.

A destination that stores our id and dedupes on it inside a TTL, and answers ``in_progress`` (not an acknowledgement, not a failure)
for a key it is still processing. It is the runtime half of the control plane's ``dedupe-ttl/0.1`` profile
(``cogno-us/cognous-control-plane``, ``agent_control_plane/dedupe.py``); the control plane reads the two declared attributes below and
nothing else, and never infers the capability.

* ``dedupe_capability = "dedupe-ttl/0.1"`` and ``dedupe_ttl_seconds``: the destination remembers the operation key for that long. After
  it the key is forgotten: a second commit under another effect id for the same operation is accepted as new. That is the duplicate the
  control plane's escalation deadline (inside the key TTL) exists to beat; it is modelled here so the suites can show it.
* ``in_progress``: after ``defer_next_commit()`` (delayed-commit/0.1) the key is held durably as in progress. A commit for the same
  operation key, under the same or another effect id, raises ``InProgress`` instead of creating a second effect. ``land()`` completes the
  deferred commit and clears the hold. A hold older than the TTL is ignored. A landing that is refused (for example by the cumulative
  cap) leaves the hold in place until its TTL: later requests for the key are held, never duplicated.

This is a CI/local aid with no authority of its own and no model of any real provider: nothing here touches an external service. The
clock is injectable (``clock() -> float`` seconds) so suites move time without sleeping. Legacy destinations are untouched.
"""
from __future__ import annotations

import time
from typing import Callable

from .delayed_commit_destination import DelayedCommitMixin
from .operation_identity_destination import OperationIdentityDestination

CAPABILITY = "dedupe-ttl/0.1"
IN_PROGRESS = "destination_in_progress"


class InProgress(TimeoutError):
    """The destination holds this operation key and is still processing it. A TimeoutError, so the executor reports ``unknown``."""

    def __init__(self):
        super().__init__(IN_PROGRESS)


class DedupeTtlMixin:
    """Mix in ahead of ``DelayedCommitMixin`` and ``OperationIdentityDestination`` (see the concrete class below)."""

    dedupe_capability = CAPABILITY

    def _dedupe_init(self, ttl_seconds: int, clock: Callable[[], float]) -> None:
        if type(ttl_seconds) is not int or ttl_seconds <= 0:
            raise ValueError("dedupe_ttl_seconds must be a positive integer")
        self.dedupe_ttl_seconds = ttl_seconds
        self._clock = clock
        self._landing = False
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS dedupe_in_progress_v1 ("
                "principal_id TEXT NOT NULL, authority_domain TEXT NOT NULL, operation_id TEXT NOT NULL, "
                "effect_id TEXT NOT NULL, started_at REAL NOT NULL, "
                "PRIMARY KEY(principal_id, authority_domain, operation_id))")

    # -- the key and its hold -------------------------------------------------------------------------------
    def _mark_in_progress(self, snapshot, operation_id: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO dedupe_in_progress_v1 VALUES(?,?,?,?,?)",
                         (*self._key(snapshot, operation_id), snapshot.effect_id, self._clock()))

    def commit(self, snapshot, **kwargs):
        operation_id = kwargs.get("operation_id")
        if operation_id is not None and getattr(self, "_defer_next", False):
            self._mark_in_progress(snapshot, operation_id)  # the request is accepted and still being processed
        return super().commit(snapshot, **kwargs)

    def land(self):
        self._landing = True  # the deferred request is the one that holds the key: it may complete it
        try:
            return super().land()
        finally:
            self._landing = False

    # -- dedupe inside the TTL (called inside the effect transaction, which it rolls back before refusing) ------
    def _operation_guard(self, conn, snapshot, operation_id: str) -> None:
        now, ttl, key = self._clock(), self.dedupe_ttl_seconds, self._key(snapshot, operation_id)
        row = conn.execute(
            "SELECT effect_id, committed_at FROM operation_effects_v1 WHERE principal_id=? AND authority_domain=? AND operation_id=?",
            key).fetchone()
        if row is not None:
            if now - row["committed_at"] < ttl:
                if row["effect_id"] != snapshot.effect_id:
                    conn.execute("ROLLBACK")
                    raise PermissionError("operation_already_committed")
            else:  # the key is forgotten after its TTL: this is what the control plane's escalation deadline must beat
                conn.execute("DELETE FROM operation_effects_v1 WHERE principal_id=? AND authority_domain=? AND operation_id=?", key)
        held = conn.execute(
            "SELECT effect_id, started_at FROM dedupe_in_progress_v1 WHERE principal_id=? AND authority_domain=? AND operation_id=?",
            key).fetchone()
        if held is not None and now - held["started_at"] < ttl and not (self._landing and held["effect_id"] == snapshot.effect_id):
            conn.execute("ROLLBACK")
            raise InProgress()

    def _operation_record(self, conn, snapshot, operation_id: str) -> None:
        key = self._key(snapshot, operation_id)
        conn.execute("DELETE FROM dedupe_in_progress_v1 WHERE principal_id=? AND authority_domain=? AND operation_id=?", key)
        conn.execute("INSERT OR REPLACE INTO operation_effects_v1(principal_id,authority_domain,operation_id,effect_id,committed_at) "
                     "VALUES(?,?,?,?,?)", (*key, snapshot.effect_id, self._clock()))


class DedupeTtlOperationDestination(DedupeTtlMixin, DelayedCommitMixin, OperationIdentityDestination):
    """Operation-identity destination with a key TTL, an in-progress answer and the delayed-commit controls."""

    def __init__(self, root, database_name: str = "refunds.sqlite3", *, dedupe_ttl_seconds: int = 600,
                 clock: Callable[[], float] = time.time):
        OperationIdentityDestination.__init__(self, root, database_name)
        self._delayed_init()
        self._dedupe_init(dedupe_ttl_seconds, clock)
