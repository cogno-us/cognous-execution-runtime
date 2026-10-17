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
import re
from dataclasses import dataclass
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
    if "const" in schema and value != schema["const"]:
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
        if "pattern" in schema and not re.search(schema["pattern"], value):
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
    if kind == "recovery":
        recovery_violations = _recovery_rules(obs, result)
        if recovery_violations:
            return out + recovery_violations
    if result["status"] == "unknown":
        if kind == "validated":
            out.append(Violation("unknown_with_validated_observation", "$.execution_result.observation",
                                 "status 'unknown' asserts that no validated observation exists; it cannot carry a "
                                 "validated Control Plane observation"))
            return out
        expected = "unknown"
        if kind == "recovery" and obs["status"] in {"applied", "partial"}:
            expected = obs["status"]
        if result["observed_state"] != expected:
            out.append(Violation("unknown_state_mismatch", "$.execution_result.observed_state",
                                 f"status 'unknown' with {'a recovery observation' if kind == 'recovery' else 'no observation'} "
                                 f"requires observed_state {expected!r}; 'applied'/'partial' is only what recovery "
                                 "reports, and 'unknown' otherwise"))
            return out
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
        if any(seq[i] == seq[j] for i in range(len(seq)) for j in range(i)):
            out.append(Violation("repeated_record", "$.control_plane_attempts",
                                 f"attempt {aid} lifecycle repeats a record"))
    return out


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
            # Journal ids and check ids are separate producer ids today; bind by content.
            candidates = [i for i, row in enumerate(checks_rows)
                          if row["stage"] == ev["stage"] and _norm(row["inputs"]) == _norm(state[cid]["inputs"])
                          and set(ev["reasons"]) <= set(row["reasons"])]
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


def validate(doc: Any) -> list[Violation]:
    """Return all violations; an empty list means the document conforms to 0.1.0-draft."""
    out = validate_structure(doc)
    if out:
        return out
    for rule in (_executor_rules, _control_plane_rules, _journal_rules):
        out.extend(rule(doc))
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


def main(argv: list[str]) -> int:
    import sys
    code = 0
    for name in argv:
        violations = validate(json.loads(Path(name).read_text(encoding="utf-8")))
        for v in violations:
            print(f"{name}: {v.code} {v.path}: {v.message}")
        code |= bool(violations)
    return code


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
