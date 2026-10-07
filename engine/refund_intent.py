"""Opt-in synthetic intent registry and destination ownership enforcement.

Only trusted host code may provision intents or access this database. Reservation
does not authorize execution. The Control Plane and destination must enforce the
binding. This is not authentication of an external domain service.
"""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import json
import sqlite3

from .safe_executor import DurableRefundDestination, FrozenEnvelope, commitment

PROFILE = "synthetic-refund-intent/1"


@dataclass(frozen=True)
class RefundIntent:
    institution_id: str
    authority_domain: str
    customer_id: str
    request_id: str
    effect_class: str = "refund.issue"

    @property
    def key(self) -> str:
        parts = (PROFILE, self.institution_id, self.authority_domain,
                 self.customer_id, self.effect_class, self.request_id)
        if any(not isinstance(p, str) or not p.strip() for p in parts):
            raise ValueError("intent scope and request identity must be nonempty strings")
        if self.effect_class != "refund.issue":
            raise ValueError("only the synthetic refund profile is supported")
        # A JSON array preserves tuple boundaries, including punctuation in IDs.
        return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class IntentClaim:
    original_effect_id: str
    original_operation_digest: str
    newly_reserved: bool
    dispatch_started: bool


def business_commitment(intent: RefundIntent, snapshot: FrozenEnvelope) -> str:
    op = snapshot.operation
    payload = op.payload()
    if (op.institution_id != intent.institution_id
            or op.authority_domain != intent.authority_domain
            or payload.get("customer_id") != intent.customer_id
            or op.action_id not in {"refund.issue", "urn:cognous:action:refund-issue-routine-v1"}):
        raise PermissionError("intent scope does not match operation")
    return commitment({
        "intent": intent.key, "action_id": op.action_id,
        "target": op.target, "adapter_id": op.adapter_id,
        "amount": op.amount, "unit": op.unit, "payload": payload,
        "effects": op.effects,
    })


class RefundIntentRegistry:
    """Durable owner binding in the synthetic destination's SQLite database.

No release, expiry, reassignment or retry operation exists. A consumed dispatch
claim remains consumed even if the caller crashes before performing any effect.
    Creating the registry opts this database out of legacy unbound writes.
"""

    def __init__(self, destination: DurableRefundDestination):
        self.path = destination.path
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            initialized = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                       "AND name='refund_intent_registry_v1'").fetchone()
            claims_exist = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                        "AND name='refund_intent_claims_v1'").fetchone()
            if bool(initialized) != bool(claims_exist):
                raise PermissionError("incomplete intent schema; refuse ownership reconstruction")
            if not initialized and conn.execute("SELECT 1 FROM effects LIMIT 1").fetchone():
                raise PermissionError("intent profile requires an empty destination; no inferred backfill")
            schema = """
                CREATE TABLE IF NOT EXISTS refund_intent_registry_v1 (
                    intent_key TEXT PRIMARY KEY,
                    business_digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS refund_intent_claims_v1 (
                    intent_key TEXT PRIMARY KEY REFERENCES refund_intent_registry_v1,
                    effect_id TEXT UNIQUE NOT NULL,
                    operation_digest TEXT NOT NULL,
                    dispatch_started INTEGER NOT NULL DEFAULT 0
                        CHECK(dispatch_started IN (0,1))
                );
            """
            for statement in schema.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute("COMMIT")

    @contextmanager
    def _connect(self):
        # mode=rw prevents accidentally replacing a missing durable database.
        conn = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True,
                               timeout=10, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=FULL")
            with conn:
                yield conn
        finally:
            conn.close()

    def provision(self, intent: RefundIntent, snapshot: FrozenEnvelope) -> None:
        """Trusted domain-service operation; never expose to an agent.

        Reprovisioning identical content is idempotent. Amendments are rejected.
        This synthetic registry does not authenticate an external institution.
        """
        key, digest = intent.key, business_commitment(intent, snapshot)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT business_digest FROM refund_intent_registry_v1 "
                               "WHERE intent_key=?", (key,)).fetchone()
            if row is not None and row[0] != digest:
                raise PermissionError("intent provisioning conflicts with immutable operation")
            conn.execute("INSERT OR IGNORE INTO refund_intent_registry_v1 VALUES (?,?)",
                         (key, digest))
            conn.execute("COMMIT")

    @staticmethod
    def _registered(conn, intent, snapshot):
        digest = business_commitment(intent, snapshot)
        row = conn.execute("SELECT business_digest FROM refund_intent_registry_v1 "
                           "WHERE intent_key=?", (intent.key,)).fetchone()
        if row is None or row[0] != digest:
            raise PermissionError("unregistered intent or conflicting operation")

    def reserve(self, intent: RefundIntent, snapshot: FrozenEnvelope) -> IntentClaim:
        if not isinstance(snapshot.effect_id, str) or not snapshot.effect_id.strip():
            raise ValueError("effect identity is required")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._registered(conn, intent, snapshot)
            row = conn.execute("SELECT * FROM refund_intent_claims_v1 WHERE intent_key=?",
                               (intent.key,)).fetchone()
            if row is not None:
                result = IntentClaim(row["effect_id"], row["operation_digest"],
                                     False, bool(row["dispatch_started"]))
            else:
                conn.execute("INSERT INTO refund_intent_claims_v1 "
                             "(intent_key,effect_id,operation_digest) VALUES (?,?,?)",
                             (intent.key, snapshot.effect_id, snapshot.operation.digest))
                result = IntentClaim(snapshot.effect_id, snapshot.operation.digest, True, False)
            conn.execute("COMMIT")
        return result

    def mark_dispatch_started(self, intent: RefundIntent, snapshot: FrozenEnvelope) -> bool:
        """Consume the original owner's one dispatch claim, durably before dispatch.

        True is a storage transition only, never authorization. False means hold;
        it cannot be used to retry, even after a point-in-time absent observation.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._registered(conn, intent, snapshot)
            row = conn.execute("SELECT * FROM refund_intent_claims_v1 WHERE intent_key=?",
                               (intent.key,)).fetchone()
            if (row is None or row["effect_id"] != snapshot.effect_id
                    or row["operation_digest"] != snapshot.operation.digest):
                raise PermissionError("dispatch must retain the exact original owner")
            started = not bool(row["dispatch_started"])
            if started:
                conn.execute("UPDATE refund_intent_claims_v1 SET dispatch_started=1 "
                             "WHERE intent_key=?", (intent.key,))
            conn.execute("COMMIT")
        return started

    def export_intent(self, intent: RefundIntent) -> dict:
        """Read-only sidecar evidence, not an authorization or Replay profile."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT r.business_digest,c.effect_id,c.operation_digest,c.dispatch_started "
                "FROM refund_intent_registry_v1 r LEFT JOIN refund_intent_claims_v1 c "
                "ON r.intent_key=c.intent_key WHERE r.intent_key=?", (intent.key,)).fetchone()
            if row is None:
                raise PermissionError("unregistered intent")
            evidence = {"profile": PROFILE, "intent_key": json.loads(intent.key),
                        "business_digest": row["business_digest"],
                        "original_effect_id": row["effect_id"],
                        "original_operation_digest": row["operation_digest"],
                        "dispatch_started": bool(row["dispatch_started"]),
                        "retry_eligible": False,
                        "authority_established_by_registry": False}
        return evidence


class IntentRefundDestination(DurableRefundDestination):
    """Per-request binding used only after current Control Plane authorization.

    Host code, database administration and direct SQL are trusted. This class is
    a synthetic in-process boundary, not confinement against hostile host code.
    """

    def __init__(self, destination: DurableRefundDestination, intent: RefundIntent):
        self.root, self.path = destination.root, destination.path
        self.intent = intent
        # Do not create/repair profile tables on the execution path.
        self.registry = RefundIntentRegistry.__new__(RefundIntentRegistry)
        self.registry.path = self.path

    def commit(self, snapshot: FrozenEnvelope, *, simulate: str | None = None) -> dict:
        claim = self.registry.reserve(self.intent, snapshot)
        if (claim.original_effect_id != snapshot.effect_id
                or claim.original_operation_digest != snapshot.operation.digest):
            raise PermissionError("intent already owned by original effect: "
                                  + claim.original_effect_id)
        if not self.registry.mark_dispatch_started(self.intent, snapshot):
            raise PermissionError("original dispatch already started; hold without retry: "
                                  + claim.original_effect_id)
        return super().commit(snapshot, simulate=simulate)

    def _check_commit_profile(self, conn, snapshot: FrozenEnvelope) -> None:
        self.registry._registered(conn, self.intent, snapshot)
        row = conn.execute("SELECT * FROM refund_intent_claims_v1 WHERE intent_key=?",
                           (self.intent.key,)).fetchone()
        if (row is None or row["effect_id"] != snapshot.effect_id
                or row["operation_digest"] != snapshot.operation.digest
                or row["dispatch_started"] != 1):
            raise PermissionError("destination does not retain original dispatch ownership")
