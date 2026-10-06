from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path
from typing import Any

from .safe_executor import (
    EXECUTION_ENVELOPE_VERSION,
    DurableRefundDestination,
    ExecutionEnvelope,
    ExecutionOperation,
    LocalExecutionPolicy,
    commitment,
)

EXECUTOR_PRODUCER_PROFILE = "urn:cognous:profiles:moltbot-safe-executor-evidence:1.0.0"
EXECUTOR_PRODUCER_PROFILE_VERSION = "1.0.0"
PRODUCER_SCHEMA_VERSION = "1.0.0"


def policy_for_operation(operation: ExecutionOperation) -> LocalExecutionPolicy:
    """Return the narrow local policy used by the synthetic refund adapter.

    This helper creates execution policy only. It does not construct, discover,
    or grant institutional authority.
    """
    return LocalExecutionPolicy(
        allowed_institutions=frozenset({operation.institution_id}),
        allowed_authority_domains=frozenset({operation.authority_domain}),
        allowed_adapters=frozenset({operation.adapter_id}),
        allowed_actions=frozenset({operation.action_id}),
        allowed_target_prefixes=("urn:cognous:synthetic-account:",),
        allowed_units=frozenset({operation.unit}),
        max_amount=1000.0,
        max_effects=1,
    )


def _rows(path: Path, table: str) -> list[dict[str, Any]]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def export_executor_evidence(
    *,
    envelope: ExecutionEnvelope,
    result: Any,
    destination: DurableRefundDestination,
    repository_revision: str,
) -> dict[str, Any]:
    """Export the versioned producer record consumed by downstream evidence tools.

    Provenance in this object is source-asserted. A consumer may independently
    establish repository identity or artifact integrity, but this function does
    not make that claim.
    """
    if envelope.version != EXECUTION_ENVELOPE_VERSION:
        raise ValueError("unsupported execution envelope version")
    if not isinstance(repository_revision, str) or not repository_revision:
        raise ValueError("repository_revision is required")
    effect_id = envelope.effect_id
    decision_id = envelope.decision_id
    result_dict = dataclasses.asdict(result) if dataclasses.is_dataclass(result) else dict(result)
    if result_dict.get("effect_id") != effect_id or result_dict.get("decision_id") != decision_id:
        raise ValueError("execution result identity contradicts envelope")
    attempts = [
        row for row in _rows(Path(destination.path), "attempts")
        if row.get("effect_id") == effect_id and row.get("decision_id") == decision_id
    ]
    attempt_ids = {row["attempt_id"] for row in attempts if row.get("attempt_id")}
    events = [
        row for row in _rows(Path(destination.path), "attempt_events")
        if row.get("attempt_id") in attempt_ids
    ]
    effects = [
        row for row in _rows(Path(destination.path), "effects")
        if row.get("effect_id") == effect_id
    ]
    return {
        "producer_profile": {
            "profile": EXECUTOR_PRODUCER_PROFILE,
            "profile_version": EXECUTOR_PRODUCER_PROFILE_VERSION,
            "schema_version": PRODUCER_SCHEMA_VERSION,
            "execution_envelope_version": EXECUTION_ENVELOPE_VERSION,
            "repository_revision": repository_revision,
            "repository_revision_provenance": "source_asserted",
            "independently_established_provenance": False,
        },
        "execution_envelope": dataclasses.asdict(envelope),
        "execution_result": result_dict,
        "effects": effects,
        "attempts": attempts,
        "attempt_events": events,
        "observation": result_dict.get("observation"),
        "bindings": {
            "decision_id": decision_id,
            "effect_id": effect_id,
            "operation_digest": commitment(dataclasses.asdict(envelope.operation)),
        },
    }
