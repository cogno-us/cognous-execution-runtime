"""Frozen formats for the trusted authorization pin (Registry r08-v2 Amendment 4, adopted at Registry `1a4de326`).

PURE: constants, canonical serialization, closed-member validation, row and anchor digests, the section 7 admission decision table and the
per-decision fence path. No file, database or lock is opened, written or created here, so importing this module grants no write capability.
The installer (``host/authorization_pin``) and the Runtime guard (``engine/authorization_pin``) both import it, so they cannot diverge.

This module is the interface freeze of the F-A1 implementation. Changing a constant, a member list or a decision rule is a contract change
and goes through the lead builder, never through a component workstream.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PIN_PROFILE = "ocs-s4/authorization-pin/0.1"
ANCHOR_PROFILE = "ocs-s4/pin-anchor/1"
ROW_DOMAIN = b"ocs-s4/authorization-pin-row/0.1\x00"
PAYLOAD_DOMAIN = b"ocs-s4/authorization-pin-payload/0.1\x00"
INTENT_DOMAIN = b"ocs-s4/pin-anchor-intent/1\x00"
ANCHOR_DOMAIN = b"ocs-s4/pin-anchor-record/1\x00"
ZERO_DIGEST = "sha256:" + "0" * 64

#: Table and meta names of the pin store (SQLite, ``journal_mode=DELETE``, written only by the installer identity).
PIN_TABLE = "authorization_pin_v1"
META_TABLE = "authorization_pin_meta_v1"

#: Closed payload members of Amendment 4 section 3.1 that originate at the Control Plane (17), in contract order.
PAYLOAD_MEMBERS = (
    "tenant_id", "institution_id", "authority_domain", "consumer_id", "operation",
    "decision_id", "effect_id", "action_id", "grant_id", "grant_revision",
    "approval_commitment", "binding_digest", "selection_epoch", "activation_ref", "policy_generation",
    "not_before", "expires_at",
)
_STRING_MEMBERS = tuple(m for m in PAYLOAD_MEMBERS if m not in ("selection_epoch", "policy_generation"))
_DIGEST_MEMBERS = ("approval_commitment", "binding_digest", "activation_ref")
_INT_MEMBERS = ("selection_epoch", "policy_generation")
_TIME_MEMBERS = ("not_before", "expires_at")
STATUSES = ("active", "withdrawn", "revoked", "superseded")
TERMINAL_STATUSES = ("withdrawn", "revoked", "superseded")
ACTIONS = ("install", "terminal")
ROW_MEMBERS = ("pin_epoch", "decision_id", "status", "payload", "provenance", "prev_pin_digest", "pin_digest")
INTENT_MEMBERS = ("epoch", "decision_id", "action", "status", "provenance_ref", "intent_digest")
ANCHOR_MEMBERS = ("profile", "pin_store_id", "committed_epoch", "committed_head_digest", "intent", "anchored_at", "anchor_digest")

#: Typed outcomes. Codes are EXISTING Registry reason codes (Amendment 4 section 9); the cause is evidence detail, never a new reason code.
CODE_MISMATCH = "policy_identity_mismatch"   # REFUSE
CODE_UNKNOWN = "store_unknown"               # HOLD
CODE_UNAVAILABLE = "verifier_unavailable"    # HOLD
CAUSE_PENDING = "pin_transition_pending"
CAUSE_ANCHOR_UNAVAILABLE = "pin_anchor_unavailable"
CAUSE_ANCHOR_INCONSISTENT = "pin_anchor_inconsistent"
CAUSE_STORE_UNREADABLE = "pin_store_unreadable"
CAUSE_CHAIN_BROKEN = "pin_chain_broken"
CAUSE_CONFLICT = "pin_conflict"
CAUSE_FENCE_UNAVAILABLE = "pin_fence_unavailable"
CAUSE_ABSENT = "pin_absent"
CAUSE_REQUIRED_BINDING_ABSENT = "pin_required_binding_absent"
CAUSE_NOT_VALID_NOW = "pin_not_valid_now"
CAUSE_STATUS_PREFIX = "pin_status:"
CAUSE_LINEAGE_PREFIX = "pin_lineage_mismatch:"
CAUSE_BINDING_PREFIX = "pin_binding_mismatch:"

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MAX_STR = 512


class PinFormatError(ValueError):
    """A pin row, payload, intent or anchor is not in the frozen format."""


def canonical_bytes(value: Any) -> bytes:
    """Same canonical JSON as the Control Plane commitment helper: sorted keys, no whitespace, UTF-8, no NaN."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(domain: bytes, value: Any) -> str:
    return "sha256:" + hashlib.sha256(domain + canonical_bytes(value)).hexdigest()


def valid_digest(value: Any) -> bool:
    return type(value) is str and _DIGEST.match(value) is not None


def parse_instant(value: Any) -> datetime:
    """Strict, timezone-aware ISO-8601 instant in UTC. Raises PinFormatError."""
    if type(value) is not str or not value or len(value) > 64:
        raise PinFormatError("instant")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PinFormatError("instant") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PinFormatError("instant")
    return parsed.astimezone(timezone.utc)


def payload_defect(payload: Any) -> Optional[str]:
    """Name of the first defect of a pin payload, or None. Closed member set; no extra, missing, null or wrong-typed member."""
    if type(payload) is not dict or any(type(k) is not str for k in payload):
        return "payload"
    for name in PAYLOAD_MEMBERS:
        if name not in payload:
            return name
    for name in sorted(payload):
        if name not in PAYLOAD_MEMBERS:
            return name
    for name in _STRING_MEMBERS:
        value = payload[name]
        if type(value) is not str or not value or len(value) > _MAX_STR:
            return name
    for name in _DIGEST_MEMBERS:
        if not valid_digest(payload[name]) or payload[name] == ZERO_DIGEST:
            return name
    for name in _INT_MEMBERS:
        value = payload[name]
        if type(value) is not int or value < 1 or value > 2**53 - 1:
            return name
    try:
        start, end = (parse_instant(payload[m]) for m in _TIME_MEMBERS)
    except PinFormatError:
        return "not_before" if _bad_time(payload["not_before"]) else "expires_at"
    if end <= start:
        return "expires_at"
    return None


def _bad_time(value: Any) -> bool:
    try:
        parse_instant(value)
        return False
    except PinFormatError:
        return True


def payload_digest(payload: dict) -> str:
    defect = payload_defect(payload)
    if defect is not None:
        raise PinFormatError(f"payload:{defect}")
    return _digest(PAYLOAD_DOMAIN, payload)


def row_digest(row_without_digest: dict) -> str:
    return _digest(ROW_DOMAIN, row_without_digest)


def make_row(*, pin_epoch: int, decision_id: str, status: str, payload: dict, provenance: dict, prev_pin_digest: str) -> dict:
    """Build a complete pin row including its ``pin_digest``. Every row, terminal rows included, repeats the full payload."""
    row = {"pin_epoch": pin_epoch, "decision_id": decision_id, "status": status, "payload": payload,
           "provenance": provenance, "prev_pin_digest": prev_pin_digest}
    defect = row_defect({**row, "pin_digest": ZERO_DIGEST}, check_digest=False)
    if defect is not None:
        raise PinFormatError(f"row:{defect}")
    row["pin_digest"] = row_digest(row)
    return row


def row_defect(row: Any, *, check_digest: bool = True) -> Optional[str]:
    if type(row) is not dict or sorted(row) != sorted(ROW_MEMBERS):
        return "row"
    if type(row["pin_epoch"]) is not int or row["pin_epoch"] < 1:
        return "pin_epoch"
    if type(row["decision_id"]) is not str or not row["decision_id"]:
        return "decision_id"
    if row["status"] not in STATUSES:
        return "status"
    defect = payload_defect(row["payload"])
    if defect is not None:
        return f"payload.{defect}"
    if row["payload"]["decision_id"] != row["decision_id"]:
        return "decision_id"
    prov = row["provenance"]
    if type(prov) is not dict or sorted(prov) != ["intent_digest", "reason"] or not valid_digest(prov["intent_digest"]) \
            or type(prov["reason"]) is not str or not prov["reason"] or len(prov["reason"]) > _MAX_STR:
        return "provenance"
    if not valid_digest(row["prev_pin_digest"]) or not valid_digest(row["pin_digest"]):
        return "digest"
    if check_digest:
        body = {k: row[k] for k in ROW_MEMBERS if k != "pin_digest"}
        if row["pin_digest"] != row_digest(body):
            return "pin_digest"
    return None


def decode_row(text: str) -> dict:
    """Parse and verify one stored ``row_json``."""
    try:
        row = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise PinFormatError("row") from exc
    defect = row_defect(row)
    if defect is not None:
        raise PinFormatError(f"row:{defect}")
    return row


def encode_row(row: dict) -> str:
    defect = row_defect(row)
    if defect is not None:
        raise PinFormatError(f"row:{defect}")
    return canonical_bytes(row).decode("utf-8")


def chain_defect(rows: list[dict], anchor: dict) -> Optional[str]:
    """Verify the hash chain over ``rows`` (ascending ``pin_epoch``) that starts at the anchored head.

    ``rows`` must start at epoch ``max(committed_epoch, 1)`` (the anchored row itself when ``committed_epoch >= 1``) and run to the store head.
    Returns a cause string (``pin_chain_broken``) or None. An empty list is valid only when ``committed_epoch == 0`` and the store is empty.
    """
    committed = anchor["committed_epoch"]
    if not rows:
        return None if committed == 0 and anchor["committed_head_digest"] == ZERO_DIGEST else CAUSE_CHAIN_BROKEN
    first = rows[0]
    if first["pin_epoch"] != max(committed, 1):
        return CAUSE_CHAIN_BROKEN
    if committed == 0:
        if first["prev_pin_digest"] != ZERO_DIGEST:
            return CAUSE_CHAIN_BROKEN
    elif first["pin_digest"] != anchor["committed_head_digest"]:
        return CAUSE_CHAIN_BROKEN
    previous = None
    for row in rows:
        if row_defect(row) is not None:
            return CAUSE_CHAIN_BROKEN
        if previous is not None and (row["pin_epoch"] != previous["pin_epoch"] + 1 or row["prev_pin_digest"] != previous["pin_digest"]):
            return CAUSE_CHAIN_BROKEN
        previous = row
    return None


def decision_state(rows: list[dict]) -> tuple[Optional[dict], Optional[str]]:
    """Current row of one decision from ALL its rows (ascending epoch), and a conflict cause.

    Conflict (HOLD ``pin_conflict``): non-ascending epochs; two rows at one epoch; an ``active`` row after a terminal row; a terminal row
    followed by another row; a first row that is not ``active``. The current state is the row with the highest epoch.
    """
    if not rows:
        return None, None
    last_epoch, seen_terminal = 0, False
    for index, row in enumerate(rows):
        if row["pin_epoch"] <= last_epoch:
            return None, CAUSE_CONFLICT
        last_epoch = row["pin_epoch"]
        if index == 0 and row["status"] != "active":
            return None, CAUSE_CONFLICT
        if seen_terminal:
            return None, CAUSE_CONFLICT
        if row["status"] in TERMINAL_STATUSES:
            seen_terminal = True
        elif index > 0:
            return None, CAUSE_CONFLICT       # a second active row for one decision is a conflicting payload or a re-activation
    return rows[-1], None


def make_intent(*, epoch: int, decision_id: str, action: str, status: str, provenance_ref: str) -> dict:
    if action not in ACTIONS or (action == "install" and status != "active") or (action == "terminal" and status not in TERMINAL_STATUSES):
        raise PinFormatError("intent")
    intent = {"epoch": epoch, "decision_id": decision_id, "action": action, "status": status, "provenance_ref": provenance_ref}
    intent["intent_digest"] = _digest(INTENT_DOMAIN, intent)
    return intent


def intent_defect(intent: Any) -> Optional[str]:
    if type(intent) is not dict or sorted(intent) != sorted(INTENT_MEMBERS):
        return "intent"
    if type(intent["epoch"]) is not int or intent["epoch"] < 1:
        return "epoch"
    if type(intent["decision_id"]) is not str or not intent["decision_id"] or type(intent["provenance_ref"]) is not str or not intent["provenance_ref"]:
        return "intent"
    if intent["action"] not in ACTIONS or (intent["action"] == "install") != (intent["status"] == "active"):
        return "action"
    if intent["status"] not in STATUSES:
        return "status"
    body = {k: intent[k] for k in INTENT_MEMBERS if k != "intent_digest"}
    if intent["intent_digest"] != _digest(INTENT_DOMAIN, body):
        return "intent_digest"
    return None


def make_anchor(*, pin_store_id: str, committed_epoch: int, committed_head_digest: str, intent: Optional[dict], anchored_at: str) -> dict:
    anchor = {"profile": ANCHOR_PROFILE, "pin_store_id": pin_store_id, "committed_epoch": committed_epoch,
              "committed_head_digest": committed_head_digest, "intent": intent, "anchored_at": anchored_at}
    anchor["anchor_digest"] = _digest(ANCHOR_DOMAIN, anchor)
    defect = anchor_defect(anchor)
    if defect is not None:
        raise PinFormatError(f"anchor:{defect}")
    return anchor


def anchor_defect(anchor: Any) -> Optional[str]:
    if type(anchor) is not dict or sorted(anchor) != sorted(ANCHOR_MEMBERS):
        return "anchor"
    if anchor["profile"] != ANCHOR_PROFILE:
        return "profile"
    if type(anchor["pin_store_id"]) is not str or not anchor["pin_store_id"] or len(anchor["pin_store_id"]) > _MAX_STR:
        return "pin_store_id"
    if type(anchor["committed_epoch"]) is not int or anchor["committed_epoch"] < 0:
        return "committed_epoch"
    if not valid_digest(anchor["committed_head_digest"]) or (anchor["committed_epoch"] == 0) != (anchor["committed_head_digest"] == ZERO_DIGEST):
        return "committed_head_digest"
    if anchor["intent"] is not None:
        defect = intent_defect(anchor["intent"])
        if defect is not None:
            return f"intent.{defect}"
    if _bad_time(anchor["anchored_at"]):
        return "anchored_at"
    body = {k: anchor[k] for k in ANCHOR_MEMBERS if k != "anchor_digest"}
    if anchor["anchor_digest"] != _digest(ANCHOR_DOMAIN, body):
        return "anchor_digest"
    return None


def anchor_encode(anchor: dict) -> bytes:
    defect = anchor_defect(anchor)
    if defect is not None:
        raise PinFormatError(f"anchor:{defect}")
    return canonical_bytes(anchor) + b"\n"


def anchor_decode(data: bytes) -> dict:
    try:
        anchor = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PinFormatError("anchor:unparsable") from exc
    defect = anchor_defect(anchor)
    if defect is not None:
        raise PinFormatError(f"anchor:{defect}")
    return anchor


def fence_path(fence_dir: str | Path, decision_id: str) -> Path:
    """Per-decision fence file. The name is a fixed hash of the decision id, so a claim can never name a path."""
    if type(decision_id) is not str or not decision_id:
        raise PinFormatError("decision_id")
    return Path(fence_dir) / (hashlib.sha256(decision_id.encode("utf-8")).hexdigest() + ".fence")


@dataclasses.dataclass(frozen=True)
class Head:
    """The pin store head as read by the reader or the installer. An empty store is ``Head(0, ZERO_DIGEST, None, None)``."""
    epoch: int
    digest: str
    row_decision_id: Optional[str]
    row_status: Optional[str]


EMPTY_HEAD = Head(0, ZERO_DIGEST, None, None)


@dataclasses.dataclass(frozen=True)
class Admission:
    verdict: str                  # "accept" | "hold_decision" | "hold_all"
    cause: Optional[str] = None


ACCEPT = Admission("accept")


def anchor_admission(anchor: Optional[dict], head: Head, decision_id: str) -> Admission:
    """Amendment 4 section 7 decision table. ``anchor`` None means absent, unreadable or malformed. Chain verification is separate."""
    if anchor is None or anchor_defect(anchor) is not None:
        return Admission("hold_all", CAUSE_ANCHOR_UNAVAILABLE)
    committed, intent = anchor["committed_epoch"], anchor["intent"]
    inconsistent = Admission("hold_all", CAUSE_ANCHOR_INCONSISTENT)
    if head.epoch < committed:
        return inconsistent
    if head.epoch == committed and head.digest != anchor["committed_head_digest"]:
        return inconsistent
    if intent is None:
        return ACCEPT        # head == committed, or the store is ahead of a stale anchor (the chain check decides)
    if intent["epoch"] != committed + 1:
        return inconsistent
    if head.epoch == committed:                      # the intended row is not committed
        if intent["action"] == "terminal" and intent["decision_id"] == decision_id:
            return Admission("hold_decision", CAUSE_PENDING)
        return ACCEPT
    if head.epoch == intent["epoch"]:                # the row is committed and the anchor completion is pending
        if head.row_decision_id == intent["decision_id"] and head.row_status == intent["status"]:
            return ACCEPT
        return inconsistent
    return inconsistent
