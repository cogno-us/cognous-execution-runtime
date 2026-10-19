"""Reference validator for the execution-attempt lifecycle contract (0.1.0-draft).

Stdlib only. This is a spike: it states the contract executably so each side of the boundary can be
tested alone against golden fixtures. It does not import or modify ``odes``.

Two layers:

1. Structure: a minimal interpreter for the JSON Schema subset used by ``schema.json`` (so the schema is
   the single source of truth for members, types and the closed-member-set rule).
2. Lifecycle rules: the cross-record rules in README.md.

``validate`` returns a list of ``Violation`` (empty list means conformant). Refusal codes are stable
strings; a fixture states which code it expects.
"""
from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schema.json"
CONTRACT = "execution-attempt-lifecycle"
CONTRACT_VERSION = "0.1.0-draft"


@dataclass(frozen=True)
class Violation:
    code: str
    path: str
    message: str


# --------------------------------------------------------------------------- input hardening

class InputRefused(ValueError):
    """The input file is not a strict JSON document (duplicate member, non-finite number, bad encoding)."""


_TIME_SHAPE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])")


def _pattern_ok(pattern: str, value: str) -> bool:
    """A schema pattern matches the whole string: ``^`` and ``$`` are anchors, never "before a trailing newline"."""
    body = pattern[1:] if pattern.startswith("^") else pattern
    if body.endswith("$") and not body.endswith("\\$"):
        body = body[:-1]
    return re.fullmatch(body, value, re.ASCII) is not None


def _printable(text: str) -> str:
    return text.encode("ascii", "backslashreplace").decode("ascii")


def _scan_input(value: Any, root: str = "$") -> list[Violation]:
    """Refuse non-finite numbers and text that is not valid Unicode before any rule reads the document."""
    out: list[Violation] = []
    todo = [(value, root)]
    while todo:
        item, path = todo.pop()
        if isinstance(item, float) and not math.isfinite(item):
            out.append(Violation("schema_violation", path, "non-finite numbers (NaN, Infinity) are refused"))
        elif isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                out.append(Violation("schema_violation", path, "text is not valid Unicode (unpaired surrogate)"))
        elif isinstance(item, dict):
            for key, member in reversed(list(item.items())):
                if isinstance(key, str):
                    try:
                        key.encode("utf-8")
                    except UnicodeEncodeError:
                        out.append(Violation("schema_violation", f"{path}.{_printable(key)}",
                                             "member name is not valid Unicode (unpaired surrogate)"))
                todo.append((member, f"{path}.{_printable(str(key))}"))
        elif isinstance(item, list):
            for i in range(len(item) - 1, -1, -1):
                todo.append((item[i], f"{path}[{i}]"))
    return out


def _guarded(fn):
    """Wrap ``validate`` so a document can only be accepted or refused with typed violations, never raise."""
    def validate(doc: Any, *args: Any, **kwargs: Any) -> list[Violation]:
        found = _scan_input(doc)
        for n, extra in enumerate(list(args) + list(kwargs.values()), 1):
            if isinstance(extra, (dict, list)):
                found.extend(_scan_input(extra, f"$input{n}"))
        if found:
            return found
        try:
            return fn(doc, *args, **kwargs)
        except (UnicodeError, ArithmeticError, ValueError, RecursionError) as exc:
            return [Violation("schema_violation", "$", f"the document could not be evaluated ({type(exc).__name__})")]
    validate.__doc__ = fn.__doc__
    validate.__name__ = "validate"
    return validate


def _refuse_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise InputRefused(f"duplicate member name {_printable(key)!r}")
        seen[key] = value
    return seen


def _refuse_constant(name: str) -> Any:
    raise InputRefused(f"non-finite number {name} is not JSON")


def _parse_float(text: str) -> float:
    number = float(text)
    if not math.isfinite(number):
        raise InputRefused("number outside the finite range")
    return number


def strict_loads(text: str) -> Any:
    """Strict JSON: no duplicate members, no NaN or Infinity (also by overflow), UTF-8 text only."""
    try:
        return json.loads(text, object_pairs_hook=_refuse_duplicates, parse_constant=_refuse_constant,
                          parse_float=_parse_float)
    except RecursionError as exc:
        raise InputRefused("nesting too deep") from exc


def load_strict_json(path: Path) -> Any:
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputRefused("file is not UTF-8") from exc
    try:
        return strict_loads(text)
    except InputRefused:
        raise
    except ValueError as exc:
        raise InputRefused(f"file is not valid JSON ({type(exc).__name__})") from exc


def _after(start: datetime, seconds: Any) -> datetime | None:
    """``start`` plus ``seconds``; None when the sum is outside the representable range."""
    try:
        return start + timedelta(seconds=seconds)
    except (OverflowError, ValueError):
        return None



def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- structure

def _is_type(value: Any, name: str) -> bool:
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    raise ValueError(f"unsupported schema type {name!r}")


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if ref is None:
        return schema
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported $ref {ref!r}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _check(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str, out: list[Violation]) -> None:
    schema = _resolve(schema, root)
    if "const" in schema and not (type(value) is type(schema["const"]) and value == schema["const"]):
        out.append(Violation("schema_violation", path, f"expected constant {schema['const']!r}"))
        return
    if "enum" in schema and not any(value == option and type(value) is type(option) for option in schema["enum"]):
        # Closed enumeration: an unknown value is refused with its own typed code and never coerced. A new
        # value in any enumeration is a major version bump (ODES item 6, decided 2026-10-17).
        out.append(Violation("unknown_enum_value", path, f"value {value!r} not in declared set"))
        return
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, n) for n in names):
            out.append(Violation("schema_violation", path, f"expected type {'|'.join(names)}"))
            return
    if "oneOf" in schema:
        matches = 0
        for option in schema["oneOf"]:
            trial: list[Violation] = []
            _check(value, option, root, path, trial)
            matches += not trial
        if matches != 1:
            # Surface the most specific failure of the non-null branch for readability.
            for option in schema["oneOf"]:
                trial = []
                _check(value, option, root, path, trial)
                if trial and value is not None:
                    out.extend(trial)
                    return
            out.append(Violation("schema_violation", path, "does not match exactly one alternative"))
        return
    if "anyOf" in schema:
        if not any(not _sub(value, option, root, path) for option in schema["anyOf"]):
            out.append(Violation("schema_violation", path, "does not satisfy any required member group"))
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append(Violation("schema_violation", path, "string shorter than minimum length"))
        if "pattern" in schema and not _pattern_ok(schema["pattern"], value):
            out.append(Violation("schema_violation", path, f"string does not match {schema['pattern']}"))
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            _check(item, schema["items"], root, f"{path}[{i}]", out)
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                out.append(Violation("schema_violation", f"{path}.{name}", "required member missing"))
        extra = schema.get("additionalProperties", True)
        for name, member in value.items():
            if name in props:
                _check(member, props[name], root, f"{path}.{name}", out)
            elif extra is False:
                out.append(Violation("undeclared_member", f"{path}.{name}",
                                     "member is neither declared nor inside the extensions namespace"))
            elif isinstance(extra, dict):
                _check(member, extra, root, f"{path}.{name}", out)
        names_schema = schema.get("propertyNames")
        if names_schema is not None:
            for name in value:
                if _sub(name, names_schema, root, path):
                    out.append(Violation("invalid_extension_key", f"{path}.{name}",
                                         "extension member names must match the reserved x- form"))


def _sub(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str) -> list[Violation]:
    out: list[Violation] = []
    _check(value, schema, root, path, out)
    return out


def validate_structure(doc: Any) -> list[Violation]:
    schema = load_schema()
    out: list[Violation] = []
    if isinstance(doc, dict) and isinstance(doc.get("contract_version"), str) \
            and doc["contract_version"] != CONTRACT_VERSION and doc.get("contract") == CONTRACT:
        return [Violation("unsupported_contract_version", "$.contract_version",
                          f"this validator implements {CONTRACT_VERSION} only")]
    _check(doc, schema, schema, "$", out)
    return out


# --------------------------------------------------------------------------- lifecycle rules

def observation_kind(obs: Any) -> str:
    """Classify a result observation by shape: 'none', 'validated' (has `state`) or 'recovery' (has `status`)."""
    if obs is None:
        return "none"
    return "validated" if "state" in obs else "recovery"


def _recovery_rules(obs: dict[str, Any], result: dict[str, Any]) -> list[Violation]:
    """Rules for a typed recovery observation (route B ``reconcile_claim`` report or its unavailable placeholder)."""
    path = "$.execution_result.observation"
    out: list[Violation] = []
    if obs["retry_eligible"] is not False:
        out.append(Violation("recovery_retry_eligible", path + ".retry_eligible",
                             "a recovery report never makes an effect retry-eligible; retry_eligible must be false"))
    status = obs["status"]
    if status in {"applied", "partial"}:
        missing = [f for f in ("claim_state", "effect_id", "operation_digest") if f not in obs]
        if missing:
            out.append(Violation("recovery_report_incomplete", path,
                                 f"an '{status}' recovery report must carry " + ", ".join(missing)))
    elif not obs.get("reason"):
        out.append(Violation("recovery_report_incomplete", path,
                             f"a '{status}' recovery report must carry a non-empty reason"))
    if result["status"] == "observed" and not out:
        if any(result.get(k) for k in ("attempted", "acknowledged", "newly_executed")) or result["attempt_id"] is not None:
            out.append(Violation("effect_free_reconcile_claims_attempt", "$.execution_result",
                                 "an effect-free reconcile makes no attempt: attempt_id null and attempted, "
                                 "acknowledged and newly_executed false"))
        elif result["observed_state"] != (status if status in {"applied", "partial"} else "unknown"):
            out.append(Violation("recovery_state_mismatch", "$.execution_result.observed_state",
                                 "observed_state equals the recovery status when it is applied/partial, else 'unknown'"))
    return out


def _executor_rules(doc: dict[str, Any]) -> list[Violation]:
    out: list[Violation] = []
    rows = doc.get("attempts")
    if rows is None:
        return out
    events = doc.get("attempt_events", [])
    ids = [r["attempt_id"] for r in rows]
    if len(set(ids)) != len(ids):
        out.append(Violation("duplicate_attempt_row", "$.attempts", "attempt rows are unique per attempt_id"))
    last_id = None
    last_time = None
    per_attempt: dict[str, list[dict[str, Any]]] = {}
    for i, ev in enumerate(events):
        if last_id is not None and ev["event_id"] <= last_id:
            out.append(Violation("event_order", f"$.attempt_events[{i}].event_id",
                                 "event_id must strictly increase across the event sequence"))
        last_id = ev["event_id"]
        if last_time is not None and ev["created_at"] < last_time:
            out.append(Violation("event_order", f"$.attempt_events[{i}].created_at",
                                 "created_at must not decrease across the event sequence"))
        last_time = ev["created_at"]
        per_attempt.setdefault(ev["attempt_id"], []).append(ev)
    for aid in ids:
        seq = per_attempt.get(aid)
        if not seq:
            out.append(Violation("attempt_without_events", "$.attempt_events", f"attempt {aid} has no events"))
            continue
        if seq[0]["status"] != "attempted":
            out.append(Violation("missing_start_event", "$.attempt_events",
                                 f"attempt {aid} events must start with 'attempted'"))
        if any(e["status"] == "attempted" for e in seq[1:]):
            out.append(Violation("repeated_start_event", "$.attempt_events",
                                 f"attempt {aid} repeats its 'attempted' event"))
        if seq[0]["status"] == "attempted":
            for prev, nxt in zip(seq, seq[1:]):
                if nxt["status"] == "attempted":
                    continue
                if nxt["status"] not in EXECUTOR_TRANSITIONS.get(prev["status"], frozenset()):
                    out.append(Violation("attempt_transition", "$.attempt_events",
                                         f"attempt {aid}: event {nxt['status']!r} may not follow {prev['status']!r}"))
                    break
    result = doc.get("execution_result")
    if result is None:
        return out
    obs = result["observation"]
    kind = observation_kind(obs)
    listed = doc.get("recovery_observations")
    if listed is not None and listed != ([obs] if kind == "recovery" else []):
        out.append(Violation("recovery_observations_mismatch", "$.recovery_observations",
                             "recovery_observations equals the recovery report carried by execution_result.observation, "
                             "or is empty when the result carries none"))
        return out
    if doc.get("observations") is not None and doc["observations"] != ([obs] if kind == "validated" else []):
        out.append(Violation("observations_mismatch", "$.observations",
                             "observations lists the validated observation the result carries, or is empty when it carries none"))
        return out
    if kind == "recovery" and result["status"] not in {"unknown", "observed"}:
        out.append(Violation("recovery_observation_status", "$.execution_result.observation",
                             "a recovery observation is only carried by results 'unknown' (post-commit recovery) "
                             "or 'observed' (effect-free reconcile)"))
        return out
    if result["status"] == "observed" and kind != "recovery" and (
            any(result.get(k) for k in ("attempted", "acknowledged", "newly_executed"))
            or result["attempt_id"] is not None):
        return out + [Violation("observed_result_claims_attempt", "$.execution_result",
                                "result 'observed' is an effect-free observation: attempt_id null and attempted, "
                                "acknowledged and newly_executed false")]
    if result["status"] == "executed" and obs is None:
        return out + [Violation("executed_without_observation", "$.execution_result.observation",
                                "result 'executed' carries the observation that established it; null is refused")]
    if kind == "recovery":
        recovery_violations = _recovery_rules(obs, result)
        if recovery_violations:
            return out + recovery_violations
    if kind == "none" and result["observed_state"] != "unknown":
        # Owner decision 2026-10-17 (strict): with no observation nothing was established, whatever the status.
        return out + [Violation("null_observation_state_mismatch", "$.execution_result.observed_state",
                                "observation null requires observed_state 'unknown' for every result status "
                                f"(got {result['observed_state']!r})")]
    if result["status"] == "unknown":
        if kind == "validated":
            if obs["state"] not in {"absent", "unknown"}:
                out.append(Violation("unknown_with_validated_observation", "$.execution_result.observation",
                                     f"status 'unknown' may carry a validated observation only when its state is "
                                     f"'absent' or 'unknown' (the follow-up reading after an ambiguous dispatch); "
                                     f"a validated {obs['state']!r} observation is a 'reconciled' or 'partial' outcome"))
                return out
            expected = obs["state"]
        else:
            expected = "unknown"
            if kind == "recovery" and obs["status"] in {"applied", "partial"}:
                expected = obs["status"]
        if kind != "none" and result["observed_state"] != expected:
            what = {"validated": "a validated observation", "recovery": "a recovery observation"}[kind]
            out.append(Violation("unknown_state_mismatch", "$.execution_result.observed_state",
                                 f"status 'unknown' with {what} requires observed_state {expected!r}"
                                 + ("" if kind == "validated" else
                                    "; 'applied'/'partial' is only what recovery reports, and 'unknown' otherwise")))
            return out
    if result["status"] in ATTEMPT_REQUIRED_STATUSES and result["attempt_id"] not in ids:
        # Rule ``result_attempt_unresolved``: a status that claims an attempt names a row of ``attempts`` (null names none).
        return out + [Violation("result_attempt_unresolved", "$.execution_result.attempt_id",
                                f"result status {result['status']!r} names an attempt row of the record "
                                "(a null attempt_id or an id that matches no row is refused)")]
    seq = per_attempt.get(result["attempt_id"])
    if result["attempt_id"] in ids and seq and result["status"] != "observed":
        terminal = seq[-1]["status"]
        if terminal == "attempted":
            out.append(Violation("terminal_event_missing", "$.attempt_events",
                                 "the attempt named by the result has no terminal event"))
        elif terminal != result["status"] and not (terminal == "executed" and result["status"] == "unknown"):
            out.append(Violation("status_terminal_mismatch", "$.execution_result.status",
                                 f"result status {result['status']!r} contradicts terminal event {terminal!r}"))
    return out


def _control_plane_rules(doc: dict[str, Any]) -> list[Violation]:
    out: list[Violation] = []
    rows = doc.get("control_plane_attempts")
    if rows is None:
        return out
    lifecycle: dict[str, list[dict[str, Any]]] = {}
    for i, row in enumerate(rows):
        marker = row["acknowledgement"].get("reconciled_existing")
        if "reconciled_existing" in row["acknowledgement"] and marker is not True:
            out.append(Violation("invalid_reconciled_marker", f"$.control_plane_attempts[{i}].acknowledgement",
                                 "reconciled_existing, when present, must be true"))
        lifecycle.setdefault(row["attempt_id"], []).append(row)
    for aid, seq in lifecycle.items():
        reconciled_only = (len(seq) == 1 and seq[0]["status"] in {"acknowledged", "partial"}
                           and seq[0]["acknowledgement"] == {"reconciled_existing": True})
        if any(r["acknowledgement"].get("reconciled_existing") is True for r in seq) and not reconciled_only:
            out.append(Violation("invalid_reconciled_marker", "$.control_plane_attempts",
                                 f"attempt {aid}: reconciled_existing is only valid on a single acknowledged/partial row"))
        elif seq[0]["status"] != "attempted" and not reconciled_only:
            out.append(Violation("missing_start_record", "$.control_plane_attempts",
                                 f"attempt {aid} lifecycle lacks its 'attempted' start record"))
        if len({r["started_at"] for r in seq}) > 1:
            out.append(Violation("attempt_started_at_changed", "$.control_plane_attempts",
                                 f"attempt {aid}: started_at is fixed when the attempt starts and identical on every row"))
        if len({(r["effect_id"], r["decision_id"]) for r in seq}) > 1:
            out.append(Violation("attempt_identity_changed", "$.control_plane_attempts",
                                 f"attempt {aid}: effect_id and decision_id are fixed by the first row and identical on every row"))
        if not reconciled_only:
            for prev, nxt in zip(seq, seq[1:]):
                if prev == nxt:
                    continue
                if nxt["status"] not in CONTROL_PLANE_TRANSITIONS.get(prev["status"], frozenset()):
                    out.append(Violation("attempt_transition", "$.control_plane_attempts",
                                         f"attempt {aid}: row status {nxt['status']!r} may not follow {prev['status']!r}"))
                    break
        if any(seq[i] == seq[j] for i in range(len(seq)) for j in range(i)):
            out.append(Violation("repeated_record", "$.control_plane_attempts",
                                 f"attempt {aid} lifecycle repeats a record"))
    return out


# Allowed successors of each status (decision 37; decision 39-P adds executed -> unknown and partial -> unknown: the Control Plane
# adapter appends an unknown row after an executed or partial one when the follow-up observation is not accepted). Statuses with no successor are terminal and closed: a
# closed attempt is never reopened and never receives a later, contradicting record.
EXECUTOR_TRANSITIONS: dict[str, frozenset[str]] = {
    "attempted": frozenset({"executed", "partial", "failed", "unknown", "denied", "reconciled"}),
    "unknown": frozenset({"executed", "partial", "failed", "reconciled"}),
    "executed": frozenset({"unknown", "reconciled"}),
    "partial": frozenset({"unknown", "reconciled"}),
    "failed": frozenset(),
    "denied": frozenset(),
    "reconciled": frozenset(),
}
CONTROL_PLANE_TRANSITIONS: dict[str, frozenset[str]] = {
    "attempted": frozenset({"acknowledged", "unknown", "failed", "partial"}),
    "unknown": frozenset({"acknowledged", "failed", "partial"}),
    "acknowledged": frozenset(),
    "partial": frozenset(),
    "failed": frozenset(),
}


# Rule ``result_state_matrix`` (decision 39-K). The execution result's status, flags and observation must agree. This mirrors the
# runtime producer's export check (``engine/producer_contract.py`` ``_validate_result_state`` at 62a5cc15), row for row, and is
# the exhaustive status x flags x observation matrix:
#   - a failed, partial, unknown, executed or reconciled result requires ``attempted``;
#   - an acknowledgement requires ``attempted``; a fresh execution (``newly_executed``) is acknowledged and executed, partial or unknown;
#   - denied and failed carry no acknowledgement, no fresh execution and ``observed_state`` unknown;
#   - executed is acknowledged, fresh and observed ``applied``; reconciled is not fresh and observed ``applied``;
#     partial is observed ``partial``;
#   - executed, reconciled, partial and observed require a retained observation object;
#   - a VALIDATED observation (it has ``state``) carries the result's ``observed_state``; a route-B recovery report (it has
#     ``status``) is told apart and is held to the recovery rules instead (``_recovery_rules``), never to this row;
#   - ``observed_state`` is one of applied, partial, absent, unknown.
# The observed status (effect-free) and its attempt-free shape stay with their own rules (``observed_result_claims_attempt``).
OBSERVED_STATES = frozenset({"applied", "partial", "absent", "unknown"})
ATTEMPT_REQUIRED_STATUSES = frozenset({"failed", "partial", "unknown", "executed", "reconciled"})


def result_matrix_problems(result: dict[str, Any]) -> list[str]:
    """What the producer's status matrix refuses about ``result``; empty when it conforms."""
    problems: list[str] = []
    status = result["status"]
    present = {name: result[name] for name in ("attempted", "acknowledged", "newly_executed") if name in result}
    if any(type(value) is not bool for value in present.values()):
        return ["attempted, acknowledged and newly_executed are carried as booleans"]
    if len(present) != 3:  # the flags are optional members; absent flags are not claimed, so the flag rows have nothing to read
        present = {}
    attempted, acknowledged, fresh = (present.get(n) for n in ("attempted", "acknowledged", "newly_executed"))
    observed_state, obs = result["observed_state"], result["observation"]
    if observed_state not in OBSERVED_STATES:
        problems.append("observed_state is one of applied, partial, absent, unknown")
    if status in ATTEMPT_REQUIRED_STATUSES and attempted is False:
        problems.append(f"status {status!r} requires an attempted execution")
    if acknowledged and attempted is False:
        problems.append("an acknowledgement requires an attempted execution")
    if fresh and not (acknowledged is True and status in {"executed", "partial", "unknown"}):
        problems.append("a fresh execution is acknowledged and has status executed, partial or unknown")
    if status in {"denied", "failed"} and (acknowledged or fresh or observed_state != "unknown"):
        problems.append("a denied or failed result carries no acknowledgement, no fresh execution and observed_state unknown")
    if status == "executed" and (acknowledged is False or fresh is False or observed_state != "applied"):
        problems.append("an executed result is acknowledged, fresh and observed as applied")
    if status == "reconciled" and (fresh or observed_state != "applied"):
        problems.append("a reconciled result is observed as applied and is not a fresh execution")
    if status == "partial" and observed_state != "partial":
        problems.append("a partial result is observed as partial")
    if status in {"executed", "reconciled", "partial", "observed"} and not isinstance(obs, dict):
        problems.append(f"status {status!r} requires a retained observation")
    if observation_kind(obs) == "validated" and obs["state"] != observed_state:
        problems.append("a validated observation's state is the result's observed_state")
    return problems


def _result_matrix_rules(doc: dict[str, Any]) -> list[Violation]:
    result = doc.get("execution_result")
    if result is None:
        return []
    problems = result_matrix_problems(result)
    if not problems:
        return []
    return [Violation("result_state_matrix", "$.execution_result", "; ".join(problems))]


def _result_binding_rules(doc: dict[str, Any]) -> list[Violation]:
    """The execution result and its observation name the effect and decision of the attempt they report."""
    result = doc.get("execution_result")
    if result is None:
        return []
    out: list[Violation] = []
    attempt_id = result.get("attempt_id")
    identities: dict[str, tuple[str, str]] = {}
    for row in doc.get("attempts") or []:
        identities.setdefault(row["attempt_id"], (row["effect_id"], row["decision_id"]))
    for row in doc.get("control_plane_attempts") or []:
        identities.setdefault(row["attempt_id"], (row["effect_id"], row["decision_id"]))
    expected = identities.get(attempt_id) if attempt_id is not None else None
    obs = result.get("observation")
    if expected is not None:
        effect_id, decision_id = expected
        if "effect_id" in result and result["effect_id"] != effect_id:
            out.append(Violation("result_binding_mismatch", "$.execution_result.effect_id",
                                 "the result names a different effect than the attempt it reports"))
        if "decision_id" in result and result["decision_id"] != decision_id:
            out.append(Violation("result_binding_mismatch", "$.execution_result.decision_id",
                                 "the result names a different decision than the attempt it reports"))
        if isinstance(obs, dict) and "effect_id" in obs and obs["effect_id"] != effect_id:
            out.append(Violation("result_binding_mismatch", "$.execution_result.observation.effect_id",
                                 "the observation names a different effect than the attempt it reports"))
    elif isinstance(obs, dict) and "effect_id" in obs and "effect_id" in result and obs["effect_id"] != result["effect_id"]:
        out.append(Violation("result_binding_mismatch", "$.execution_result.observation.effect_id",
                             "the observation names a different effect than the result"))
    return out


# Reason codes the control plane appends to a check row AFTER the journal completion row is written (operation grant, renewal
# and admission gates; merge and dedupe effect-time gates; the per-input time-floor comparison). A check row may carry its
# journal completion's reasons plus codes of this closed set and nothing else (rule ``reasons_match``). Source: the Control
# Plane at 6093aca7, ``bounded.py`` ``_admit_operation``, ``_operation_gate``, ``_effect_gate_n4`` and
# ``record_resolution_check``, and ``merge.py`` ``merge_gate``. A consumer that vendors this set records its digest.
POST_JOURNAL_GATE_CODES = frozenset({
    "operation_grant_mismatch",
    "operation_renewal_unrecorded",
    "operation_not_admitted",
    "operation_merged_effect_exists",
    "operation_merged_effect_unresolved",
    "dedupe_unresolved_attempt_blocks_new_key",
    "dedupe_operation_effect_applied",
    "input_observation_time_regressed",
})


# Rule ``reasons_minimum`` (decision 39-E). A completion's reasons include the minimum set its own recorded input events imply. The
# table is what the Control Plane writer (``bounded.py`` ``_resolve_journal``, ``_read_grant``, ``_read_input``, ``capture`` and
# ``runtime_authority.validate_extensions`` at 6093aca7) always writes for these recorded facts; it is a floor, never a full
# derivation (the required-input list is not recorded, so ``required_input_not_read`` is not derived):
#   an input whose read_state is "unreadable" and that no later read of the same input rescued as "cached_positive"
#       -> status_unreadable
#   an input with read_state "nonce_mismatch"                         -> status_nonce_mismatch
#   an authority_context whose last read is missing, no read_state    -> authority_context_missing
#   a purpose whose last read is missing, no read_state               -> purpose_unreadable
#   any input whose last read is missing and not rescued              -> at least one reason
IMPLIED_REASONS = {
    "unreadable": "status_unreadable",
    "nonce_mismatch": "status_nonce_mismatch",
    "authority_context_missing": "authority_context_missing",
    "purpose_missing": "purpose_unreadable",
}


def implied_reasons(inputs: list[dict[str, Any]]) -> tuple[set[str], bool]:
    """The reasons a completed check must carry given its recorded input events, and whether it must carry at least one."""
    last: dict[tuple[Any, Any], dict[str, Any]] = {}
    states: dict[tuple[Any, Any], set[Any]] = {}
    implied: set[str] = set()
    for x in inputs:
        key = (x.get("input_type"), x.get("input_id"))
        last[key] = x
        states.setdefault(key, set()).add(x.get("read_state"))
        if x.get("read_state") == "nonce_mismatch":
            implied.add(IMPLIED_REASONS["nonce_mismatch"])
    some = False
    for key, x in last.items():
        rescued = "cached_positive" in states[key]
        if "unreadable" in states[key] and not rescued:
            implied.add(IMPLIED_REASONS["unreadable"])
        if x.get("missing") is True and not rescued:
            some = True
            if x.get("read_state") is None and x.get("input_type") == "authority_context":
                implied.add(IMPLIED_REASONS["authority_context_missing"])
            if x.get("read_state") is None and x.get("input_type") == "purpose":
                implied.add(IMPLIED_REASONS["purpose_missing"])
    return implied, some


def _norm(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in x.items() if k not in {"previous_observed_at", "extensions"}} for x in inputs]


def _journal_rules(doc: dict[str, Any]) -> list[Violation]:
    out: list[Violation] = []
    journal = doc.get("resolution_journal")
    if journal is None:
        return out
    checks_rows = doc.get("resolution_checks", [])
    seen_ids: set[str] = set()
    for i, row in enumerate(checks_rows):
        if row["check_id"] in seen_ids:
            out.append(Violation("duplicate_check_id", f"$.resolution_checks[{i}]", "duplicate resolution check id"))
        seen_ids.add(row["check_id"])
    state: dict[str, dict[str, Any]] = {}
    completions: list[tuple[str, list[int]]] = []
    open_at: dict[str, int] = {}
    close_at: dict[str, int] = {}
    for seq, ev in enumerate(journal):
        cid = ev["check_id"]
        if ev["phase"] == "opened":
            if cid in state:
                out.append(Violation("journal_protocol", f"$.resolution_journal[{seq}]", "duplicate journal opening"))
                continue
            state[cid] = {"event": ev, "closed": False, "inputs": []}
            open_at[cid] = seq
            continue
        if cid not in state or state[cid]["closed"]:
            out.append(Violation("journal_protocol", f"$.resolution_journal[{seq}]",
                                 "journal event without an open check (missing opening or already closed)"))
            continue
        opening = state[cid]["event"]
        if any(ev[k] != opening[k] for k in ("owner", "stage", "proposal_commitment", "decision_id", "effect_id", "action_class")):
            out.append(Violation("journal_protocol", f"$.resolution_journal[{seq}]", "journal binding changed after opening"))
            continue
        if ev["phase"] == "input":
            if ev["input"] is None:
                out.append(Violation("journal_protocol", f"$.resolution_journal[{seq}]", "input phase requires an input"))
                continue
            state[cid]["inputs"].append(ev["input"])
            continue
        state[cid]["closed"] = True
        if ev["phase"] == "completed":
            implied, some = implied_reasons(state[cid]["inputs"])
            lacking = implied - set(ev["reasons"])
            if lacking or (some and not ev["reasons"]):
                out.append(Violation("reasons_minimum", f"$.resolution_journal[{seq}]",
                                     "the completion's reasons lack what its own recorded inputs imply"
                                     + (f": {', '.join(sorted(lacking))}" if lacking else ": a missing input requires at least one reason")))
                continue
            # Journal ids and check ids are separate producer ids today; bind by content.
            candidates = [i for i, row in enumerate(checks_rows)
                          if row["stage"] == ev["stage"] and _norm(row["inputs"]) == _norm(state[cid]["inputs"])
                          and set(ev["reasons"]) <= set(row["reasons"])
                          and set(row["reasons"]) - set(ev["reasons"]) <= POST_JOURNAL_GATE_CODES]
            if not candidates:
                out.append(Violation("journal_unbound_completion", f"$.resolution_journal[{seq}]",
                                     "completed journal check matches no resolution check"))
                continue
            completions.append((cid, candidates))
            close_at[cid] = seq
    if out:
        return out
    owner: dict[int, int] = {}

    def assign(j: int, seen: set[int]) -> bool:
        for i in completions[j][1]:
            if i not in seen:
                seen.add(i)
                if i not in owner or assign(owner[i], seen):
                    owner[i] = j
                    return True
        return False

    for j in range(len(completions)):
        if not assign(j, set()):
            return [Violation("journal_check_reused", "$.resolution_journal",
                              "completed journal checks reuse one resolution check")]
    # Resolution checks are appended as they complete. A check that opened after another completed must
    # appear after it; overlapping checks may complete (and be listed) in either order.
    chosen: dict[str, int] = {}
    for cid, candidates in completions:
        previous = max([chosen[o] for o in chosen if close_at[o] < open_at[cid]], default=-1)
        later = [i for i in candidates if i > previous]
        if not later:
            return [Violation("resolution_checks_out_of_order", "$.resolution_checks",
                              "non-overlapping checks are listed out of journal order")]
        chosen[cid] = min(later)
    return out


def _validate(doc: Any) -> list[Violation]:
    """Return all violations; an empty list means the document conforms to 0.1.0-draft."""
    out = validate_structure(doc)
    if out:
        return out
    for rule in (_executor_rules, _control_plane_rules, _result_binding_rules, _journal_rules):
        out.extend(rule(doc))
    if not out:  # the matrix reads a result whose kind, binding and terminal event were already found consistent
        out.extend(_result_matrix_rules(doc))
    return out


# --------------------------------------------------------------------------- extension handling

def canonical_bytes(doc: Any) -> bytes:
    """Digest input: the whole document including every `extensions` member, never a projection."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def known_view(doc: dict[str, Any]) -> dict[str, Any]:
    """What a consumer of this version acts on: declared members only (extensions excluded from logic)."""
    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k != "extensions"}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    return strip(doc)


def preserve(doc: dict[str, Any]) -> dict[str, Any]:
    """Consumers re-emit what they received: ignore-but-preserve means a faithful deep copy."""
    return json.loads(json.dumps(doc))


validate = _guarded(_validate)


def main(argv: list[str]) -> int:
    code = 0
    for name in argv:
        try:
            data = load_strict_json(Path(name))
        except (InputRefused, OSError) as exc:
            print(f"{name}: input_refused $: {_printable(str(exc))}")
            code |= 2
            continue
        wrapped = isinstance(data, dict) and "document" in data and "fixture_id" in data
        violations = validate(data["document"] if wrapped else data)
        for v in violations:
            print(f"{name}: {v.code} {v.path}: {_printable(v.message)}")
        code |= bool(violations)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
