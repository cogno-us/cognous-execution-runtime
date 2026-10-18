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
    exact_amount_equal,
    snapshot_envelope,
)

EXECUTOR_PRODUCER_PROFILE_ID = "urn:cognous:profiles:moltbot-safe-executor-producer"
EXECUTOR_PRODUCER_PROFILE_VERSION = "2.0.0"
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


_OBSERVED_STATES = frozenset({"applied", "partial", "absent", "unknown"})
# Exhaustive status matrix for a result handed to the producer export. Each row lists what a status may carry; the
# export refuses a result outside it instead of serialising a contradictory record (a result labelled executed that
# claims nothing was attempted, a duplicate-free claim on an unacknowledged attempt, and so on).
_ATTEMPT_REQUIRED_STATUSES = frozenset({"failed", "partial", "unknown", "executed", "reconciled"})


def _validate_result_state(result: ExecutionResult) -> None:
    """Refuse an execution result whose status, flags and observation contradict one another."""
    status = result.status
    if status not in {"denied", "failed", "partial", "unknown", "executed", "observed", "reconciled"}:
        raise ValueError("execution result status is not recognised")
    for name in ("attempted", "acknowledged", "newly_executed"):
        if type(getattr(result, name)) is not bool:
            raise ValueError(f"execution result {name} must be a boolean")
    if result.observed_state not in _OBSERVED_STATES:
        raise ValueError("execution result observed_state is not recognised")
    if result.observation is not None and not isinstance(result.observation, dict):
        raise ValueError("execution result observation must be an object when present")
    if status in _ATTEMPT_REQUIRED_STATUSES and not result.attempted:
        raise ValueError("execution result status requires an attempted execution")
    if status == "observed" and (result.attempted or result.acknowledged or result.newly_executed
                                 or result.attempt_id is not None):
        raise ValueError("an observed result cannot claim an attempt, acknowledgement or fresh execution")
    if result.acknowledged and not result.attempted:
        raise ValueError("an acknowledgement requires an attempted execution")
    if result.newly_executed and not (result.acknowledged and status in {"executed", "partial", "unknown"}):
        raise ValueError("a fresh execution must be acknowledged and have status executed, partial or unknown")
    if status in {"denied", "failed"} and (result.acknowledged or result.newly_executed
                                           or result.observed_state != "unknown"):
        raise ValueError("a denied or failed result cannot carry an acknowledgement or an observed effect")
    if status == "executed" and not (
        result.acknowledged and result.newly_executed and result.observed_state == "applied"
    ):
        raise ValueError("an executed result must be acknowledged, fresh and observed as applied")
    if status == "reconciled" and (result.newly_executed or result.observed_state != "applied"):
        raise ValueError("a reconciled result is observed as applied and is not a fresh execution")
    if status == "partial" and result.observed_state != "partial":
        raise ValueError("a partial result must be observed as partial")
    if status in {"executed", "reconciled", "partial", "observed"} and not isinstance(result.observation, dict):
        raise ValueError("this result status requires a retained observation")
    if isinstance(result.observation, dict) and result.observation.get("state") is not None \
            and result.observation.get("state") != result.observed_state:
        raise ValueError("execution observation state contradicts execution result")


def _validate_retained_bindings(
    frozen: Any,
    result: ExecutionResult,
    *,
    effects: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:

    operation_digest = frozen.operation.digest

    for row in effects:
        if row.get("operation_digest") != operation_digest:
            raise ValueError("destination effect operation binding contradicts supplied envelope")
        if row.get("grant_id") != frozen.operation.grant_id:
            raise ValueError("destination effect grant binding contradicts supplied envelope")
        if row.get("target") != frozen.operation.target:
            raise ValueError("destination effect target contradicts supplied envelope")
        if not exact_amount_equal(row.get("amount"), frozen.operation.amount):
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

    observation = result.observation if isinstance(result.observation, dict) else {}
    control_plane_evidence = result.control_plane_evidence.get("attempt") or observation.get("control_plane_attempt_evidence")
    control_plane_attempts: list[dict[str, Any]] = []
    if control_plane_evidence:
        if (control_plane_evidence.get("decision_id") != frozen.decision_id or
                control_plane_evidence.get("effect_id") != frozen.effect_id):
            raise ValueError("Control Plane attempt evidence binding mismatch")
        control_plane_attempts.append(copy.deepcopy(control_plane_evidence))
    attempt_identity: dict[str, Any] | None = None

    if result.attempt_id is not None:
        matching = [row for row in attempts if row.get("attempt_id") == result.attempt_id]
        if matching:
            attempt_identity = {
                "namespace": "executor",
                "attempt_id": result.attempt_id,
                "owner": "cogno-us/moltbot-safe",
            }
        else:
            if not isinstance(control_plane_evidence, dict):
                raise ValueError(
                    "execution result attempt_id has no retained bound attempt evidence"
                )
            if control_plane_evidence.get("attempt_id") != result.attempt_id:
                raise ValueError("Control Plane attempt evidence identity mismatch")
            if control_plane_evidence.get("decision_id") != frozen.decision_id:
                raise ValueError("Control Plane attempt evidence decision binding mismatch")
            if control_plane_evidence.get("effect_id") != frozen.effect_id:
                raise ValueError("Control Plane attempt evidence effect binding mismatch")
            if control_plane_evidence not in control_plane_attempts:
                control_plane_attempts.append(copy.deepcopy(control_plane_evidence))
            attempt_identity = {
                "namespace": "control_plane",
                "attempt_id": result.attempt_id,
                "owner": "cogno-us/cognous-agent-control-plane",
            }
    elif result.attempted and result.status not in {"denied", "observed"}:
        raise ValueError("attempted execution result is missing attempt_id")

    reconciliation = result.control_plane_evidence.get("reconciliation")
    if reconciliation:
        if reconciliation.get("effect_id") != frozen.effect_id:
            raise ValueError("Control Plane reconciliation effect binding mismatch")
        if reconciliation.get("retry_eligible") is not False:
            raise ValueError("current Control Plane evidence must not confer retry permission")
        accepted = reconciliation.get("observation_accepted") is True
        if observation and (not accepted or reconciliation.get("observation") != observation):
            raise ValueError("rejected or substituted observation cannot enter accepted claims")
        if not observation and accepted and result.status != "denied":
            raise ValueError("accepted Control Plane observation missing from result")
        if not accepted and result.observed_state != "unknown":
            raise ValueError("rejected observation cannot establish observed state")

    observed_state = result.observed_state
    if result.status in {"executed", "reconciled", "partial"} and observed_state in {
        "applied", "partial"
    } and not effects:
        raise ValueError("execution result requires retained destination effect evidence")
    if result.status == "observed" and observed_state in {"applied", "partial"} and not effects:
        raise ValueError("historical observation requires retained destination effect evidence")
    if result.status == "observed" and observed_state in {"absent", "unknown"} and effects:
        raise ValueError("absence/unknown observation contradicts retained destination effect evidence")

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
        if actual_amount is not None and not exact_amount_equal(actual_amount, frozen.operation.amount):
            raise ValueError("execution observation amount contradicts supplied envelope")
        actual_payload = destination_state.get("payload")
        if actual_payload is not None and actual_payload != frozen.operation.payload():
            raise ValueError("execution observation payload contradicts supplied envelope")

    observed_effect = observation.get("effect_id")
    if observed_effect is not None and observed_effect != frozen.effect_id:
        raise ValueError("execution observation effect identity contradicts supplied envelope")
    observed_state_value = observation.get("state")
    if observed_state_value is not None and observed_state_value != result.observed_state:
        raise ValueError("execution observation state contradicts execution result")
    if result.status == "observed" and result.observed_state in {"absent", "unknown"}:
        if isinstance(destination_state, dict) and destination_state:
            raise ValueError(
                "absence/unknown observation must not fabricate destination state"
            )

    return attempt_identity, control_plane_attempts


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
    _validate_result_state(result)

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

    attempt_identity, control_plane_attempts = _validate_retained_bindings(
        frozen,
        result,
        effects=effects,
        attempts=attempts,
        events=events,
    )

    # Layout (lifecycle contract, export layout 2026-10-17): `observations` carries validated
    # Control Plane observations only (they carry `state`). A typed recovery report (`status`,
    # no `state`) goes in the optional `recovery_observations`, emitted only when one exists;
    # `execution_result.observation` still carries the report as before.
    observations: list[dict[str, Any]] = []
    recovery_observations: list[dict[str, Any]] = []
    if isinstance(result.observation, dict) and result.observation:
        if "state" in result.observation:
            observations.append(copy.deepcopy(result.observation))
        else:
            recovery_observations.append(copy.deepcopy(result.observation))

    revision = repository_revision if repository_revision is not None else "unavailable"
    if not isinstance(revision, str) or not revision:
        raise ValueError("repository_revision must be a non-empty string when supplied")

    source = copy.deepcopy(source_asserted_provenance or {})
    source.setdefault("repository_revision", revision)
    source.setdefault("producer_profile_id", EXECUTOR_PRODUCER_PROFILE_ID)
    source.setdefault("producer_profile_version", EXECUTOR_PRODUCER_PROFILE_VERSION)

    exported = {
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
        "attempt_identity": attempt_identity,
        "control_plane_attempts": control_plane_attempts,
        "observations": observations,
        "control_plane_evidence": copy.deepcopy(result.control_plane_evidence),
        "rejected_observations": [copy.deepcopy(rec["observation"])]
            if (rec := result.control_plane_evidence.get("reconciliation", {}))
            and not rec.get("observation_accepted") and rec.get("observation") else [],
    }
    if recovery_observations:
        exported["recovery_observations"] = recovery_observations
    return exported


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
