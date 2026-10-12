"""Bounded durable revocation disposition and original-attempt closeout profile.

This opt-in sidecar records runtime evidence only. It does not authorize execution,
change executor semantics, issue grants, infer retry permission, or claim destination
commit atomicity.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

PROFILE_ID = "cognous-revocation-closeout/1"
ItemState = Literal["queued", "executing", "completed"]
DispositionStatus = Literal[
    "refused_before_execution", "completed_before_revocation", "in_doubt"
]
ObservationState = Literal["applied", "partial", "absent", "unknown"]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class OrderingEvidence:
    order: int
    source_ref: str

    def __post_init__(self) -> None:
        if type(self.order) is not int or self.order < 0:
            raise ValueError("order must be a non-negative integer")
        if not self.source_ref.strip():
            raise ValueError("source_ref must be non-empty")


@dataclass(frozen=True)
class RevocationDisposition:
    effect_id: str
    attempt_id: str | None
    grant_id: str
    grant_revision: str
    status: DispositionStatus
    item_order: int
    revocation_order: int
    ordering_ref: str
    owner: str | None
    deadline: str | None
    reconciliation_ref: str | None


@dataclass(frozen=True)
class CloseoutResult:
    effect_id: str
    attempt_id: str
    observation_state: ObservationState
    observation_ref: str
    closed: bool
    retry_eligible: bool
    reason: str


class RevocationCloseoutStore:
    """Durable, opt-in evidence ledger for revocation disposition and closeout."""

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
        database_name: str = "revocation-closeout.sqlite3",
        disposition_writer_enabled: bool = True,
        closeout_writer_enabled: bool = True,
    ):
        self.root = Path(root).expanduser().absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / database_name
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.disposition_writer_enabled = disposition_writer_enabled
        self.closeout_writer_enabled = closeout_writer_enabled
        self._init_db()

    def _now(self) -> datetime:
        return _utc(self.clock())

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runtime_items_v1 (
                    effect_id TEXT PRIMARY KEY,
                    attempt_id TEXT,
                    decision_id TEXT NOT NULL,
                    grant_id TEXT NOT NULL,
                    grant_revision TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('queued','executing','completed')),
                    state_order INTEGER NOT NULL,
                    ordering_ref TEXT NOT NULL,
                    completed_order INTEGER,
                    completion_ref TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS revocation_dispositions_v1 (
                    effect_id TEXT PRIMARY KEY,
                    attempt_id TEXT,
                    grant_id TEXT NOT NULL,
                    grant_revision TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'refused_before_execution','completed_before_revocation','in_doubt'
                    )),
                    item_order INTEGER NOT NULL,
                    revocation_order INTEGER NOT NULL,
                    ordering_ref TEXT NOT NULL,
                    owner TEXT,
                    deadline TEXT,
                    reconciliation_ref TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS original_attempt_closeouts_v1 (
                    attempt_id TEXT PRIMARY KEY,
                    effect_id TEXT NOT NULL,
                    observation_state TEXT NOT NULL CHECK(observation_state IN (
                        'applied','partial','absent','unknown'
                    )),
                    observation_order INTEGER NOT NULL,
                    observation_ref TEXT NOT NULL,
                    closed INTEGER NOT NULL,
                    retry_eligible INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def record_item(
        self,
        *,
        effect_id: str,
        decision_id: str,
        grant_id: str,
        grant_revision: str,
        state: ItemState,
        ordering: OrderingEvidence,
        attempt_id: str | None = None,
        completion: OrderingEvidence | None = None,
    ) -> None:
        required = {
            "effect_id": effect_id,
            "decision_id": decision_id,
            "grant_id": grant_id,
            "grant_revision": grant_revision,
        }
        if any(not value.strip() for value in required.values()):
            raise ValueError("item identifiers must be non-empty")
        if state == "completed" and completion is None:
            raise ValueError("completed item requires authoritative completion ordering")
        if state != "completed" and completion is not None:
            raise ValueError("completion ordering is only valid for completed items")
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO runtime_items_v1(
                    effect_id,attempt_id,decision_id,grant_id,grant_revision,state,
                    state_order,ordering_ref,completed_order,completion_ref,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    effect_id,
                    attempt_id,
                    decision_id,
                    grant_id,
                    grant_revision,
                    state,
                    ordering.order,
                    ordering.source_ref,
                    completion.order if completion else None,
                    completion.source_ref if completion else None,
                    self._now().isoformat(),
                ),
            )

    def _disposition_for(
        self,
        row: sqlite3.Row,
        *,
        revocation: OrderingEvidence,
        owner: str | None,
        deadline: datetime | None,
        reconciliation_ref: str | None,
    ) -> RevocationDisposition:
        state = row["state"]
        status: DispositionStatus
        if state == "queued" and row["state_order"] <= revocation.order:
            status = "refused_before_execution"
        elif (
            state == "completed"
            and row["completed_order"] is not None
            and int(row["completed_order"]) < revocation.order
        ):
            status = "completed_before_revocation"
        else:
            status = "in_doubt"

        deadline_text = _utc(deadline).isoformat() if deadline else None
        if status == "in_doubt":
            if not owner or not owner.strip():
                raise ValueError("in_doubt disposition requires a named owner")
            if deadline_text is None:
                raise ValueError("in_doubt disposition requires a deadline")
            if not reconciliation_ref or not reconciliation_ref.strip():
                raise ValueError("in_doubt disposition requires a reconciliation reference")
        return RevocationDisposition(
            effect_id=row["effect_id"],
            attempt_id=row["attempt_id"],
            grant_id=row["grant_id"],
            grant_revision=row["grant_revision"],
            status=status,
            item_order=int(row["state_order"]),
            revocation_order=revocation.order,
            ordering_ref=revocation.source_ref,
            owner=owner if status == "in_doubt" else None,
            deadline=deadline_text if status == "in_doubt" else None,
            reconciliation_ref=reconciliation_ref if status == "in_doubt" else None,
        )

    def record_revocation(
        self,
        *,
        grant_id: str,
        grant_revision: str,
        revocation: OrderingEvidence,
        owner: str | None = None,
        deadline: datetime | None = None,
        reconciliation_ref: str | None = None,
    ) -> list[RevocationDisposition]:
        if not self.disposition_writer_enabled:
            raise RuntimeError("revocation disposition writer disabled")
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM runtime_items_v1
                WHERE grant_id=? AND grant_revision=?
                ORDER BY effect_id""",
                (grant_id, grant_revision),
            ).fetchall()
            dispositions = [
                self._disposition_for(
                    row,
                    revocation=revocation,
                    owner=owner,
                    deadline=deadline,
                    reconciliation_ref=reconciliation_ref,
                )
                for row in rows
            ]
            conn.execute("BEGIN IMMEDIATE")
            try:
                for item in dispositions:
                    conn.execute(
                        """INSERT INTO revocation_dispositions_v1(
                            effect_id,attempt_id,grant_id,grant_revision,status,item_order,
                            revocation_order,ordering_ref,owner,deadline,reconciliation_ref,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            item.effect_id,
                            item.attempt_id,
                            item.grant_id,
                            item.grant_revision,
                            item.status,
                            item.item_order,
                            item.revocation_order,
                            item.ordering_ref,
                            item.owner,
                            item.deadline,
                            item.reconciliation_ref,
                            self._now().isoformat(),
                        ),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return dispositions

    def dispositions(self) -> list[RevocationDisposition]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM revocation_dispositions_v1 ORDER BY effect_id"
            ).fetchall()
        return [
            RevocationDisposition(
                effect_id=row["effect_id"],
                attempt_id=row["attempt_id"],
                grant_id=row["grant_id"],
                grant_revision=row["grant_revision"],
                status=row["status"],
                item_order=int(row["item_order"]),
                revocation_order=int(row["revocation_order"]),
                ordering_ref=row["ordering_ref"],
                owner=row["owner"],
                deadline=row["deadline"],
                reconciliation_ref=row["reconciliation_ref"],
            )
            for row in rows
        ]

    def close_original_attempt(
        self,
        *,
        attempt_id: str,
        observation_state: ObservationState,
        observation: OrderingEvidence,
        observation_ref: str,
    ) -> CloseoutResult:
        if not self.closeout_writer_enabled:
            raise RuntimeError("original-attempt closeout writer disabled")
        if not observation_ref.strip():
            raise ValueError("observation_ref must be non-empty")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT effect_id FROM runtime_items_v1 WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise LookupError("original attempt is not retained")
            if observation_state in {"applied", "partial"}:
                closed = True
                reason = "authoritative_observation_closes_original_attempt"
            else:
                closed = False
                reason = "observation_does_not_establish_effect_outcome"
            result = CloseoutResult(
                effect_id=row["effect_id"],
                attempt_id=attempt_id,
                observation_state=observation_state,
                observation_ref=observation_ref,
                closed=closed,
                retry_eligible=False,
                reason=reason,
            )
            conn.execute(
                """INSERT INTO original_attempt_closeouts_v1(
                    attempt_id,effect_id,observation_state,observation_order,
                    observation_ref,closed,retry_eligible,reason,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    attempt_id,
                    result.effect_id,
                    observation_state,
                    observation.order,
                    observation_ref,
                    1 if closed else 0,
                    0,
                    reason,
                    self._now().isoformat(),
                ),
            )
        return result

    def closeout(self, attempt_id: str) -> CloseoutResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM original_attempt_closeouts_v1 WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            return None
        return CloseoutResult(
            effect_id=row["effect_id"],
            attempt_id=row["attempt_id"],
            observation_state=row["observation_state"],
            observation_ref=row["observation_ref"],
            closed=bool(row["closed"]),
            retry_eligible=bool(row["retry_eligible"]),
            reason=row["reason"],
        )

    def overdue_report(self, *, now: datetime | None = None) -> dict[str, int | float | None]:
        current = _utc(now) if now else self._now()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT deadline FROM revocation_dispositions_v1
                WHERE status='in_doubt' AND deadline IS NOT NULL"""
            ).fetchall()
        overdue = []
        for row in rows:
            deadline = _utc(datetime.fromisoformat(row["deadline"]))
            if deadline < current:
                overdue.append((current - deadline).total_seconds())
        return {
            "count": len(overdue),
            "oldest_age_seconds": max(overdue) if overdue else None,
            "youngest_age_seconds": min(overdue) if overdue else None,
        }


__all__ = [
    "PROFILE_ID",
    "CloseoutResult",
    "OrderingEvidence",
    "RevocationCloseoutStore",
    "RevocationDisposition",
]
