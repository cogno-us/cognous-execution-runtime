from __future__ import annotations

import copy
import dataclasses
import json
import sqlite3
from pathlib import Path
from typing import Any

from .control_plane_adapter import PinnedControlPlaneExecutor
from .safe_executor import (
    EXECUTION_ENVELOPE_VERSION,
    DurableRefundDestination,
    ExecutionEnvelope,
    ExecutionResult,
    ExecutionOperation,
    LocalExecutionPolicy,
    commitment,
    snapshot_envelope,
)

EXECUTOR_PRODUCER_PROFILE_ID = "urn:cognous:profiles:moltbot-safe-executor-producer"
EXECUTOR_PRODUCER_PROFILE_VERSION = "1.0.0"
SUPPORTED_EXECUTION_ENVELOPE_VERSIONS = (EXECUTION_ENVELOPE_VERSION,)


def _rows(path: Path, table: str) -> list[dict[str, Any]]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def _asdict(value: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=False)
    if isinstance(value, dict):
        return copy.deepcopy(value)
    raise TypeError(f"unsupported producer artifact type: {type(value).__name__}")


def _frozen_envelope_dict(frozen: Any) -> dict[str, Any]:
    """Serialize only the already-validated immutable snapshot."""
    op = frozen.operation
    return {
        "version": frozen.version,
        "decision_id": frozen.decision_id,
        "effect_id": frozen.effect_id,
        "operation": {
            **op.canonical_dict(),
        },
        "attempt_id": frozen.requested_attempt_id,
    }


def _validate_retained_bindings(
    frozen: Any,
    result: ExecutionResult,
    *,
    effects: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> None:
    operation_digest = frozen.operation.digest

    for row in effects:
        if row.get("operation_digest") != operation_digest:
            raise ValueError("destination effect operation binding contradicts supplied envelope")
        if row.get("grant_id") != frozen.operation.grant_id:
            raise ValueError("destination effect grant binding contradicts supplied envelope")
        if row.get("target") != frozen.operation.target:
            raise ValueError("destination effect target contradicts supplied envelope")
        if float(row.get("amount")) != float(frozen.operation.amount):
            raise ValueError("destination effect amount contradicts supplied envelope")
        if row.get("unit") != frozen.operation.unit:
            raise ValueError("destination effect unit contradicts supplied envelope")
        try:
            payload = json.loads(row.get("payload_json"))
        except Exception as exc:
            raise ValueError("destination effect payload is malformed") from exc
        if payload != frozen.operation.payload():
            raise ValueError("destination effect payload contradicts supplied envelope")

    for row in attempts:
        if row.get("operation_digest") != operation_digest:
            raise ValueError("destination attempt operation binding contradicts supplied envelope")
        if row.get("decision_id") != frozen.decision_id:
            raise ValueError("destination attempt decision binding contradicts supplied envelope")
        if row.get("effect_id") != frozen.effect_id:
            raise ValueError("destination attempt effect binding contradicts supplied envelope")

    attempt_ids = {str(row["attempt_id"]) for row in attempts if row.get("attempt_id")}
    for event in events:
        if str(event.get("attempt_id")) not in attempt_ids:
            raise ValueError("attempt event lacks a retained bound attempt")

    if result.attempt_id is not None:
        matching = [row for row in attempts if row.get("attempt_id") == result.attempt_id]
        if not matching:
            raise ValueError("execution result attempt_id has no retained bound attempt")
    elif result.attempted and result.status not in {"denied"}:
        raise ValueError("attempted execution result is missing attempt_id")

    if result.status in {"executed", "reconciled", "partial", "observed"} and not effects:
        raise ValueError("execution result requires retained destination effect evidence")

    observation = result.observation if isinstance(result.observation, dict) else {}
    destination_state = observation.get("destination_state")
    if isinstance(destination_state, dict) and destination_state:
        observed_effect = destination_state.get("effect_id")
        if observed_effect is not None and observed_effect != frozen.effect_id:
            raise ValueError("execution observation effect binding contradicts supplied envelope")
        observed_digest = destination_state.get("operation_digest")
        if observed_digest is not None and observed_digest != operation_digest:
            raise ValueError("execution observation operation binding contradicts supplied envelope")
        for field, expected in (
            ("grant_id", frozen.operation.grant_id),
            ("target", frozen.operation.target),
            ("unit", frozen.operation.unit),
        ):
            actual = destination_state.get(field)
            if actual is not None and actual != expected:
                raise ValueError(
                    f"execution observation {field} contradicts supplied envelope"
                )
        actual_amount = destination_state.get("amount")
        if actual_amount is not None and float(actual_amount) != float(frozen.operation.amount):
            raise ValueError("execution observation amount contradicts supplied envelope")
        actual_payload = destination_state.get("payload")
        if actual_payload is not None and actual_payload != frozen.operation.payload():
            raise ValueError("execution observation payload contradicts supplied envelope")


def export_execution_artifacts(
    envelope: ExecutionEnvelope,
    result: ExecutionResult,
    destination: DurableRefundDestination,
    *,
    repository_revision: str | None = None,
    source_asserted_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Export the bounded executor producer contract without granting authority.

    The caller supplies an already-authorized/revalidated execution envelope and
    the exact result returned by the public executor adapter. This function only
    serializes the resulting producer records. It never constructs an authority
    resolver, policy grant, approval, or execution decision.
    """

    frozen = snapshot_envelope(envelope)
    if frozen.version not in SUPPORTED_EXECUTION_ENVELOPE_VERSIONS:
        raise ValueError("unsupported execution envelope version")
    if result.decision_id != frozen.decision_id or result.effect_id != frozen.effect_id:
        raise ValueError("execution result identity contradicts execution envelope")

    effects = [
        row for row in _rows(Path(destination.path), "effects")
        if row.get("effect_id") == frozen.effect_id
    ]
    attempts = [
        row for row in _rows(Path(destination.path), "attempts")
        if row.get("effect_id") == frozen.effect_id
        and row.get("decision_id") == frozen.decision_id
    ]
    attempt_ids = {str(row["attempt_id"]) for row in attempts if row.get("attempt_id")}
    events = [
        row for row in _rows(Path(destination.path), "attempt_events")
        if row.get("attempt_id") in attempt_ids
    ]

    _validate_retained_bindings(
        frozen,
        result,
        effects=effects,
        attempts=attempts,
        events=events,
    )

    observations: list[dict[str, Any]] = []
    if isinstance(result.observation, dict) and result.observation:
        observations.append(copy.deepcopy(result.observation))

    revision = repository_revision if repository_revision is not None else "unavailable"
    if not isinstance(revision, str) or not revision:
        raise ValueError("repository_revision must be a non-empty string when supplied")

    source = copy.deepcopy(source_asserted_provenance or {})
    source.setdefault("repository_revision", revision)
    source.setdefault("producer_profile_id", EXECUTOR_PRODUCER_PROFILE_ID)
    source.setdefault("producer_profile_version", EXECUTOR_PRODUCER_PROFILE_VERSION)

    return {
        "producer_profile": {
            "profile_id": EXECUTOR_PRODUCER_PROFILE_ID,
            "profile_version": EXECUTOR_PRODUCER_PROFILE_VERSION,
            "execution_envelope_version": frozen.version,
        },
        "repository": {
            "repository": "cogno-us/moltbot-safe",
            "revision": revision,
            "revision_status": "source_asserted" if revision != "unavailable" else "unavailable",
        },
        "provenance": {
            "source_asserted": source,
            "independently_established": [],
            "meaning": (
                "source_asserted values are emitted by the producer/caller; "
                "this export does not independently establish repository provenance"
            ),
        },
        "execution_envelope": _frozen_envelope_dict(frozen),
        "execution_result": _asdict(result),
        "effects": effects,
        "attempts": attempts,
        "attempt_events": events,
        "observations": observations,
    }


__all__ = [
    "EXECUTOR_PRODUCER_PROFILE_ID",
    "EXECUTOR_PRODUCER_PROFILE_VERSION",
    "SUPPORTED_EXECUTION_ENVELOPE_VERSIONS",
    "PinnedControlPlaneExecutor",
    "DurableRefundDestination",
    "ExecutionEnvelope",
    "ExecutionOperation",
    "ExecutionResult",
    "LocalExecutionPolicy",
    "commitment",
    "export_execution_artifacts",
]
