from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Protocol

EXECUTION_ENVELOPE_VERSION = "0.1.0"
CONTROL_PLANE_COMMIT = "283500652d47a692fb0b99a1172a6d5faffbd9a7"
MANIFEST_COMMIT = "46c950bed37fe3812000895430bc0312d29e37ce"
ALVORADA_COMMIT = "fb3d97938969a89e149e8ff8db2756091d1233fc"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def commitment(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


class DecisionSource(Protocol):
    def get_decision(self, decision_id: str) -> dict | None: ...


class InProcessDecisionSource:
    """Trusted-call adapter for canonical Control Plane decisions.

    The caller supplies only a decision ID. This is an in-process trust boundary,
    not transport authentication.
    """

    def __init__(self, lookup: Callable[[str], dict | None]):
        self._lookup = lookup

    def get_decision(self, decision_id: str) -> dict | None:
        value = self._lookup(decision_id)
        return copy.deepcopy(value) if value is not None else None


@dataclass(frozen=True)
class ExecutionOperation:
    actor: str
    principal: str
    institution_id: str
    manifest_id: str
    manifest_version: str
    manifest_digest: str
    proposal_commitment: str
    action_id: str
    adapter_id: str
    target: str
    payload: dict
    payload_commitment: str
    requested_permissions: tuple[str, ...]
    amount: float
    unit: str
    effects: int
    authority_context_id: str
    requirement_id: str
    grant_id: str
    grant_revision: str
    effective_max_effects: int

    def canonical_dict(self) -> dict:
        return {
            "actor": self.actor,
            "principal": self.principal,
            "institution_id": self.institution_id,
            "manifest_id": self.manifest_id,
            "manifest_version": self.manifest_version,
            "manifest_digest": self.manifest_digest,
            "proposal_commitment": self.proposal_commitment,
            "action_id": self.action_id,
            "adapter_id": self.adapter_id,
            "target": self.target,
            "payload": copy.deepcopy(self.payload),
            "payload_commitment": self.payload_commitment,
            "requested_permissions": list(self.requested_permissions),
            "amount": self.amount,
            "unit": self.unit,
            "effects": self.effects,
            "authority_context_id": self.authority_context_id,
            "requirement_id": self.requirement_id,
            "grant_id": self.grant_id,
            "grant_revision": self.grant_revision,
            "effective_max_effects": self.effective_max_effects,
        }


@dataclass(frozen=True)
class ExecutionEnvelope:
    version: str
    decision_id: str
    effect_id: str
    operation: ExecutionOperation
    attempt_id: str | None = None


@dataclass(frozen=True)
class LocalExecutionPolicy:
    allowed_institutions: frozenset[str]
    allowed_adapters: frozenset[str]
    allowed_actions: frozenset[str]
    allowed_target_prefixes: tuple[str, ...]
    allowed_units: frozenset[str]
    max_amount: float
    max_effects: int

    def check(self, op: ExecutionOperation) -> None:
        if op.institution_id not in self.allowed_institutions:
            raise PermissionError("local institution restriction")
        if op.adapter_id not in self.allowed_adapters:
            raise PermissionError("local adapter restriction")
        if op.action_id not in self.allowed_actions:
            raise PermissionError("local action restriction")
        if not any(op.target.startswith(prefix) for prefix in self.allowed_target_prefixes):
            raise PermissionError("local target restriction")
        if op.unit not in self.allowed_units:
            raise PermissionError("local unit restriction")
        if op.amount > self.max_amount:
            raise PermissionError("local amount restriction")
        if op.effects > self.max_effects or op.effective_max_effects > self.max_effects:
            raise PermissionError("local effect-count restriction")


@dataclass
class ExecutionResult:
    status: Literal["denied", "failed", "partial", "unknown", "executed", "reconciled"]
    decision_id: str
    effect_id: str
    attempt_id: str
    executed: bool
    observation: dict = field(default_factory=dict)
    error: str | None = None


class DurableRefundDestination:
    """SQLite synthetic refund destination with durable dedupe and local limits."""

    def __init__(self, root: str | Path, database_name: str = "refunds.sqlite3"):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._assert_no_symlink(self.root)
        raw_db = self.root / database_name
        if raw_db.is_symlink():
            raise PermissionError("database path must not be a symlink")
        self.path = raw_db.resolve()
        if self.root != self.path.parent and self.root not in self.path.parents:
            raise PermissionError("database path escapes execution root")
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _assert_no_symlink(path: Path) -> None:
        current = path
        while True:
            if current.is_symlink():
                raise PermissionError("symlinked execution root is not supported")
            if current.parent == current:
                break
            current = current.parent

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS effects (
                    effect_id TEXT PRIMARY KEY,
                    operation_digest TEXT NOT NULL,
                    grant_id TEXT NOT NULL,
                    target TEXT NOT NULL,
                    amount REAL NOT NULL,
                    unit TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('applied','partial'))
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY,
                    effect_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT
                );
                """
            )

    def observe(self, effect_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM effects WHERE effect_id = ?", (effect_id,)
            ).fetchone()
        if row is None:
            return {"effect_id": effect_id, "state": "absent", "destination_state": {}}
        return {
            "effect_id": effect_id,
            "state": row["state"],
            "destination_state": {
                "effect_id": row["effect_id"],
                "grant_id": row["grant_id"],
                "target": row["target"],
                "amount": row["amount"],
                "unit": row["unit"],
                "payload": json.loads(row["payload_json"]),
                "operation_digest": row["operation_digest"],
            },
        }

    def effect_count(self, grant_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM effects WHERE grant_id = ?", (grant_id,)
            ).fetchone()
            return int(row["n"])

    def record_attempt(
        self,
        attempt_id: str,
        effect_id: str,
        decision_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO attempts(attempt_id,effect_id,decision_id,status,error) VALUES(?,?,?,?,?)",
                (attempt_id, effect_id, decision_id, status, error),
            )

    def apply(self, envelope: ExecutionEnvelope, *, simulate: str | None = None) -> dict:
        op = envelope.operation
        digest = commitment(op.canonical_dict())
        payload_json = json.dumps(
            op.payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT operation_digest,state FROM effects WHERE effect_id = ?",
                (envelope.effect_id,),
            ).fetchone()
            if existing is not None:
                if existing["operation_digest"] != digest:
                    conn.execute("ROLLBACK")
                    raise PermissionError(
                        "effect_id is already bound to different operation content"
                    )
                conn.execute("COMMIT")
                return {
                    "duplicate": True,
                    "observation": self.observe(envelope.effect_id),
                }
            used = conn.execute(
                "SELECT COUNT(*) AS n FROM effects WHERE grant_id = ?", (op.grant_id,)
            ).fetchone()["n"]
            if int(used) >= op.effective_max_effects:
                conn.execute("ROLLBACK")
                raise PermissionError("local cumulative max_effects exhausted")
            state = "partial" if simulate == "partial" else "applied"
            conn.execute(
                "INSERT INTO effects(effect_id,operation_digest,grant_id,target,amount,unit,payload_json,state) VALUES(?,?,?,?,?,?,?,?)",
                (
                    envelope.effect_id,
                    digest,
                    op.grant_id,
                    op.target,
                    op.amount,
                    op.unit,
                    payload_json,
                    state,
                ),
            )
            conn.execute("COMMIT")
        if simulate == "lost_ack":
            raise TimeoutError("synthetic acknowledgement lost after durable commit")
        return {"duplicate": False, "observation": self.observe(envelope.effect_id)}


class SafeExecutor:
    """Supported Moltbot Safe runtime for the bounded local refund pilot."""

    def __init__(
        self,
        *,
        decisions: DecisionSource,
        destination: DurableRefundDestination,
        policy: LocalExecutionPolicy,
    ):
        self.decisions = decisions
        self.destination = destination
        self.policy = policy

    def execute(
        self, envelope: ExecutionEnvelope, *, simulate: str | None = None
    ) -> ExecutionResult:
        attempt_id = envelope.attempt_id or str(uuid.uuid4())
        try:
            self._validate_envelope(envelope)
            authoritative = self.decisions.get_decision(envelope.decision_id)
            self._validate_authoritative_decision(envelope, authoritative)
            self.policy.check(envelope.operation)
        except Exception as exc:
            self.destination.record_attempt(
                attempt_id,
                envelope.effect_id,
                envelope.decision_id,
                "denied",
                str(exc),
            )
            return ExecutionResult(
                "denied",
                envelope.decision_id,
                envelope.effect_id,
                attempt_id,
                False,
                error=str(exc),
            )

        existing = self.destination.observe(envelope.effect_id)
        if existing["state"] in {"applied", "partial"}:
            expected_digest = commitment(envelope.operation.canonical_dict())
            actual_digest = existing["destination_state"].get("operation_digest")
            if actual_digest != expected_digest:
                error = "effect_id is already bound to different operation content"
                self.destination.record_attempt(
                    attempt_id,
                    envelope.effect_id,
                    envelope.decision_id,
                    "denied",
                    error,
                )
                return ExecutionResult(
                    "denied",
                    envelope.decision_id,
                    envelope.effect_id,
                    attempt_id,
                    False,
                    observation=existing,
                    error=error,
                )
            status = "reconciled" if existing["state"] == "applied" else "partial"
            self.destination.record_attempt(
                attempt_id, envelope.effect_id, envelope.decision_id, status
            )
            return ExecutionResult(
                status,
                envelope.decision_id,
                envelope.effect_id,
                attempt_id,
                False,
                observation=existing,
            )

        try:
            ack = self.destination.apply(envelope, simulate=simulate)
            observation = ack["observation"]
            status = "partial" if observation["state"] == "partial" else "executed"
            self.destination.record_attempt(
                attempt_id, envelope.effect_id, envelope.decision_id, status
            )
            return ExecutionResult(
                status,
                envelope.decision_id,
                envelope.effect_id,
                attempt_id,
                True,
                observation=observation,
            )
        except TimeoutError as exc:
            observation = self.destination.observe(envelope.effect_id)
            self.destination.record_attempt(
                attempt_id,
                envelope.effect_id,
                envelope.decision_id,
                "unknown",
                str(exc),
            )
            return ExecutionResult(
                "unknown",
                envelope.decision_id,
                envelope.effect_id,
                attempt_id,
                True,
                observation=observation,
                error=str(exc),
            )
        except Exception as exc:
            self.destination.record_attempt(
                attempt_id,
                envelope.effect_id,
                envelope.decision_id,
                "failed",
                str(exc),
            )
            return ExecutionResult(
                "failed",
                envelope.decision_id,
                envelope.effect_id,
                attempt_id,
                True,
                error=str(exc),
            )

    def reconcile(self, envelope: ExecutionEnvelope) -> ExecutionResult:
        attempt_id = envelope.attempt_id or str(uuid.uuid4())
        observation = self.destination.observe(envelope.effect_id)
        if observation["state"] == "applied":
            status = "reconciled"
        elif observation["state"] == "partial":
            status = "partial"
        else:
            status = "unknown"
        self.destination.record_attempt(
            attempt_id, envelope.effect_id, envelope.decision_id, status
        )
        return ExecutionResult(
            status,
            envelope.decision_id,
            envelope.effect_id,
            attempt_id,
            False,
            observation=observation,
        )

    def _validate_envelope(self, envelope: ExecutionEnvelope) -> None:
        if envelope.version != EXECUTION_ENVELOPE_VERSION:
            raise ValueError("unsupported execution envelope version")
        if not envelope.decision_id or not envelope.effect_id:
            raise ValueError("decision_id and effect_id are required")
        op = envelope.operation
        if op.payload_commitment != commitment(op.payload):
            raise ValueError("payload commitment mismatch")
        if op.amount < 0 or op.effects < 0 or op.effective_max_effects < 1:
            raise ValueError("invalid amount/effect limits")
        required_strings = [
            op.actor,
            op.principal,
            op.institution_id,
            op.manifest_id,
            op.manifest_version,
            op.manifest_digest,
            op.proposal_commitment,
            op.action_id,
            op.adapter_id,
            op.target,
            op.authority_context_id,
            op.requirement_id,
            op.grant_id,
            op.grant_revision,
        ]
        if any(not isinstance(value, str) or not value for value in required_strings):
            raise ValueError("execution operation contains empty required fields")

    def _validate_authoritative_decision(
        self, envelope: ExecutionEnvelope, decision: dict | None
    ) -> None:
        if decision is None:
            raise PermissionError("decision not found at trusted boundary")
        if decision.get("decision_id") != envelope.decision_id:
            raise PermissionError("decision identifier mismatch")
        if decision.get("result") != "authorized":
            raise PermissionError("decision is not authorized")
        if decision.get("effect_id") != envelope.effect_id:
            raise PermissionError("effect identifier mismatch")
        binding = decision.get("binding")
        if not isinstance(binding, dict):
            raise PermissionError("authorized decision is missing binding")
        op = envelope.operation
        checks = {
            "proposal_commitment": op.proposal_commitment,
            "manifest_id": op.manifest_id,
            "manifest_version": op.manifest_version,
            "manifest_digest": op.manifest_digest,
            "actor": op.actor,
            "principal": op.principal,
            "action_id": op.action_id,
            "adapter_id": op.adapter_id,
            "target": op.target,
            "payload_commitment": op.payload_commitment,
            "requested_permissions": list(op.requested_permissions),
            "amount": op.amount,
            "unit": op.unit,
            "effects": op.effects,
            "authority_context_id": op.authority_context_id,
            "requirement_id": op.requirement_id,
            "grant_id": op.grant_id,
            "grant_revision": op.grant_revision,
            "effective_max_effects": op.effective_max_effects,
        }
        for key, expected in checks.items():
            if binding.get(key) != expected:
                raise PermissionError(f"decision binding mismatch: {key}")

        # The pinned Control Plane AuthorizationBinding does not carry
        # institution_id/authority_domain. institution_id is therefore narrowed
        # locally but cannot be independently re-bound to that issued decision.
