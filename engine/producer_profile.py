from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path
from typing import Any

from .safe_executor import EXECUTION_ENVELOPE_VERSION, ExecutionEnvelope, ExecutionResult

EXECUTOR_PRODUCER_PROFILE = "cognous.moltbot-safe.executor"
EXECUTOR_PRODUCER_PROFILE_VERSION = "1.0.0"


class ProducerContractError(ValueError):
    """Executor output violates the versioned producer contract."""


def _rows(path: Path, table: str) -> list[dict[str, Any]]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def _asdict(value: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return dict(value)
    raise ProducerContractError("producer value is not serializable by the supported contract")


def export_execution_producer_record(
    *,
    envelope: ExecutionEnvelope,
    result: ExecutionResult,
    destination: Any,
    repository_revision: str,
    provenance_state: str = "source_asserted",
) -> dict[str, Any]:
    """Export one effect-scoped executor record.

    This function serializes already-authorized execution output. It does not
    construct authority, make an authorization decision, or execute an effect.
    """
    if envelope.version != EXECUTION_ENVELOPE_VERSION:
        raise ProducerContractError("unsupported execution envelope version")
    if not isinstance(repository_revision, str) or not repository_revision:
        raise ProducerContractError("repository_revision must be supplied")
    if provenance_state not in {"source_asserted", "independently_established"}:
        raise ProducerContractError("unsupported provenance_state")

    path = Path(destination.path)
    envelope_dict = _asdict(envelope)
    result_dict = _asdict(result)
    decision_id = envelope.decision_id
    effect_id = envelope.effect_id

    if result_dict.get("decision_id") != decision_id or result_dict.get("effect_id") != effect_id:
        raise ProducerContractError("execution result identity conflicts with envelope")

    effects = [row for row in _rows(path, "effects") if row.get("effect_id") == effect_id]
    attempts = [
        row for row in _rows(path, "attempts")
        if row.get("effect_id") == effect_id and row.get("decision_id") == decision_id
    ]
    attempt_ids = {row.get("attempt_id") for row in attempts if row.get("attempt_id")}
    events = [
        row for row in _rows(path, "attempt_events")
        if row.get("attempt_id") in attempt_ids
    ]

    for row in effects:
        if row.get("effect_id") != effect_id:
            raise ProducerContractError("effect identity conflict")
    for row in attempts:
        if row.get("effect_id") != effect_id or row.get("decision_id") != decision_id:
            raise ProducerContractError("attempt binding conflict")
    for row in events:
        if row.get("attempt_id") not in attempt_ids:
            raise ProducerContractError("attempt event has no retained attempt")

    observation = result_dict.get("observation")
    if isinstance(observation, dict) and observation:
        if observation.get("effect_id") != effect_id:
            raise ProducerContractError("observation effect identity conflict")

    return {
        "producer_profile": EXECUTOR_PRODUCER_PROFILE,
        "producer_profile_version": EXECUTOR_PRODUCER_PROFILE_VERSION,
        "repository": "cogno-us/moltbot-safe",
        "repository_revision": repository_revision,
        "provenance": {
            "source_asserted": True,
            "independently_established": provenance_state == "independently_established",
            "state": provenance_state,
        },
        "execution_envelope": envelope_dict,
        "execution_result": result_dict,
        "effects": effects,
        "attempts": attempts,
        "attempt_events": events,
        "observation": observation,
    }


def validate_execution_producer_record(record: dict[str, Any]) -> None:
    if record.get("producer_profile") != EXECUTOR_PRODUCER_PROFILE:
        raise ProducerContractError("unsupported executor producer profile")
    if record.get("producer_profile_version") != EXECUTOR_PRODUCER_PROFILE_VERSION:
        raise ProducerContractError("unsupported executor producer profile version")
    if not record.get("repository_revision"):
        raise ProducerContractError("executor repository revision is required")
    envelope = record.get("execution_envelope") or {}
    result = record.get("execution_result") or {}
    if envelope.get("version") != EXECUTION_ENVELOPE_VERSION:
        raise ProducerContractError("unsupported execution envelope version")
    if envelope.get("decision_id") != result.get("decision_id"):
        raise ProducerContractError("decision identity conflict")
    if envelope.get("effect_id") != result.get("effect_id"):
        raise ProducerContractError("effect identity conflict")
    effect_id = envelope.get("effect_id")
    decision_id = envelope.get("decision_id")
    for row in record.get("effects") or []:
        if row.get("effect_id") != effect_id:
            raise ProducerContractError("effect identity conflict")
    attempts = record.get("attempts") or []
    attempt_ids = set()
    for row in attempts:
        if row.get("effect_id") != effect_id or row.get("decision_id") != decision_id:
            raise ProducerContractError("attempt binding conflict")
        if row.get("attempt_id"):
            attempt_ids.add(row["attempt_id"])
    for row in record.get("attempt_events") or []:
        if row.get("attempt_id") not in attempt_ids:
            raise ProducerContractError("attempt event has no retained attempt")
