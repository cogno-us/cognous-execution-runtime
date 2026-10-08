from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

EXECUTION_ENVELOPE_VERSION = "0.2.0"
CONTROL_PLANE_COMMIT = "2ea9528eeb87e14ff10f05de06473122b9df540f"
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


@dataclass(frozen=True)
class ExecutionOperation:
    actor: str
    principal: str
    institution_id: str
    authority_domain: str
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
    tenant_id: str | None = None


@dataclass(frozen=True)
class ExecutionEnvelope:
    version: str
    decision_id: str
    effect_id: str
    operation: ExecutionOperation
    attempt_id: str | None = None


@dataclass(frozen=True)
class FrozenOperation:
    actor: str
    principal: str
    institution_id: str
    authority_domain: str
    manifest_id: str
    manifest_version: str
    manifest_digest: str
    proposal_commitment: str
    action_id: str
    adapter_id: str
    target: str
    payload_json: str
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
    tenant_id: str | None = None

    def payload(self) -> dict:
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):
            raise ValueError("payload snapshot is not an object")
        return value

    def canonical_dict(self) -> dict:
        value = {
            "actor": self.actor,
            "principal": self.principal,
            "institution_id": self.institution_id,
            "authority_domain": self.authority_domain,
            "manifest_id": self.manifest_id,
            "manifest_version": self.manifest_version,
            "manifest_digest": self.manifest_digest,
            "proposal_commitment": self.proposal_commitment,
            "action_id": self.action_id,
            "adapter_id": self.adapter_id,
            "target": self.target,
            "payload": self.payload(),
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
        if self.tenant_id is not None:
            value["tenant_id"] = self.tenant_id
        return value

    @property
    def digest(self) -> str:
        return commitment(self.canonical_dict())


@dataclass(frozen=True)
class FrozenEnvelope:
    version: str
    decision_id: str
    effect_id: str
    operation: FrozenOperation
    requested_attempt_id: str | None = None


def snapshot_envelope(envelope: ExecutionEnvelope) -> FrozenEnvelope:
    """Deep-snapshot request data before any trusted callback can run."""
    op = envelope.operation
    payload_copy = copy.deepcopy(op.payload)
    payload_json = json.dumps(
        payload_copy,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    frozen = FrozenOperation(
        actor=copy.deepcopy(op.actor),
        principal=copy.deepcopy(op.principal),
        institution_id=copy.deepcopy(op.institution_id),
        authority_domain=copy.deepcopy(op.authority_domain),
        manifest_id=copy.deepcopy(op.manifest_id),
        manifest_version=copy.deepcopy(op.manifest_version),
        manifest_digest=copy.deepcopy(op.manifest_digest),
        proposal_commitment=copy.deepcopy(op.proposal_commitment),
        action_id=copy.deepcopy(op.action_id),
        adapter_id=copy.deepcopy(op.adapter_id),
        target=copy.deepcopy(op.target),
        payload_json=payload_json,
        payload_commitment=copy.deepcopy(op.payload_commitment),
        requested_permissions=tuple(copy.deepcopy(op.requested_permissions))
        if isinstance(op.requested_permissions, (tuple, list))
        else copy.deepcopy(op.requested_permissions),
        amount=op.amount,
        unit=copy.deepcopy(op.unit),
        effects=op.effects,
        authority_context_id=copy.deepcopy(op.authority_context_id),
        requirement_id=copy.deepcopy(op.requirement_id),
        grant_id=copy.deepcopy(op.grant_id),
        grant_revision=copy.deepcopy(op.grant_revision),
        effective_max_effects=op.effective_max_effects,
        tenant_id=copy.deepcopy(op.tenant_id),
    )
    snap = FrozenEnvelope(
        version=copy.deepcopy(envelope.version),
        decision_id=copy.deepcopy(envelope.decision_id),
        effect_id=copy.deepcopy(envelope.effect_id),
        operation=frozen,
        requested_attempt_id=(
            copy.deepcopy(envelope.attempt_id) if envelope.attempt_id is not None else None
        ),
    )
    validate_snapshot(snap)
    return snap


def _strict_positive_int(value: object, *, name: str, exactly_one: bool = False) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    if exactly_one and value != 1:
        raise ValueError(f"{name} must equal exactly 1")
    if not exactly_one and value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _strict_finite_number(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return numeric


def validate_snapshot(envelope: FrozenEnvelope) -> None:
    if envelope.version != EXECUTION_ENVELOPE_VERSION:
        raise ValueError("unsupported execution envelope version")
    if (
        not isinstance(envelope.decision_id, str)
        or not envelope.decision_id
        or not isinstance(envelope.effect_id, str)
        or not envelope.effect_id
    ):
        raise ValueError("decision_id and effect_id must be non-empty strings")
    if envelope.requested_attempt_id is not None and (
        not isinstance(envelope.requested_attempt_id, str)
        or not envelope.requested_attempt_id
    ):
        raise ValueError("attempt_id must be a non-empty string when supplied")
    op = envelope.operation
    if not isinstance(op.payload_commitment, str) or not op.payload_commitment:
        raise ValueError("payload_commitment must be a non-empty string")
    if op.payload_commitment != commitment(op.payload()):
        raise ValueError("payload commitment mismatch")
    if not isinstance(op.requested_permissions, tuple) or any(
        not isinstance(value, str) or not value for value in op.requested_permissions
    ):
        raise ValueError("requested_permissions must contain non-empty strings")
    if not isinstance(op.unit, str) or not op.unit:
        raise ValueError("unit must be a non-empty string")
    _strict_finite_number(op.amount, name="amount")
    _strict_positive_int(op.effects, name="effects", exactly_one=True)
    _strict_positive_int(op.effective_max_effects, name="effective_max_effects")
    if op.tenant_id is not None and (
        not isinstance(op.tenant_id, str) or not (1 <= len(op.tenant_id) <= 128)
    ):
        raise ValueError("tenant_id must be an exact non-empty string up to 128 characters")
    required = [
        op.actor,
        op.principal,
        op.institution_id,
        op.authority_domain,
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
    if any(not isinstance(value, str) or not value for value in required):
        raise ValueError("execution operation contains empty required fields")


@dataclass(frozen=True)
class LocalExecutionPolicy:
    allowed_institutions: frozenset[str]
    allowed_authority_domains: frozenset[str]
    allowed_adapters: frozenset[str]
    allowed_actions: frozenset[str]
    allowed_target_prefixes: tuple[str, ...]
    allowed_units: frozenset[str]
    max_amount: float
    max_effects: int

    def check(self, op: FrozenOperation) -> None:
        max_amount = _strict_finite_number(self.max_amount, name="local max_amount")
        max_effects = _strict_positive_int(self.max_effects, name="local max_effects")
        if op.institution_id not in self.allowed_institutions:
            raise PermissionError("local institution restriction")
        if op.authority_domain not in self.allowed_authority_domains:
            raise PermissionError("local authority-domain restriction")
        if op.adapter_id not in self.allowed_adapters:
            raise PermissionError("local adapter restriction")
        if op.action_id not in self.allowed_actions:
            raise PermissionError("local action restriction")
        if not any(op.target.startswith(prefix) for prefix in self.allowed_target_prefixes):
            raise PermissionError("local target restriction")
        if op.unit not in self.allowed_units:
            raise PermissionError("local unit restriction")
        if float(op.amount) > max_amount:
            raise PermissionError("local amount restriction")
        if op.effects != 1:
            raise PermissionError("local adapter supports exactly one effect")
        if op.effective_max_effects > max_effects:
            raise PermissionError("local effect-count restriction")


@dataclass
class ExecutionResult:
    status: Literal[
        "denied",
        "failed",
        "partial",
        "unknown",
        "executed",
        "observed",
        "reconciled",
    ]
    decision_id: str
    effect_id: str
    attempt_id: str | None
    attempted: bool
    acknowledged: bool
    observed_state: str
    newly_executed: bool
    observation: dict | None = field(default_factory=dict)
    error: str | None = None
    control_plane_evidence: dict = field(default_factory=dict)


class AttemptIdConflict(RuntimeError):
    pass


class DurableRefundDestination:
    """Synthetic SQLite destination with durable effect and attempt history."""

    def __init__(self, root: str | Path, database_name: str = "refunds.sqlite3"):
        raw_root = Path(root).expanduser().absolute()
        self._reject_existing_symlink_components(raw_root)
        raw_root.mkdir(parents=True, exist_ok=True)
        self._reject_existing_symlink_components(raw_root)

        raw_db = raw_root / database_name
        self._reject_path_escape(raw_root, raw_db)
        if raw_db.exists() and raw_db.is_symlink():
            raise PermissionError("database path must not be a symlink")

        # Resolution happens only after symlink checks. A filesystem race remains
        # possible without an OS-level dirfd/openat confinement strategy.
        self.root = raw_root.resolve()
        self.path = raw_db.resolve(strict=False)
        if self.root != self.path.parent and self.root not in self.path.parents:
            raise PermissionError("database path escapes execution root")
        self._init_db()

    @staticmethod
    def _reject_path_escape(root: Path, candidate: Path) -> None:
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PermissionError("database path escapes execution root") from exc

    @staticmethod
    def _reject_existing_symlink_components(path: Path) -> None:
        current = Path(path.anchor) if path.anchor else Path()
        parts = path.parts[1:] if path.anchor else path.parts
        for part in parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise PermissionError("symlinked execution path is not supported")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS effects (
                    effect_id TEXT PRIMARY KEY,
                    operation_digest TEXT NOT NULL,
                    grant_id TEXT NOT NULL,
                    target TEXT NOT NULL,
                    amount REAL NOT NULL,
                    unit TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('applied','partial'))
                )
                """
            )
            conn.execute("PRAGMA journal_mode=WAL")

            attempt_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='attempts'"
            ).fetchone()
            if attempt_exists is not None:
                columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(attempts)").fetchall()
                }
                if "operation_digest" not in columns or "created_at" not in columns:
                    legacy_rows = conn.execute(
                        "SELECT attempt_id,effect_id,decision_id,status,error FROM attempts"
                    ).fetchall()
                    conn.execute("ALTER TABLE attempts RENAME TO attempts_v01")
                    conn.execute("DROP TABLE IF EXISTS attempt_events")
                    self._create_attempt_tables(conn)
                    for row in legacy_rows:
                        created_at = time.time()
                        conn.execute(
                            "INSERT INTO attempts(attempt_id,effect_id,decision_id,operation_digest,created_at) VALUES(?,?,?,?,?)",
                            (
                                row["attempt_id"],
                                row["effect_id"],
                                row["decision_id"],
                                "legacy:unknown",
                                created_at,
                            ),
                        )
                        conn.execute(
                            "INSERT INTO attempt_events(attempt_id,status,error,created_at) VALUES(?,?,?,?)",
                            (
                                row["attempt_id"],
                                row["status"],
                                row["error"],
                                created_at,
                            ),
                        )
                    conn.execute("DROP TABLE attempts_v01")
                else:
                    self._create_attempt_tables(conn)
            else:
                self._create_attempt_tables(conn)

    @staticmethod
    def _create_attempt_tables(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY,
                effect_id TEXT NOT NULL,
                decision_id TEXT NOT NULL,
                operation_digest TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attempt_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY(attempt_id) REFERENCES attempts(attempt_id)
            );
            CREATE TABLE IF NOT EXISTS failure_records_v1 (
                failure_id TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL,
                effect_id TEXT NOT NULL,
                attempt_id TEXT,
                proposal_commitment TEXT,
                manifest_version TEXT,
                manifest_digest TEXT,
                grant_id TEXT,
                grant_revision TEXT,
                failure_class TEXT NOT NULL CHECK(failure_class IN (
                    'policy_denial','authority_hold','malformed_input',
                    'capability_unavailable','evaluation_error','dispatch_error'
                )),
                reason_code TEXT NOT NULL,
                stage TEXT NOT NULL CHECK(stage IN ('pre_dispatch','post_dispatch')),
                detail TEXT,
                created_at REAL NOT NULL
            );
            """
        )

    def record_failure(
        self,
        snapshot: FrozenEnvelope,
        *,
        failure_class: str,
        reason_code: str,
        stage: str,
        attempt_id: str | None = None,
        detail: str | None = None,
        failure_id: str | None = None,
    ) -> str:
        """Persist one typed C4 failure without manufacturing an execution attempt."""
        if failure_class not in {
            "policy_denial",
            "authority_hold",
            "malformed_input",
            "capability_unavailable",
            "evaluation_error",
            "dispatch_error",
        }:
            raise ValueError("unsupported failure class")
        if stage not in {"pre_dispatch", "post_dispatch"}:
            raise ValueError("unsupported failure stage")
        failure_id = failure_id or str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO failure_records_v1(
                    failure_id,decision_id,effect_id,attempt_id,proposal_commitment,
                    manifest_version,manifest_digest,grant_id,grant_revision,
                    failure_class,reason_code,stage,detail,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    failure_id,
                    snapshot.decision_id,
                    snapshot.effect_id,
                    attempt_id,
                    snapshot.operation.proposal_commitment,
                    snapshot.operation.manifest_version,
                    snapshot.operation.manifest_digest,
                    snapshot.operation.grant_id,
                    snapshot.operation.grant_revision,
                    failure_class,
                    reason_code,
                    stage,
                    detail,
                    time.time(),
                ),
            )
        return failure_id

    def failure_history(self, effect_id: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if effect_id is None:
                rows = conn.execute(
                    "SELECT * FROM failure_records_v1 ORDER BY created_at,failure_id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM failure_records_v1 WHERE effect_id=? ORDER BY created_at,failure_id",
                    (effect_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def begin_attempt(
        self, requested_attempt_id: str | None, snapshot: FrozenEnvelope
    ) -> str:
        attempt_id = requested_attempt_id or str(uuid.uuid4())
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO attempts(attempt_id,effect_id,decision_id,operation_digest,created_at) VALUES(?,?,?,?,?)",
                    (
                        attempt_id,
                        snapshot.effect_id,
                        snapshot.decision_id,
                        snapshot.operation.digest,
                        time.time(),
                    ),
                )
                conn.execute(
                    "INSERT INTO attempt_events(attempt_id,status,error,created_at) VALUES(?,?,?,?)",
                    (attempt_id, "attempted", None, time.time()),
                )
        except sqlite3.IntegrityError as exc:
            raise AttemptIdConflict(f"attempt_id already exists: {attempt_id}") from exc
        return attempt_id

    def transition_attempt(
        self, attempt_id: str, status: str, error: str | None = None
    ) -> None:
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(f"unknown attempt_id: {attempt_id}")
            conn.execute(
                "INSERT INTO attempt_events(attempt_id,status,error,created_at) VALUES(?,?,?,?)",
                (attempt_id, status, error, time.time()),
            )

    def attempt_history(self, attempt_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status,error,created_at FROM attempt_events WHERE attempt_id = ? ORDER BY event_id",
                (attempt_id,),
            ).fetchall()
        return [dict(row) for row in rows]

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
                "state": row["state"],
                "grant_id": row["grant_id"],
                "target": row["target"],
                "amount": row["amount"],
                "unit": row["unit"],
                "payload": json.loads(row["payload_json"]),
                "operation_digest": row["operation_digest"],
            },
        }

    def observe_bound(self, snapshot: FrozenEnvelope) -> dict:
        observation = self.observe(snapshot.effect_id)
        if observation["state"] != "absent":
            actual = observation["destination_state"].get("operation_digest")
            if actual != snapshot.operation.digest:
                raise PermissionError(
                    "effect_id is bound to different operation content"
                )
        return observation

    def effect_count(self, grant_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM effects WHERE grant_id = ?", (grant_id,)
            ).fetchone()
            return int(row["n"])

    def commit(
        self, snapshot: FrozenEnvelope, *, simulate: str | None = None
    ) -> dict:
        op = snapshot.operation
        digest = op.digest
        payload_json = op.payload_json
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='authority_effect_profile_v1'"
            ).fetchone():
                conn.execute("ROLLBACK")
                raise PermissionError(
                    "authority-effect-profile destination requires atomic claim execution"
                )
            self._check_commit_profile(conn, snapshot)
            existing = conn.execute(
                "SELECT operation_digest,state FROM effects WHERE effect_id = ?",
                (snapshot.effect_id,),
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
                    "observation": self.observe_bound(snapshot),
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
                    snapshot.effect_id,
                    digest,
                    op.grant_id,
                    op.target,
                    float(op.amount),
                    op.unit,
                    payload_json,
                    state,
                ),
            )
            conn.execute("COMMIT")

        if simulate in {"lost_ack", "crash_after_commit"}:
            raise TimeoutError("synthetic acknowledgement unavailable after durable commit")
        return {"duplicate": False, "observation": self.observe_bound(snapshot)}

    def _check_commit_profile(self, conn, snapshot: FrozenEnvelope) -> None:
        # Opting a database into intent ownership disables its legacy write path.
        # Current authorization is still the responsibility of the trusted host.
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='refund_intent_registry_v1'").fetchone():
            raise PermissionError("intent-profile destination requires bound dispatch")


class LocalDestinationExecutor:
    """Low-level destination executor.

    This class does not establish institutional authorization. The supported
    integration calls it only after the pinned Control Plane revalidates current
    authority and invokes the destination adapter at effect time.
    """

    def __init__(self, destination: DurableRefundDestination, policy: LocalExecutionPolicy):
        self.destination = destination
        self.policy = policy

    def execute_snapshot(
        self, snapshot: FrozenEnvelope, *, simulate: str | None = None
    ) -> ExecutionResult:
        self.policy.check(snapshot.operation)
        try:
            attempt_id = self.destination.begin_attempt(
                snapshot.requested_attempt_id, snapshot
            )
        except AttemptIdConflict as exc:
            # Preserve the original attempt history; record this rejected request
            # under a fresh attempt identity instead of overwriting it.
            rejected = FrozenEnvelope(
                snapshot.version,
                snapshot.decision_id,
                snapshot.effect_id,
                snapshot.operation,
                None,
            )
            attempt_id = self.destination.begin_attempt(None, rejected)
            self.destination.transition_attempt(attempt_id, "denied", str(exc))
            return ExecutionResult(
                "denied",
                snapshot.decision_id,
                snapshot.effect_id,
                attempt_id,
                True,
                False,
                "unknown",
                False,
                error=str(exc),
            )

        try:
            existing = self.destination.observe_bound(snapshot)
        except Exception as exc:
            self.destination.transition_attempt(attempt_id, "denied", str(exc))
            return ExecutionResult(
                "denied",
                snapshot.decision_id,
                snapshot.effect_id,
                attempt_id,
                True,
                False,
                "unknown",
                False,
                error=str(exc),
            )

        if existing["state"] in {"applied", "partial", "unknown"}:
            status = "reconciled" if existing["state"] == "applied" else existing["state"]
            self.destination.transition_attempt(attempt_id, status)
            return ExecutionResult(
                status,
                snapshot.decision_id,
                snapshot.effect_id,
                attempt_id,
                True,
                False,
                existing["state"],
                False,
                observation=existing,
            )

        try:
            ack = self.destination.commit(snapshot, simulate=simulate)
            observation = ack["observation"]
            status = "partial" if observation["state"] == "partial" else (
                "reconciled" if ack["duplicate"] else "executed"
            )
            self.destination.transition_attempt(attempt_id, status)
            return ExecutionResult(
                status,
                snapshot.decision_id,
                snapshot.effect_id,
                attempt_id,
                True,
                True,
                observation["state"],
                not ack["duplicate"],
                observation=observation,
            )
        except TimeoutError as exc:
            try:
                observation = self.destination.observe_bound(snapshot)
            except Exception:
                observation = {"effect_id": snapshot.effect_id, "state": "unknown", "destination_state": {}}
            self.destination.transition_attempt(attempt_id, "unknown", str(exc))
            return ExecutionResult(
                "unknown",
                snapshot.decision_id,
                snapshot.effect_id,
                attempt_id,
                True,
                False,
                observation["state"],
                False,
                observation=observation,
                error=str(exc),
            )
        except Exception as exc:
            self.destination.transition_attempt(attempt_id, "failed", str(exc))
            return ExecutionResult(
                "failed",
                snapshot.decision_id,
                snapshot.effect_id,
                attempt_id,
                True,
                False,
                "unknown",
                False,
                error=str(exc),
            )

    def observe_historical(self, envelope: ExecutionEnvelope) -> ExecutionResult:
        """Observe a previously bound effect without renewing authorization."""
        snapshot = snapshot_envelope(envelope)
        observation = self.destination.observe_bound(snapshot)
        state = observation["state"]
        return ExecutionResult(
            "observed",
            snapshot.decision_id,
            snapshot.effect_id,
            None,
            False,
            False,
            state,
            False,
            observation=observation,
        )
