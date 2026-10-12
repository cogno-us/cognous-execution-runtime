"""Experimental synthetic evidence ledger; no execution or retry authority.

Not the accepted recovery successor contract. Explicit construction is required;
no executor, v1 API, or production admission path imports this module.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

PROFILE = "cognous-recovery-resolution/synthetic-0.1"
CANONICALIZATION = "python-json-sort-ascii-compact/1"
POLICY = "synthetic-structural/2"
WORK_STATES = (
    "awaiting_observation", "awaiting_finality", "partial_followup",
    "conflict_review", "resolved",
)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone required")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class SyntheticObservation:
    event_id: str
    effect_id: str
    attempt_id: str
    adapter_id: str
    client_reference: str
    state: str
    observed_at: datetime
    ordering_domain: str
    order: int
    observation_ref: str
    observation_digest: str
    observer_ref: str
    watermark_ref: str
    provider_acceptance_ref: str
    evidence_location_ref: str
    complete_coverage: bool = False
    final: bool = False

    def payload(self):
        data = asdict(self)
        data["observed_at"] = _utc(self.observed_at).isoformat()
        for key, value in data.items():
            if key not in {"order", "complete_coverage", "final"}:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"non-empty string required: {key}")
        if self.state not in {"unknown", "absent", "partial", "applied"}:
            raise ValueError("invalid observation state")
        if type(self.order) is not int or not 0 <= self.order < 2**63:
            raise ValueError("invalid order")
        if type(self.complete_coverage) is not bool or type(self.final) is not bool:
            raise ValueError("coverage and finality must be booleans")
        if len(self.observation_digest) != 64 or any(c not in "0123456789abcdef" for c in self.observation_digest):
            raise ValueError("observation_digest must be lowercase SHA256")
        return data


class SyntheticRecoveryLedger:
    """One trusted local database, synthetic assertions only, no network IO.

    Reference/digest presence is structural validation, not authentication. All
    returned records and metrics carry an explicit synthetic profile identifier.
    """

    def __init__(self, database, *, synthetic=False, writer_enabled=True, clock=None):
        if synthetic is not True:
            raise ValueError("explicit synthetic=True required; production admission unsupported")
        self.path = Path(database).resolve()
        self.writer_enabled = writer_enabled
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        with self._connection() as conn:
            # Require the owning v1 store to exist; never invent original attempts.
            conn.execute("SELECT effect_id FROM runtime_items_v1 LIMIT 0")
            conn.execute("SELECT effect_id FROM revocation_dispositions_v1 LIMIT 0")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS recovery_synthetic_events (
                    attempt_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    event_id TEXT NOT NULL, digest TEXT NOT NULL,
                    kind TEXT NOT NULL, body TEXT NOT NULL, receipt TEXT NOT NULL,
                    PRIMARY KEY(attempt_id, revision), UNIQUE(event_id, digest)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS recovery_synthetic_admitted_id
                    ON recovery_synthetic_events(event_id) WHERE kind='observation';
                CREATE TABLE IF NOT EXISTS recovery_synthetic_projection (
                    attempt_id TEXT PRIMARY KEY, body TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS recovery_synthetic_no_update
                    BEFORE UPDATE ON recovery_synthetic_events
                    BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END;
                CREATE TRIGGER IF NOT EXISTS recovery_synthetic_no_delete
                    BEFORE DELETE ON recovery_synthetic_events
                    BEGIN SELECT RAISE(ABORT, 'append-only evidence'); END;
            """)

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _identity(conn, effect, attempt):
        rows = conn.execute("SELECT * FROM runtime_items_v1 WHERE attempt_id=?", (attempt,)).fetchall()
        disposition = conn.execute(
            "SELECT * FROM revocation_dispositions_v1 WHERE effect_id=?", (effect,)
        ).fetchone()
        if (not attempt or len(rows) != 1 or rows[0]["effect_id"] != effect
                or disposition is None or disposition["status"] != "in_doubt"
                or disposition["attempt_id"] != attempt):
            raise ValueError("HOLD: missing or ambiguous original in-doubt binding")
        row = rows[0]
        if any(row[k] != disposition[k] for k in ("grant_id", "grant_revision")):
            raise ValueError("HOLD: inconsistent grant binding")
        return {key: row[key] for key in (
            "effect_id", "attempt_id", "decision_id", "grant_id", "grant_revision"
        )}

    @staticmethod
    def _projection(conn, attempt):
        row = conn.execute("SELECT body FROM recovery_synthetic_projection WHERE attempt_id=?", (attempt,)).fetchone()
        return json.loads(row[0]) if row else {
            "profile": PROFILE, "revision": 0, "head_digest": None,
            "observation": "none", "finality": "unestablished",
            "work_state": "awaiting_observation", "retry_eligible": False,
            "execution_authority": "none", "settlement_verified": False,
        }

    def current(self, attempt_id):
        with self._connection() as conn:
            # Projection and original binding must belong to the same snapshot.
            conn.execute("BEGIN")
            current = self._projection(conn, attempt_id)
            retained = current.get("identity")
            if retained is not None:
                identity = self._identity(conn, retained["effect_id"], attempt_id)
                if identity != retained:
                    raise ValueError("HOLD: original identity changed")
            conn.execute("COMMIT")
            return current

    def history(self, attempt_id):
        with self._connection() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM recovery_synthetic_events WHERE attempt_id=? ORDER BY revision", (attempt_id,)
            )]

    def export_attempt(self, *, effect_id, attempt_id):
        """Return a JSON-compatible inspection snapshot, not a consumer contract.

        Legacy facts are retained separately with explicit provenance gaps. This
        method never imports them into the synthetic stream or upgrades assurance.
        """
        with self._connection() as conn:
            conn.execute("BEGIN")
            identity = self._identity(conn, effect_id, attempt_id)
            current = self._projection(conn, attempt_id)
            if current.get("identity", identity) != identity:
                raise ValueError("HOLD: original identity changed")
            original = dict(conn.execute(
                "SELECT * FROM runtime_items_v1 WHERE effect_id=?", (effect_id,)
            ).fetchone())
            disposition = dict(conn.execute(
                "SELECT * FROM revocation_dispositions_v1 WHERE effect_id=?", (effect_id,)
            ).fetchone())
            legacy = conn.execute(
                "SELECT * FROM original_attempt_closeouts_v1 WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if legacy is not None and legacy["effect_id"] != effect_id:
                raise ValueError("HOLD: legacy closeout binding differs")
            events = [
                {"revision": row["revision"], "event_id": row["event_id"],
                 "digest": row["digest"], "kind": row["kind"],
                 "body": json.loads(row["body"]),
                 "admission": json.loads(row["receipt"])}
                for row in conn.execute(
                    "SELECT * FROM recovery_synthetic_events WHERE attempt_id=? ORDER BY revision",
                    (attempt_id,),
                )
            ]
            snapshot = {
                "export_format": "cognous-recovery-inspection/synthetic-0.1",
                "profile": PROFILE, "evaluated_at": _utc(self.clock()).isoformat(),
                "original": original, "disposition": disposition,
                "events": events, "current": current,
                "legacy": None if legacy is None else {
                    "profile": "cognous-revocation-closeout/1",
                    "assurance": "legacy_unverified", "record": dict(legacy),
                    "unavailable_provenance": [
                        "observer_ref", "watermark_ref", "provider_acceptance_ref",
                        "evidence_location_ref", "observation_digest", "admission_policy",
                    ],
                },
                "limitations": [
                    "synthetic_assertions_only", "not_a_replay_or_evidence_pack_mapping",
                    "no_source_authentication", "no_settlement_verification",
                    "legacy_facts_do_not_resolve_synthetic_work",
                ],
            }
            conn.execute("COMMIT")
            return snapshot

    def append(self, observation, *, expected_revision, predecessor_digest):
        if not self.writer_enabled:
            raise RuntimeError("evidence writer disabled")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        if predecessor_digest is not None and not isinstance(predecessor_digest, str):
            raise ValueError("invalid predecessor digest")
        data = observation.payload()
        envelope = {"profile": PROFILE, "canonicalization": CANONICALIZATION,
                    "observation": data, "expected_revision": expected_revision,
                    "predecessor_digest": predecessor_digest}
        body = _json(envelope)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                identity = self._identity(conn, observation.effect_id, observation.attempt_id)
                current = self._projection(conn, observation.attempt_id)
                if current.get("identity", identity) != identity:
                    raise ValueError("HOLD: original identity changed")
                prior = conn.execute("SELECT * FROM recovery_synthetic_events WHERE event_id=? ORDER BY revision", (observation.event_id,)).fetchall()
                exact = next((r for r in prior if r["digest"] == digest), None)
                if exact:
                    conn.execute("COMMIT")
                    return {"admission": json.loads(exact["receipt"]), "current": current}
                if any(r["attempt_id"] != observation.attempt_id for r in prior):
                    raise ValueError("HOLD: event ID belongs to another attempt")
                if current["revision"] != expected_revision or current["head_digest"] != predecessor_digest:
                    raise ValueError("HOLD: stale stream revision; re-read and re-evaluate")
                # Lock acquisition may wait: assess freshness inside the transaction.
                # Exact duplicates above retain their original evaluation receipt.
                now = _utc(self.clock())
                age = (now - _utc(observation.observed_at)).total_seconds()
                if not 0 <= age <= 86400:
                    raise ValueError("HOLD: synthetic observation outside 24-hour window")
                previous = current.get("last_observation")
                reasons = ["event_id_reused_with_changed_body"] if prior else []
                if previous:
                    for key in ("adapter_id", "client_reference", "ordering_domain"):
                        if previous[key] != data[key]:
                            reasons.append(f"{key}_changed")
                    if data["order"] <= previous["order"]:
                        reasons.append("source_order_not_increasing")
                    if previous["state"] in {"applied", "partial"} and data["state"] in {"unknown", "absent"}:
                        reasons.append("known_effect_contradicted")
                    if previous["state"] == "applied" and data["state"] == "partial":
                        reasons.append("applied_regressed_to_partial")
                    if previous["final"] and not data["final"]:
                        reasons.append("finality_regressed")
                    if previous["complete_coverage"] and not data["complete_coverage"]:
                        reasons.append("coverage_regressed")
                conflict = bool(reasons)
                existing_hold = current["work_state"] == "conflict_review"
                kind = "disagreement" if conflict else "observation"
                revision = current["revision"] + 1
                receipt = {"profile": PROFILE, "policy": POLICY, "assurance": "synthetic_admission",
                           "revision": revision, "digest": digest, "recorded_at": now.isoformat(),
                           "kind": kind, "identity": identity,
                           "prior_work_state": current["work_state"],
                           "conflict_reasons": reasons, "freshness_age_seconds": age,
                           "checks": {
                               "structure": "passed", "retained_identity": "passed",
                               "expected_revision": "passed", "predecessor": "passed",
                               "freshness": "passed_synthetic_policy",
                               "source_authentication": "not_established",
                               "independent_verification": "not_established",
                               "provider_finality": "not_established",
                           },
                           "reason": "conflict_hold" if conflict else (
                               "existing_conflict_hold" if existing_hold else "synthetic_checks_passed")}
                if conflict or existing_hold:
                    current["work_state"] = "conflict_review"
                    current["finality"] = "unestablished"
                else:
                    state = data["state"]
                    resolved = state == "applied" and data["complete_coverage"] and data["final"]
                    current.update(observation=state, last_observation=data,
                                   finality="synthetic_profile_only" if resolved else "unestablished",
                                   work_state="resolved" if resolved else (
                                       "partial_followup" if state == "partial" else "awaiting_finality"))
                current.update(revision=revision, head_digest=digest, identity=identity)
                # A collision retains only a commitment to the changed body.
                retained = _json({"conflicting_digest": digest, "event_id": observation.event_id}) if prior else body
                conn.execute("INSERT INTO recovery_synthetic_events VALUES(?,?,?,?,?,?,?)", (
                    observation.attempt_id, revision, observation.event_id, digest, kind, retained, _json(receipt)))
                conn.execute("INSERT INTO recovery_synthetic_projection VALUES(?,?) ON CONFLICT(attempt_id) DO UPDATE SET body=excluded.body", (observation.attempt_id, _json(current)))
                conn.execute("COMMIT")
                return {"admission": receipt, "current": current}
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def metrics(self, *, now=None):
        evaluated = _utc(now or self.clock())
        report = {"profile": PROFILE, "evaluated_at": evaluated.isoformat(),
                  "historical_in_doubt": 0, "unattributable_effects": 0,
                  "current_unresolved": 0, "current_overdue": 0,
                  "invalid_or_missing_deadlines": 0,
                  "work_states": dict.fromkeys(WORK_STATES, 0)}
        with self._connection() as conn:
            conn.execute("BEGIN")  # All denominators and projections share one snapshot.
            for row in conn.execute("SELECT * FROM revocation_dispositions_v1 WHERE status='in_doubt'").fetchall():
                report["historical_in_doubt"] += 1
                try:
                    identity = self._identity(conn, row["effect_id"], row["attempt_id"])
                    current = self._projection(conn, row["attempt_id"])
                    if current.get("identity", identity) != identity:
                        raise ValueError("identity changed")
                except ValueError:
                    report["unattributable_effects"] += 1
                    continue
                state = current["work_state"]
                report["work_states"][state] += 1
                if state == "resolved":
                    continue
                report["current_unresolved"] += 1
                try:
                    deadline = _utc(datetime.fromisoformat(row["deadline"]))
                except (TypeError, ValueError):
                    report["invalid_or_missing_deadlines"] += 1
                    continue
                report["current_overdue"] += int(deadline < evaluated)
            conn.execute("COMMIT")
        return report
