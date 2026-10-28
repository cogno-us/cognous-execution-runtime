"""V04: durable, scope-bound Registry selection authority inside the existing Runtime destination database.

BUILDER_TESTED developer implementation. NOT independently qualified. NOT an effect path: nothing in this module executes, authorizes or
commits an effect. The effect-time guard inside `execute_claim_atomic` is V05 (not implemented here, not authorized).

What it is
  * The Runtime destination (`AtomicAuthorityEffectDestination`) owns activation (ADR EXT-S4-0 section 3.1). This module adds the closed
    nine-table set to the SAME SQLite file, through the destination's own connection and `BEGIN IMMEDIATE` discipline. There is no second
    database, no service and no in-memory-only authority state.
  * One trusted writer set: `activate_selection`, `withdraw_selection` and `sync_selection` (the revoke path lives inside the snapshot
    `sync`). Each commits the state change and its hash-chained log entry in ONE transaction. Nothing a caller or a claim supplies can write
    state, and `activation_ref` is never an input: it is the `entry_hash` of the committed `activate` entry.
  * The log (`selection_log_v1`) is the authority; the other tables are a materialized view of it that is checked against a full replay at
    every open, read and write. Anything missing, unknown, inconsistent or corrupt makes the store `unknown`, which is HOLD (`store_unknown`).
  * Selections are authenticated by the Registry (`verify_selection_v2`) at write time, inside the lock, with the destination's trusted clock.
    The Registry is NOT vendored or a hard dependency: it is imported lazily from the host environment; if it cannot be imported a writer
    refuses (`verifier_unavailable`). Reads and verification never need it.

Interpretations the builder had to make (the ADR fixes the 24-member log and the table NAMES, not every column or digest preimage):
  * Table columns, the anchor digest preimage domains and the anchor `anchor_version` member are builder choices, recorded in the PR.
  * `effective_policy_digest` in the read state is the Registry effective-policy digest of the verified RECORD payload (a caller-independent
    upper bound). The claim-carried, caller-dependent digest of contract 4 / 7.4 is not stored and is not compared here.
  * `selection_evidence_v1` is part of the closed table set and is verified (hash chain, anchor) but V04 writes NO rows to it: a refused
    transition writes nothing anywhere. Negative/fallback evidence (ADR 6.2) belongs to V05.

Deliberately NOT implemented (fail closed instead): recovery re-baselining (a lost/corrupt/restored store stays `unknown`), lineage
replacement, `relax_scope_required`, multi-signature statements (exactly one signature is required), Registry record/issuer revocation
extensions of the conformance kit. The host anchor is a synthetic developer-profile file, not production custody.
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import hashlib
import importlib
import json
import os
import re
import sqlite3
import tempfile
import time
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

CLASSIFICATION = "BUILDER_TESTED developer implementation; non-effecting; not independently qualified; HOLD; not production"

TABLES = (
    "selection_activation_v1", "selection_log_v1", "selection_highwater_v1", "selection_revoked_v1", "selection_scope_required_v1",
    "selection_generation_v1", "selection_lineage_v1", "selection_store_meta_v1", "selection_evidence_v1",
)

ENTRY_VERSION = "ocs-s4/log-entry/1"
ENTRY_DOMAIN = b"ocs-reg/s4/log-entry\n"
GOVERNED_DOMAIN = b"ocs-reg/s4/governed-scope\n"
EVIDENCE_DOMAIN = b"ocs-reg/s4/evidence\n"
EVIDENCE_VERSION = "ocs-s4/transition-evidence/1"
ANCHOR_VERSION = "ocs-s4/store-anchor/1"
ANCHOR_DOMAIN = b"ocs-reg/s4/store-anchor\n"
REASONS_LABEL = "runtime-authority-reasons/0.3"
REASONS_SHA256 = "sha256:58b29ef74b9bd49d3eae7278d8cce73c7cbc6a1095a07f7b9fa44f48a7b8c07f"
REASONS_ALLOWLIST = frozenset({(REASONS_LABEL, REASONS_SHA256)})
INT_MAX = 2147483647
SEQ_MAX = 2 ** 53 - 1
ZERO_HASH = "sha256:" + "0" * 64
SCOPE_NAMES = ("consumer_id", "tenant", "institution", "authority_domain", "operation", "family")
# M2: the composite Registry family (contract `r08-v3`). PR-2 added selection, activation and transition handling; PR-3 adds effect-time
# enforcement of all seven members in `check_effect` (the five sets by subset, the two ceilings by `<=`).
FAMILY_F1ABC = "f1abc-local-execution-limits"
CONTRACT_V3 = "r08-v3"
F1ABC_CEILINGS = ("max_amount", "max_effects")
F1ABC_AMOUNT_MAX = 2 ** 53          # Registry r08-v3 max_amount range 0..2^53
F1ABC_EFFECTS_MAX = 2 ** 53 - 1     # Registry r08-v3 max_effects range 0..2^53-1 (0 = deny-all)
# M3: the nine-member Registry family (contract `r08-v4`): the seven f1abc members plus `allowed_target_prefixes` and `target_match`. Activation,
# transition handling and effect-time enforcement of all nine members (targets by accepted-set containment) ship together in this module.
FAMILY_F1ABCD = "f1abcd-local-execution-limits"
CONTRACT_V4 = "r08-v4"
F1ABCD_TARGETS = ("allowed_target_prefixes", "target_match")
COMPOSITE_FAMILIES = frozenset({FAMILY_F1ABC, FAMILY_F1ABCD})

ENTRY_MEMBERS = (
    "entry_version", "activation_sequence", "prev_entry_hash", "entry_type", "cause", "governed_scope_digest", "scope_key_digest", "selection_id",
    "selection_epoch", "selection_digest", "policy_generation", "supersedes", "participation", "state_before", "state_after", "ratchet_after",
    "snapshot_version", "snapshot_fingerprint", "issuer_key_id", "reasons_registry_label", "reasons_registry_sha256", "recorded_at",
    "baseline_floor", "determination_ref",
)
ENTRY_TYPES = ("activate", "withdraw", "revoke", "sync")
CAUSES = ("none", "issuer_key_revoked", "baseline", "ratchet_provision", "non_participation")
STATES = ("none", "active", "withdrawn", "revoked")

_IDENTIFIER = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DIGEST = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_TIME = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
_STALE_CODES = frozenset({"selection_epoch_stale", "trust_snapshot_version_stale"})
_HOLD_CODES = frozenset({"store_unknown", "time_unavailable", "verifier_unavailable"})


# ---------------------------------------------------------------------------------------------------------------------------------
# Refusals and result types
# ---------------------------------------------------------------------------------------------------------------------------------
def classify(code: str) -> str:
    """REFUSE / REFUSE_STALE / HOLD (ADR 9.1). HOLD is an indeterminate-state outcome and never a success."""
    if code in _STALE_CODES:
        return "REFUSE_STALE"
    if code in _HOLD_CODES:
        return "HOLD"
    return "REFUSE"


class SelectionRefusal(PermissionError):
    """A typed refusal. `code` is an existing Registry reason code (r08-v2 revision 2 vocabulary); `cause` is typed detail, not a reason code."""

    def __init__(self, code: str, refusal_class: Optional[str] = None, cause: Optional[str] = None, detail: str = ""):
        super().__init__(f"{code}" + (f" [{cause}]" if cause else "") + (f": {detail}" if detail else ""))
        self.code = code
        self.refusal_class = refusal_class or classify(code)
        self.cause = cause
        self.detail = detail
        self.evidence_unavailable = False


class ReplayError(Exception):
    """The log (or an entry) violates the adopted schema, chain or transition rules."""


@dataclasses.dataclass(frozen=True)
class StoreStatus:
    state: str                      # "known" or "unknown"
    cause: Optional[str]
    head_sequence: int = 0
    head_hash: Optional[str] = None
    store_id: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class TransitionResult:
    entry_type: str
    activation_sequence: Optional[int]
    entry_hash: Optional[str]
    entry_hashes: tuple
    activation_ref: Optional[str]
    transition_ref: Optional[str]
    policy_generation: Optional[int]
    anchor_advanced: bool


@dataclasses.dataclass(frozen=True)
class SelectionState:
    """Read-only view of the verified durable state of one governed scope. Immutable; it has no writer and carries no authority."""
    status: str                     # active | withdrawn | revoked | not_activated
    store_state: str
    store_id: Optional[str]
    governed_scope_digest: str
    scope: tuple                    # (consumer_id, tenant, institution, authority_domain, operation, family)
    scope_key_digest: Optional[str]
    selection_id: Optional[str]
    selection_epoch: Optional[int]
    selection_digest: Optional[str]
    participation: Optional[str]
    valid_from: Optional[str]
    valid_until: Optional[str]
    issuer_key_id: Optional[str]
    record_id: Optional[str]
    record_version: Optional[int]
    record_digest: Optional[str]
    effective_policy_digest: Optional[str]
    activation_ref: Optional[str]
    transition_ref: Optional[str]
    policy_generation: Optional[int]
    trust_snapshot_version: Optional[int]
    trust_snapshot_fingerprint: Optional[str]
    scope_required: bool
    non_participation_determined: bool
    log_head_sequence: int
    log_head_hash: Optional[str]
    reasons_registry_label: str
    reasons_registry_sha256: str


# ---------------------------------------------------------------------------------------------------------------------------------
# Canonical serialization and digests (the Registry `canonical_bytes` profile, reimplemented: Runtime does not import the Registry here)
# ---------------------------------------------------------------------------------------------------------------------------------
def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _sha(prefix: bytes, value: Any) -> str:
    return "sha256:" + hashlib.sha256(prefix + canonical_bytes(value)).hexdigest()


def entry_hash_of(entry: Mapping[str, Any]) -> str:
    return _sha(ENTRY_DOMAIN, dict(entry))


def anchor_digest(anchor: Mapping[str, Any]) -> str:
    return _sha(ANCHOR_DOMAIN, dict(anchor))


def _identifier_ok(value: Any) -> bool:
    return type(value) is str and _IDENTIFIER.match(value) is not None


def _digest_ok(value: Any) -> bool:
    return type(value) is str and _DIGEST.match(value) is not None


def _int_ok(value: Any, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _scope_tuple(scope: Any) -> tuple:
    if not isinstance(scope, Mapping) or set(scope) != set(SCOPE_NAMES) or not all(_identifier_ok(scope[n]) for n in SCOPE_NAMES):
        raise SelectionRefusal("field_invalid", cause="governed_scope")
    return tuple(scope[n] for n in SCOPE_NAMES)


def governed_scope_digest(scope: Any) -> str:
    return _sha(GOVERNED_DOMAIN, list(_scope_tuple(scope)))


def scope_key_digest(selection_id: str, scope: Any) -> str:
    if not _identifier_ok(selection_id):
        raise SelectionRefusal("field_invalid", cause="selection_id")
    t = _scope_tuple(scope)
    return "sha256:" + hashlib.sha256(canonical_bytes([selection_id, t[0], t[1], t[2], t[3], t[4], t[5]])).hexdigest()


def next_generation(counter: int, *, saturating: bool) -> int:
    """policy_generation successor (ADR 4.2). `activate` refuses at saturation; `withdraw`/`revoke` never are blocked by it."""
    if counter >= INT_MAX:
        if saturating:
            return INT_MAX
        raise SelectionRefusal("field_invalid", cause="policy_generation")
    return counter + 1


# ---------------------------------------------------------------------------------------------------------------------------------
# The 24-member log entry: builder, closed nullability matrix, validator
# ---------------------------------------------------------------------------------------------------------------------------------
def build_entry(*, sequence: int, prev_entry_hash: str, entry_type: str, cause: str = "none", governed_scope_digest: Optional[str] = None,
                scope_key_digest: Optional[str] = None, selection_id: Optional[str] = None, selection_epoch: Optional[int] = None,
                selection_digest: Optional[str] = None, policy_generation: Optional[int] = None, supersedes: Optional[dict] = None,
                participation: Optional[str] = None, state_before: Optional[str] = None, state_after: Optional[str] = None,
                ratchet_after: Optional[bool] = None, snapshot_version: Optional[int] = None, snapshot_fingerprint: Optional[str] = None,
                issuer_key_id: Optional[str] = None, reasons_registry_label: Optional[str] = REASONS_LABEL,
                reasons_registry_sha256: Optional[str] = REASONS_SHA256, recorded_at: str = "", baseline_floor: Optional[int] = None,
                determination_ref: Optional[str] = None) -> dict:
    return {
        "entry_version": ENTRY_VERSION, "activation_sequence": sequence, "prev_entry_hash": prev_entry_hash, "entry_type": entry_type, "cause": cause,
        "governed_scope_digest": governed_scope_digest, "scope_key_digest": scope_key_digest, "selection_id": selection_id,
        "selection_epoch": selection_epoch, "selection_digest": selection_digest, "policy_generation": policy_generation, "supersedes": supersedes,
        "participation": participation, "state_before": state_before, "state_after": state_after, "ratchet_after": ratchet_after,
        "snapshot_version": snapshot_version, "snapshot_fingerprint": snapshot_fingerprint, "issuer_key_id": issuer_key_id,
        "reasons_registry_label": reasons_registry_label, "reasons_registry_sha256": reasons_registry_sha256, "recorded_at": recorded_at,
        "baseline_floor": baseline_floor, "determination_ref": determination_ref,
    }


_VARIABLE = (
    "governed_scope_digest", "scope_key_digest", "selection_id", "selection_epoch", "selection_digest", "policy_generation", "supersedes",
    "participation", "state_before", "state_after", "ratchet_after", "snapshot_version", "snapshot_fingerprint", "issuer_key_id",
    "baseline_floor", "determination_ref",
)
_G, _K, _S, _E, _D, _P, _U = ("governed_scope_digest", "scope_key_digest", "selection_id", "selection_epoch", "selection_digest", "policy_generation", "supersedes")
_PA, _SB, _SA, _RA, _SV, _SF, _KI, _BF, _DR = ("participation", "state_before", "state_after", "ratchet_after", "snapshot_version", "snapshot_fingerprint",
                                                  "issuer_key_id", "baseline_floor", "determination_ref")
# kind -> (members that must be non-null, members that may be null or non-null); every other variable member must be null
_SPEC = {
    "activate": ({_G, _K, _S, _E, _D, _P, _PA, _SB, _SA, _RA, _SV, _SF, _KI}, {_U}),
    "withdraw": ({_G, _K, _S, _E, _D, _P, _U, _PA, _SB, _SA, _RA, _SV, _SF, _KI}, set()),
    "revoke_row": ({_G, _K, _S, _E, _PA, _SB, _SA, _RA, _SV, _SF}, {_D, _P}),
    "revoke_row_key": ({_G, _K, _S, _E, _PA, _SB, _SA, _RA, _SV, _SF, _KI}, {_D, _P}),
    "revoke_pair_norow": ({_S, _E, _SV, _SF}, set()),
    "revoke_key_norow": ({_KI, _SV, _SF}, set()),
    "sync_snapshot": ({_SV, _SF}, set()),
    "sync_baseline": ({_BF, _DR}, {_SV, _SF}),
    "sync_ratchet_provision": ({_G, _PA, _SB, _SA, _RA, _DR}, set()),
    "sync_non_participation": ({_G, _PA, _SB, _SA, _RA, _DR}, set()),
}


def _kind(entry: Mapping[str, Any]) -> Optional[str]:
    t, cause = entry.get("entry_type"), entry.get("cause")
    if t in ("activate", "withdraw"):
        return t if cause == "none" else None
    if t == "revoke":
        if cause == "none":
            return "revoke_row" if entry.get(_G) is not None else "revoke_pair_norow"
        if cause == "issuer_key_revoked":
            return "revoke_row_key" if entry.get(_G) is not None else "revoke_key_norow"
        return None
    if t == "sync":
        return {"none": "sync_snapshot", "baseline": "sync_baseline", "ratchet_provision": "sync_ratchet_provision",
                "non_participation": "sync_non_participation"}.get(cause)
    return None


def _supersedes_ok(value: Any) -> bool:
    return (type(value) is dict and set(value) == {"selection_id", "epoch", "selection_digest"} and _identifier_ok(value["selection_id"])
            and _int_ok(value["epoch"], 1, INT_MAX) and _digest_ok(value["selection_digest"]))


def _member_defect(name: str, value: Any) -> Optional[str]:
    ok = {
        _G: lambda v: _digest_ok(v), _K: lambda v: _digest_ok(v), _S: lambda v: _identifier_ok(v), _E: lambda v: _int_ok(v, 1, INT_MAX),
        _D: lambda v: _digest_ok(v), _P: lambda v: _int_ok(v, 1, INT_MAX), _U: _supersedes_ok, _PA: lambda v: v in ("required", "optional") and type(v) is str,
        _SB: lambda v: type(v) is str and v in STATES, _SA: lambda v: type(v) is str and v in STATES, _RA: lambda v: type(v) is bool,
        _SV: lambda v: _int_ok(v, 1, SEQ_MAX), _SF: lambda v: _digest_ok(v), _KI: lambda v: _identifier_ok(v), _BF: lambda v: _int_ok(v, 0, INT_MAX),
        _DR: lambda v: _digest_ok(v),
    }[name]
    return None if ok(value) else name


def validate_entry(entry: Any) -> Optional[str]:
    """Path of the first defect (diagnostic text, not a reason code) in a candidate log entry, or None. Closed membership, exact types."""
    if type(entry) is not dict or set(entry) != set(ENTRY_MEMBERS) or any(type(k) is not str for k in entry):
        return "members"
    if entry["entry_version"] != ENTRY_VERSION:
        return "entry_version"
    if not _int_ok(entry["activation_sequence"], 1, SEQ_MAX):
        return "activation_sequence"
    if not _digest_ok(entry["prev_entry_hash"]):
        return "prev_entry_hash"
    if type(entry["entry_type"]) is not str or entry["entry_type"] not in ENTRY_TYPES:
        return "entry_type"
    if type(entry["cause"]) is not str or entry["cause"] not in CAUSES:
        return "cause"
    kind = _kind(entry)
    if kind is None:
        return "entry_type/cause"
    if type(entry["reasons_registry_label"]) is not str or type(entry["reasons_registry_sha256"]) is not str:
        return "reasons_registry"
    if (entry["reasons_registry_label"], entry["reasons_registry_sha256"]) not in REASONS_ALLOWLIST:
        return "reasons_registry"
    if type(entry["recorded_at"]) is not str or _TIME.match(entry["recorded_at"]) is None:
        return "recorded_at"
    required, optional = _SPEC[kind]
    for name in _VARIABLE:
        value = entry[name]
        if value is None:
            if name in required:
                return name
        elif name not in required and name not in optional:
            return name
        elif _member_defect(name, value) is not None:
            return name
    if kind == "sync_baseline" and (entry[_SV] is None) != (entry[_SF] is None):
        return "snapshot"
    if kind == "sync_ratchet_provision" and not (entry[_PA] == "required" and entry[_RA] is True and entry[_SB] == entry[_SA]):
        return "ratchet_provision"
    if kind == "sync_non_participation" and not (entry[_PA] == "optional" and entry[_RA] is False and entry[_SB] == "none" and entry[_SA] == "none"):
        return "non_participation"
    return None


# ---------------------------------------------------------------------------------------------------------------------------------
# Derived state: ONE apply function shared by replay (verification) and the writers, so they cannot disagree
# ---------------------------------------------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Derived:
    head_sequence: int = 0
    head_hash: str = ZERO_HASH
    known: bool = False
    baseline_floor: int = 0
    rows: dict = dataclasses.field(default_factory=dict)        # gsd -> row dict
    lineage: dict = dataclasses.field(default_factory=dict)     # gsd -> selection_id
    highwater: dict = dataclasses.field(default_factory=dict)   # scope_key_digest -> epoch
    generation: dict = dataclasses.field(default_factory=dict)  # gsd -> counter
    revoked_pairs: set = dataclasses.field(default_factory=set)
    revoked_keys: set = dataclasses.field(default_factory=set)
    ratchet: set = dataclasses.field(default_factory=set)
    non_participation: set = dataclasses.field(default_factory=set)
    history: set = dataclasses.field(default_factory=set)       # gsd with authority-changing history
    snapshot: Optional[tuple] = None                            # (version, fingerprint)
    generation_ceiling: int = 0


def _fail(cause: str) -> None:
    raise ReplayError(cause)


def apply_entry(d: Derived, e: dict, entry_hash: str) -> None:
    """Validate `e` against `d` (sequence, chain, schema, transition rules) and advance `d`. Raises ReplayError on any violation."""
    defect = validate_entry(e)
    if defect is not None:
        _fail(f"entry_invalid:{defect}" if not defect.startswith("reasons") else f"entry_invalid:{defect} (reasons registry)")
    if entry_hash_of(e) != entry_hash:
        _fail("entry_hash_mismatch")
    if e["activation_sequence"] != d.head_sequence + 1:
        _fail("sequence_gap")
    if e["prev_entry_hash"] != d.head_hash:
        _fail("prev_entry_hash_mismatch")
    kind = _kind(e)
    if d.head_sequence == 0:
        if kind != "sync_baseline" or e[_BF] != 0 or e[_SV] is not None:
            _fail("first_entry_not_genesis_baseline")
    elif kind == "sync_baseline":
        _fail("unsupported_baseline")
    gsd, skd, sid, epoch = e[_G], e[_K], e[_S], e[_E]
    row = d.rows.get(gsd) if gsd is not None else None
    ratchet_now = gsd in d.ratchet if gsd is not None else None

    def need_snapshot() -> None:
        if d.snapshot is None or (e[_SV], e[_SF]) != d.snapshot:
            _fail("snapshot_mismatch")

    def check_key_not_revoked() -> None:
        if e[_KI] in d.revoked_keys:
            _fail("issuer_key_revoked")

    if kind == "sync_baseline":
        d.known, d.baseline_floor = True, e[_BF]
    elif kind == "activate":
        need_snapshot(); check_key_not_revoked()
        if (sid, epoch) in d.revoked_pairs:
            _fail("revoked_pair")
        if d.lineage.get(gsd, sid) != sid:
            _fail("lineage_replacement")
        if any(g != gsd and s == sid for g, s in d.lineage.items()):
            _fail("selection_id_bound_to_other_scope")
        if gsd in d.non_participation:
            _fail("non_participation_determined")
        if epoch <= d.highwater.get(skd, 0):
            _fail("epoch_not_above_high_water")
        if row is None:
            if e[_U] is not None:
                _fail("supersedes_without_history")
        elif e[_U] != {"selection_id": row["selection_id"], "epoch": row["selection_epoch"], "selection_digest": row["selection_digest"]}:
            _fail("supersedes_not_current_row")
        if e[_SB] != (row["state"] if row else "none") or e[_SA] != "active":
            _fail("state_transition")
        if ratchet_now and e[_PA] == "optional":
            _fail("participation_weaker_than_ratchet")
        if e[_RA] != (ratchet_now or e[_PA] == "required"):
            _fail("ratchet_after")
        counter = d.generation.get(gsd, d.baseline_floor)
        if counter >= INT_MAX or e[_P] != counter + 1:
            _fail("policy_generation")
        d.rows[gsd] = {"state": "active", "scope_key_digest": skd, "selection_id": sid, "selection_epoch": epoch, "selection_digest": e[_D],
                       "participation": e[_PA], "activation_ref": entry_hash, "transition_ref": None, "generation": e[_P], "issuer_key_id": e[_KI],
                       "act_epoch": epoch, "act_digest": e[_D], "act_snapshot": (e[_SV], e[_SF]), "last_sequence": e["activation_sequence"]}
        d.lineage.setdefault(gsd, sid)
        d.highwater[skd] = epoch
        d.generation[gsd] = e[_P]
        if e[_PA] == "required":
            d.ratchet.add(gsd)
        d.history.add(gsd)
        d.generation_ceiling = max(d.generation_ceiling, e[_P])
    elif kind == "withdraw":
        need_snapshot(); check_key_not_revoked()
        if row is None or row["selection_id"] != sid or d.lineage.get(gsd) != sid:
            _fail("withdraw_without_matching_row")
        if row["state"] != "active" or e[_SB] != "active" or e[_SA] != "withdrawn":
            _fail("state_transition")
        if epoch <= d.highwater.get(skd, 0):
            _fail("epoch_not_above_high_water")
        if e[_U] != {"selection_id": row["selection_id"], "epoch": row["selection_epoch"], "selection_digest": row["selection_digest"]}:
            _fail("supersedes_not_current_row")
        if ratchet_now and e[_PA] == "optional":
            _fail("participation_weaker_than_ratchet")
        if e[_RA] != ratchet_now:
            _fail("ratchet_after")
        counter = d.generation.get(gsd, d.baseline_floor)
        if e[_P] != min(counter + 1, INT_MAX):
            _fail("policy_generation")
        row.update(state="withdrawn", selection_epoch=epoch, selection_digest=e[_D], participation=e[_PA], transition_ref=entry_hash, generation=e[_P],
                   last_sequence=e["activation_sequence"])
        d.highwater[skd] = epoch
        d.generation[gsd] = e[_P]
        d.history.add(gsd)
        d.generation_ceiling = max(d.generation_ceiling, e[_P])
    elif kind in ("revoke_row", "revoke_row_key"):
        need_snapshot()
        if row is None or row["selection_id"] != sid or d.lineage.get(gsd) != sid or e[_PA] != row["participation"]:
            _fail("revoke_without_matching_row")
        if e[_RA] != ratchet_now:
            _fail("ratchet_after")
        if e[_SB] != row["state"]:
            _fail("state_transition")
        row["last_sequence"] = e["activation_sequence"]
        current = epoch == row["selection_epoch"]
        if kind == "revoke_row":
            if (sid, epoch) in d.revoked_pairs:
                _fail("pair_already_revoked")
            d.revoked_pairs.add((sid, epoch))
            changes = current and row["state"] != "revoked"
            if current and e[_D] != row["selection_digest"]:
                _fail("revoke_digest")
            if not current and (e[_D] is not None or e[_SA] != row["state"]):
                _fail("revoke_non_current_pair")
        else:
            if not current or e[_D] != row["selection_digest"]:
                _fail("revoke_key_row_pair")
            d.revoked_keys.add(e[_KI])
            d.revoked_pairs.add((sid, epoch))
            changes = row["issuer_key_id"] == e[_KI] and row["state"] != "revoked"
        if changes:
            counter = d.generation.get(gsd, d.baseline_floor)
            if e[_SA] != "revoked" or e[_P] != min(counter + 1, INT_MAX):
                _fail("policy_generation")
            row.update(state="revoked", transition_ref=entry_hash, generation=e[_P])
            d.generation[gsd] = e[_P]
            d.generation_ceiling = max(d.generation_ceiling, e[_P])
        elif e[_P] is not None or e[_SA] != row["state"]:
            _fail("revoke_no_change")
        d.history.add(gsd)
        need_snapshot_consistent = True  # noqa: F841 (snapshot already checked above)
    elif kind == "revoke_pair_norow":
        need_snapshot()
        if (sid, epoch) in d.revoked_pairs or sid in d.lineage.values():
            _fail("revoke_pair_norow")
        d.revoked_pairs.add((sid, epoch))
    elif kind == "revoke_key_norow":
        need_snapshot()
        if e[_KI] in d.revoked_keys or any(r["issuer_key_id"] == e[_KI] and r["state"] != "revoked" for r in d.rows.values()):
            _fail("revoke_key_norow")
        d.revoked_keys.add(e[_KI])
    elif kind == "sync_snapshot":
        if d.snapshot is not None and e[_SV] <= d.snapshot[0]:
            _fail("snapshot_not_newer")
        d.snapshot = (e[_SV], e[_SF])
    elif kind == "sync_ratchet_provision":
        if gsd in d.non_participation:
            _fail("non_participation_determined")
        if e[_SB] != (row["state"] if row else "none"):
            _fail("ratchet_provision_state")
        d.ratchet.add(gsd)
    elif kind == "sync_non_participation":
        if gsd in d.ratchet or gsd in d.rows or gsd in d.lineage or gsd in d.history:
            _fail("non_participation_precondition")
        d.non_participation.add(gsd)
    d.head_sequence, d.head_hash = e["activation_sequence"], entry_hash


def replay(entries: list, capture_at: Optional[int] = None) -> tuple:
    """Replay [(entry, stored_hash), ...] from genesis. Returns (Derived, anchor-digest dict captured after `capture_at` or None)."""
    d, captured = Derived(), None
    for entry, stored_hash in entries:
        apply_entry(d, entry, stored_hash)
        if capture_at is not None and d.head_sequence == capture_at:
            captured = digests_of(d)
    return d, captured


def digests_of(d: Derived) -> dict:
    return {
        "scope_required_digest": _sha(b"ocs-reg/s4/anchor-scope-required\n", sorted([g, True] for g in d.ratchet)),
        "lineage_digest": _sha(b"ocs-reg/s4/anchor-lineage\n", sorted([g, s] for g, s in d.lineage.items())),
        "highwater_digest": _sha(b"ocs-reg/s4/anchor-highwater\n", {"scope": sorted([k, v] for k, v in d.highwater.items()),
                                                                     "snapshot": list(d.snapshot) if d.snapshot else None}),
        "revoked_set_digest": _sha(b"ocs-reg/s4/anchor-revoked\n", {"pairs": sorted([s, ep] for s, ep in d.revoked_pairs), "keys": sorted(d.revoked_keys)}),
        "generation_ceiling": max(d.generation_ceiling, d.baseline_floor),
    }


def _floor_digest(min_epochs: Mapping[str, int]) -> str:
    return _sha(b"ocs-reg/s4/anchor-floor-min-epochs\n", sorted([k, v] for k, v in min_epochs.items()))


# ---------------------------------------------------------------------------------------------------------------------------------
# Host-held synthetic anchor (developer/test profile; NOT production custody)
# ---------------------------------------------------------------------------------------------------------------------------------
class HostAnchorFile:
    """A JSON file held by the host OUTSIDE the database it protects. Synthetic: no signature, no authenticated custodian, no update cadence."""

    def __init__(self, path: Any):
        self.path = Path(path)

    def read(self) -> Optional[dict]:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            anchor, digest = data["anchor"], data["anchor_digest"]
        except Exception as exc:
            raise ReplayError("anchor_unreadable") from exc
        if type(anchor) is not dict or anchor_digest(anchor) != digest:
            raise ReplayError("anchor_invalid")
        return anchor

    LOCK_TIMEOUT_SECONDS = 10.0

    @staticmethod
    def position(anchor: Mapping[str, Any]) -> tuple:
        return anchor["head_sequence"], anchor["evidence_head_sequence"]

    @contextlib.contextmanager
    def _exclusive(self):
        """Cross-PROCESS exclusive guard: `fcntl.flock` on a sidecar lock file (never the anchor file itself, which is replaced). Bounded wait;
        failure to acquire raises (conservative) and nothing is written."""
        try:
            import fcntl
        except ImportError as exc:  # no supported cross-process lock on this platform: refuse rather than write unguarded
            raise ReplayError("anchor_lock_unsupported") from exc
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(str(self.path) + ".lock", "a+b")
        try:
            deadline = time.monotonic() + self.LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise ReplayError("anchor_lock_unavailable") from exc
                    time.sleep(0.01)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def advance(self, anchor: dict) -> str:
        """Monotonic compare-and-write, serialized across processes. Returns "written", "unchanged" (identical head already anchored) or
        "stale" (a newer anchor exists; NOT overwritten). Raises ReplayError, writing nothing, when the lock cannot be taken, when an existing
        anchor is unreadable/malformed/for another store, when the positions are incomparable or equal-but-different, or when no anchor exists
        and `anchor` is not the genesis head."""
        with self._exclusive():
            existing = self.read()  # malformed/unreadable raises: never treated as permission to overwrite
            if existing is None:
                if anchor["head_sequence"] != 1:
                    raise ReplayError("anchor_absent_after_genesis")
                self._write_unlocked(anchor)
                return "written"
            for key in ("store_id", "head_sequence", "evidence_head_sequence"):
                if key not in existing or (key != "store_id" and not _int_ok(existing[key], 0, SEQ_MAX)):
                    raise ReplayError("anchor_invalid")
            if existing["store_id"] != anchor["store_id"]:
                raise ReplayError("anchor_store_mismatch")
            old, new = self.position(existing), self.position(anchor)
            if old == new:
                if (existing["head_entry_hash"], existing["evidence_head_hash"]) != (anchor["head_entry_hash"], anchor["evidence_head_hash"]):
                    raise ReplayError("anchor_equal_position_conflict")
                return "unchanged"
            if old[0] >= new[0] and old[1] >= new[1]:
                return "stale"
            if not (new[0] >= old[0] and new[1] >= old[1]):
                raise ReplayError("anchor_positions_incomparable")
            self._write_unlocked(anchor)
            return "written"

    def _write_unlocked(self, anchor: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".anchor-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"anchor": anchor, "anchor_digest": anchor_digest(anchor)}))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)  # atomic: a reader sees the old or the new anchor, never a partial one
            dir_fd = os.open(str(self.path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise


# ---------------------------------------------------------------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------------------------------------------------------------
def _guard(table: str, *, update: Optional[str] = None, delete: bool = True) -> list:
    out = []
    if update is None:
        out.append(f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, '{table} is insert-only'); END")
    else:
        out.append(f"CREATE TRIGGER IF NOT EXISTS {table}_monotone BEFORE UPDATE ON {table} WHEN {update} BEGIN SELECT RAISE(ABORT, '{table} is monotone'); END")
    if delete:
        out.append(f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, '{table} rows are never deleted'); END")
    return out


_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS selection_store_meta_v1 (singleton INTEGER PRIMARY KEY CHECK(singleton=1), store_id TEXT NOT NULL,
        store_state TEXT NOT NULL CHECK(store_state IN ('known','unknown')), schema_version TEXT NOT NULL, baseline_sequence INTEGER NOT NULL,
        baseline_ref TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS selection_log_v1 (activation_sequence INTEGER PRIMARY KEY CHECK(activation_sequence>=1),
        entry_hash TEXT NOT NULL UNIQUE, entry_json TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS selection_activation_v1 (governed_scope_digest TEXT PRIMARY KEY, scope_key_digest TEXT NOT NULL,
        scope_json TEXT NOT NULL, selection_id TEXT NOT NULL, selection_epoch INTEGER NOT NULL, selection_digest TEXT NOT NULL,
        participation TEXT NOT NULL CHECK(participation IN ('required','optional')), valid_from TEXT NOT NULL, valid_until TEXT NOT NULL,
        issuer_key_id TEXT NOT NULL, record_id TEXT NOT NULL, record_version INTEGER NOT NULL, record_digest TEXT NOT NULL,
        record_policy_digest TEXT NOT NULL, activation_ref TEXT NOT NULL, transition_ref TEXT, policy_generation INTEGER NOT NULL,
        activation_state TEXT NOT NULL CHECK(activation_state IN ('active','withdrawn','revoked')), snapshot_version INTEGER NOT NULL,
        snapshot_fingerprint TEXT NOT NULL, last_sequence INTEGER NOT NULL, statement_json TEXT NOT NULL, record_json TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS selection_highwater_v1 (hw_key TEXT PRIMARY KEY, hw_kind TEXT NOT NULL CHECK(hw_kind IN ('scope','snapshot')),
        high_water_epoch INTEGER, snapshot_version INTEGER, snapshot_fingerprint TEXT, valid_until TEXT, min_epochs_json TEXT,
        min_trust_snapshot_version INTEGER, snapshot_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS selection_revoked_v1 (kind TEXT NOT NULL CHECK(kind IN ('pair','key')), selection_id TEXT NOT NULL DEFAULT '',
        epoch INTEGER NOT NULL DEFAULT 0, key_id TEXT NOT NULL DEFAULT '', log_sequence INTEGER NOT NULL, PRIMARY KEY(kind, selection_id, epoch, key_id))""",
    "CREATE TABLE IF NOT EXISTS selection_scope_required_v1 (governed_scope_digest TEXT PRIMARY KEY, set_sequence INTEGER NOT NULL, scope_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS selection_generation_v1 (governed_scope_digest TEXT PRIMARY KEY, policy_generation INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS selection_lineage_v1 (governed_scope_digest TEXT PRIMARY KEY, selection_id TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS selection_evidence_v1 (evidence_sequence INTEGER PRIMARY KEY CHECK(evidence_sequence>=1),
        evidence_hash TEXT NOT NULL UNIQUE, evidence_json TEXT NOT NULL)""",
    *_guard("selection_log_v1"), *_guard("selection_revoked_v1"), *_guard("selection_scope_required_v1"), *_guard("selection_lineage_v1"),
    *_guard("selection_evidence_v1"), *_guard("selection_store_meta_v1", update="1=1"), *_guard("selection_activation_v1", update="NEW.governed_scope_digest<>OLD.governed_scope_digest"),
    *_guard("selection_generation_v1", update="NEW.policy_generation<OLD.policy_generation"),
    *_guard("selection_highwater_v1", update="(OLD.hw_kind='scope' AND NEW.high_water_epoch<OLD.high_water_epoch) OR "
                                            "(OLD.hw_kind='snapshot' AND NEW.snapshot_version<OLD.snapshot_version)"),
]


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _registry() -> types.SimpleNamespace:
    try:
        return types.SimpleNamespace(
            decision=importlib.import_module("registry_successor.decision"), sel=importlib.import_module("registry_successor.v2_selection"),
            verify=importlib.import_module("registry_successor.verify"), ident=importlib.import_module("registry_successor.v2_policy_identity"),
            f1ab=importlib.import_module("registry_successor.v2_f1ab"), common=importlib.import_module("registry_successor.common"),
            verifier_cls=importlib.import_module("registry_adapters.ed25519_verifier").Ed25519Verifier)
    except Exception as exc:
        raise SelectionRefusal("verifier_unavailable", cause="registry_unavailable") from exc


def _registry_v3() -> types.SimpleNamespace:
    """The `r08-v3` Registry modules, loaded only when a v3 selection is presented (so an f1ab-only deployment never depends on them)."""
    try:
        ns = types.SimpleNamespace(
            sel=importlib.import_module("registry_successor.v3_selection"), verify=importlib.import_module("registry_successor.v3_verify"),
            record=importlib.import_module("registry_successor.v3_record"), ident=importlib.import_module("registry_successor.v3_policy_identity"),
            fam=importlib.import_module("registry_successor.v3_f1abc"))
        if ns.fam.FAMILY_F1ABC != FAMILY_F1ABC or ns.sel.V3_CONTRACT != CONTRACT_V3 or tuple(ns.fam.F1ABC_FIELDS) != sa_fields() + F1ABC_CEILINGS:
            raise ValueError("registry r08-v3 constants differ from the Runtime's")
        return ns
    except Exception as exc:
        raise SelectionRefusal("verifier_unavailable", cause="registry_unavailable") from exc


def _registry_v4() -> types.SimpleNamespace:
    """The `r08-v4` Registry modules, loaded only when a v4 selection is presented (an f1ab/f1abc-only deployment never depends on them)."""
    try:
        ns = types.SimpleNamespace(
            sel=importlib.import_module("registry_successor.v4_selection"), verify=importlib.import_module("registry_successor.v4_verify"),
            record=importlib.import_module("registry_successor.v4_record"), ident=importlib.import_module("registry_successor.v4_policy_identity"),
            fam=importlib.import_module("registry_successor.v4_f1abcd"))
        if (ns.fam.FAMILY_F1ABCD != FAMILY_F1ABCD or ns.sel.V4_CONTRACT != CONTRACT_V4
                or tuple(ns.fam.F1ABCD_FIELDS) != sa_fields() + F1ABC_CEILINGS + F1ABCD_TARGETS):
            raise ValueError("registry r08-v4 constants differ from the Runtime's")
        return ns
    except Exception as exc:
        raise SelectionRefusal("verifier_unavailable", cause="registry_unavailable") from exc


def _coexistence_violated(active_scope_jsons: Any) -> bool:
    """True when the scope tuples (canonical JSON) of the ACTIVE rows show two different families for one consumer/tenant/institution/domain/operation."""
    seen: dict = {}
    for raw in active_scope_jsons:
        tup = json.loads(raw)
        if seen.setdefault(tuple(tup[:5]), tup[5]) != tup[5]:
            return True
    return False


def _declares_v3(selection_text: Any) -> bool:
    """True only when the text parses as a JSON object whose `contract_version` is exactly the string `r08-v3`. Anything else takes the unchanged
    r08-v2 path (which refuses a foreign contract), so no input can reach the v3 path by accident."""
    try:
        env = json.loads(selection_text)
    except Exception:
        return False
    return type(env) is dict and type(env.get("contract_version")) is str and env["contract_version"] == CONTRACT_V3


def _declares_v4(selection_text: Any) -> bool:
    """True only when the text parses as a JSON object whose `contract_version` is exactly the string `r08-v4` (same rule as `_declares_v3`)."""
    try:
        env = json.loads(selection_text)
    except Exception:
        return False
    return type(env) is dict and type(env.get("contract_version")) is str and env["contract_version"] == CONTRACT_V4


def _record_policy_digest(family: Any, payload: Any) -> str:
    """The Registry effective-policy digest of an authenticated record payload: all nine members for f1abcd, all seven for f1abc, the five sets for f1ab."""
    if family == FAMILY_F1ABCD:
        regv = _registry_v4()
        return regv.ident.effective_policy_digest(
            FAMILY_F1ABCD, {k: (sorted(payload[k]) if k in sa_fields() else payload[k]) for k in sa_fields() + F1ABC_CEILINGS + F1ABCD_TARGETS})
    if family == FAMILY_F1ABC:
        regv = _registry_v3()
        return regv.ident.effective_policy_digest(
            FAMILY_F1ABC, {k: (sorted(payload[k]) if k in sa_fields() else payload[k]) for k in sa_fields() + F1ABC_CEILINGS})
    reg = _registry()
    return reg.ident.effective_policy_digest(reg.f1ab.FAMILY_F1AB, {k: sorted(payload[k]) for k in sa_fields()})


# ---------------------------------------------------------------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------------------------------------------------------------
class SelectionAuthorityStore:
    """Writer/reader over the destination's own database. Construct through `AtomicAuthorityEffectDestination.configure_selection_authority`."""

    def __init__(self, destination: Any, anchor: Any, *, reasons_registry: tuple = (REASONS_LABEL, REASONS_SHA256)):
        self._dest = destination
        self._anchor = anchor if isinstance(anchor, HostAnchorFile) else HostAnchorFile(anchor)
        self._reasons = reasons_registry
        conn = self._open()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in _SCHEMA:
                conn.execute(statement)
            conn.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(Exception):
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _open(self) -> sqlite3.Connection:
        """A connection to the destination's OWN database file with the destination's discipline (rw-only, FULL sync, FKs, manual transactions)."""
        conn = sqlite3.connect(self._dest.path.as_uri() + "?mode=rw", uri=True, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    # -- seams ----------------------------------------------------------------------------------------------------------------
    def _stage(self, stage: str) -> None:
        """Fault-injection seam (like the destination's `_transaction_stage`). Does nothing in production."""

    # -- verification ---------------------------------------------------------------------------------------------------------
    def _read_log(self, conn: sqlite3.Connection) -> list:
        out = []
        for seq, h, raw in conn.execute("SELECT activation_sequence, entry_hash, entry_json FROM selection_log_v1 ORDER BY activation_sequence"):
            try:
                entry = json.loads(raw)
            except Exception as exc:
                raise ReplayError("entry_unparseable") from exc
            if canonical_bytes(entry).decode("ascii", "replace") != raw:
                raise ReplayError("entry_not_canonical")
            out.append((entry, h))
        return out

    def _evidence_head(self, conn: sqlite3.Connection) -> tuple:
        prev, count = ZERO_HASH, 0
        for seq, h, raw in conn.execute("SELECT evidence_sequence, evidence_hash, evidence_json FROM selection_evidence_v1 ORDER BY evidence_sequence"):
            try:
                body = json.loads(raw)
            except Exception as exc:
                raise ReplayError("evidence_unparseable") from exc
            if seq != count + 1 or body.get("evidence_sequence") != seq or body.get("prev_evidence_hash") != prev or _sha(EVIDENCE_DOMAIN, body) != h:
                raise ReplayError("evidence_chain")
            prev, count = h, seq
        return count, prev

    def _snapshot_row(self, conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
        return conn.execute("SELECT * FROM selection_highwater_v1 WHERE hw_kind='snapshot'").fetchone()

    def _floors(self, conn: sqlite3.Connection) -> tuple:
        row = self._snapshot_row(conn)
        if row is None:
            return {}, None
        return {k: v for k, v in json.loads(row["min_epochs_json"] or "[]")}, row["min_trust_snapshot_version"]

    def _compare_tables(self, conn: sqlite3.Connection, d: Derived) -> Optional[str]:
        def rows(sql):
            return sorted(tuple(r) for r in conn.execute(sql))
        # selection_activation_v1 column inventory (every column is authority-relevant; none is excluded as metadata):
        #   replay-derived from the log ........ governed_scope_digest, scope_key_digest, selection_id, selection_epoch, selection_digest,
        #                                         participation, activation_state, activation_ref, transition_ref, policy_generation,
        #                                         issuer_key_id, snapshot_version, snapshot_fingerprint, last_sequence
        #   pinned by the log's selection_digest  statement_json -> valid_from, valid_until, record_id, record_version, record_digest, scope_json
        #   pinned by the statement's record pin  record_json -> record_policy_digest (recomputed with the Registry effective-policy function)
        exp_act = sorted((g, r["scope_key_digest"], r["selection_id"], r["selection_epoch"], r["selection_digest"], r["participation"], r["state"],
                          r["activation_ref"], r["transition_ref"], r["generation"], r["issuer_key_id"], r["act_snapshot"][0], r["act_snapshot"][1],
                          r["last_sequence"]) for g, r in d.rows.items())
        got_act = rows("SELECT governed_scope_digest, scope_key_digest, selection_id, selection_epoch, selection_digest, participation, activation_state, "
                       "activation_ref, transition_ref, policy_generation, issuer_key_id, snapshot_version, snapshot_fingerprint, last_sequence "
                       "FROM selection_activation_v1")
        checks = (
            ("selection_activation_v1", exp_act, got_act),
            ("selection_lineage_v1", sorted(d.lineage.items()), rows("SELECT governed_scope_digest, selection_id FROM selection_lineage_v1")),
            ("selection_generation_v1", sorted(d.generation.items()), rows("SELECT governed_scope_digest, policy_generation FROM selection_generation_v1")),
            ("selection_scope_required_v1", sorted((g,) for g in d.ratchet), rows("SELECT governed_scope_digest FROM selection_scope_required_v1")),
            ("selection_revoked_v1", sorted([("pair", s, ep, "") for s, ep in d.revoked_pairs] + [("key", "", 0, k) for k in d.revoked_keys]),
             rows("SELECT kind, selection_id, epoch, key_id FROM selection_revoked_v1")),
            ("selection_highwater_v1", sorted([(k, "scope", v) for k, v in d.highwater.items()]),
             rows("SELECT hw_key, hw_kind, high_water_epoch FROM selection_highwater_v1 WHERE hw_kind='scope'")),
        )
        for name, expected, got in checks:
            if [tuple(x) for x in expected] != got:
                return f"table_mismatch:{name}"
        for gsd, scope_json in conn.execute("SELECT governed_scope_digest, scope_json FROM selection_scope_required_v1"):
            try:
                tup = json.loads(scope_json)
                if (canonical_bytes(tup).decode("ascii") != scope_json or governed_scope_digest(dict(zip(SCOPE_NAMES, tup))) != gsd):
                    return "table_mismatch:selection_scope_required_v1"
            except Exception:
                return "table_mismatch:selection_scope_required_v1"
        pinned = self._compare_pinned(conn, d)
        if pinned:
            return pinned
        if _coexistence_violated(r[0] for r in conn.execute("SELECT scope_json FROM selection_activation_v1 WHERE activation_state='active'")):
            return "table_mismatch:family_coexistence"  # two families active for one governed tuple is never a known store
        snap = self._snapshot_row(conn)
        if (d.snapshot is None) != (snap is None) or (snap is not None and (snap["snapshot_version"], snap["snapshot_fingerprint"]) != d.snapshot):
            return "table_mismatch:selection_highwater_v1"
        return self._compare_snapshot_floor(snap, d) if snap is not None else None

    def _compare_snapshot_floor(self, snap: sqlite3.Row, d: Derived) -> Optional[str]:
        """Snapshot-floor columns (valid_until, min_epochs_json) are authenticated by the stored canonical normalized snapshot: it must reproduce the
        fingerprint the replayed log recorded for the current snapshot, and the columns must equal what it contains. min_trust_snapshot_version is the
        caller-supplied external floor, which the log does not carry: it is bounded (integer or NULL, never above the accepted snapshot version) and
        checked against the anchor's recorded floor in `_verify`; the log-derived snapshot high-water, not this column, is what refuses an older snapshot."""
        bad = "table_mismatch:selection_highwater_v1"
        try:
            norm = json.loads(snap["snapshot_json"])
            if canonical_bytes(norm).decode("ascii") != snap["snapshot_json"] or norm["version"] != d.snapshot[0]:
                return bad
            if not _int_ok(snap["min_trust_snapshot_version"], 1, d.snapshot[0]) and snap["min_trust_snapshot_version"] is not None:
                return bad
            if (snap["valid_until"] != norm["valid_until"]
                    or snap["min_epochs_json"] != json.dumps(sorted([k, v] for k, v in {p[0]: p[1] for p in norm["min_epochs"]}.items()))):
                return bad
            if _registry().ident.trust_snapshot_fingerprint(norm) != d.snapshot[1]:
                return bad
        except SelectionRefusal:
            return "registry_unavailable_for_snapshot_digest"
        except Exception:
            return bad
        return None

    def _compare_pinned(self, conn: sqlite3.Connection, d: Derived) -> Optional[str]:
        """The values the log does not carry (validity window, record identity, scope tuple, record policy digest) are authenticated by the stored
        canonical statement and record: the statement must hash to the activation entry's selection_digest (from the replayed log) and the record
        to the record pin inside that statement. Anything that does not reconstruct is table_mismatch (UNKNOWN), never silently accepted."""
        bad = "table_mismatch:selection_activation_v1"
        for row in conn.execute("SELECT * FROM selection_activation_v1"):
            r = d.rows.get(row["governed_scope_digest"])
            if r is None:
                return bad
            try:
                stmt, rec = json.loads(row["statement_json"]), json.loads(row["record_json"])
                if (canonical_bytes(stmt).decode("ascii") != row["statement_json"] or canonical_bytes(rec).decode("ascii") != row["record_json"]
                        or "sha256:" + hashlib.sha256(canonical_bytes(stmt)).hexdigest() != r["act_digest"]):
                    return bad
                scope = {"consumer_id": stmt["consumer_id"], "tenant": stmt["scope"]["tenant"], "institution": stmt["scope"]["institution"],
                         "authority_domain": stmt["scope"]["authority_domain"], "operation": stmt["operation"], "family": stmt["family"]}
                pin, eff = stmt["record"], stmt["effective"]
                body = {k: v for k, v in rec.items() if k != "record_digest"}
                if (stmt["disposition"] != "select" or stmt["selection_id"] != r["selection_id"] or stmt["epoch"] != r["act_epoch"]
                        or governed_scope_digest(scope) != row["governed_scope_digest"] or scope_key_digest(stmt["selection_id"], scope) != r["scope_key_digest"]
                        or row["scope_json"] != canonical_bytes(list(_scope_tuple(scope))).decode("ascii")
                        or row["valid_from"] != eff["valid_from"] or row["valid_until"] != eff["valid_until"]
                        or row["record_id"] != pin["record_id"] or row["record_version"] != pin["record_version"] or row["record_digest"] != pin["record_digest"]
                        or rec.get("record_digest") != pin["record_digest"] or rec.get("record_id") != pin["record_id"]
                        or rec.get("record_version") != pin["record_version"]
                        or "sha256:" + hashlib.sha256(canonical_bytes(body)).hexdigest() != pin["record_digest"]):
                    return bad
                reg = _registry()
                payload = rec["payload"]
                if stmt["family"] == FAMILY_F1ABC:  # the f1abc row must reconstruct from an r08-v3 record of that family
                    if rec.get("contract_version") != CONTRACT_V3 or rec.get("family") != FAMILY_F1ABC or stmt.get("participation") != "required":
                        return bad
                elif stmt["family"] == FAMILY_F1ABCD:  # the f1abcd row must reconstruct from an r08-v4 record of that family
                    if rec.get("contract_version") != CONTRACT_V4 or rec.get("family") != FAMILY_F1ABCD or stmt.get("participation") != "required":
                        return bad
                elif stmt["family"] != reg.f1ab.FAMILY_F1AB:
                    return bad
                if row["record_policy_digest"] != _record_policy_digest(stmt["family"], payload):
                    return bad
            except SelectionRefusal:
                return "registry_unavailable_for_policy_digest"
            except Exception:
                return bad
        return None

    def _verify(self, conn: sqlite3.Connection) -> tuple:
        """Full verification inside the caller's transaction. Returns (StoreStatus, Derived or None)."""
        def unknown(cause: str, d: Optional[Derived] = None, store_id: Optional[str] = None) -> tuple:
            return StoreStatus("unknown", cause, d.head_sequence if d else 0, d.head_hash if d else None, store_id), d

        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not set(TABLES) <= names:
                return unknown("schema_missing")
            meta = conn.execute("SELECT * FROM selection_store_meta_v1").fetchone()
            entries = self._read_log(conn)
            if meta is None:
                if entries or any(conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() for t in TABLES if t != "selection_store_meta_v1"):
                    return unknown("table_mismatch:selection_store_meta_v1")
                return unknown("not_baselined")
            anchor = None
            anchor_cause = None
            try:
                anchor = self._anchor.read()
                if anchor is None:
                    anchor_cause = "anchor_absent"
            except ReplayError as exc:
                anchor_cause = str(exc)
            capture_at = anchor["head_sequence"] if anchor and _int_ok(anchor.get("head_sequence"), 1, SEQ_MAX) else None
            try:
                d, captured = replay(entries, capture_at)
            except ReplayError as exc:
                return unknown(f"log_integrity:{exc}", None, meta["store_id"])
            if meta["store_state"] != "known" or not d.known or meta["baseline_sequence"] != 1 or meta["baseline_ref"] != entries[0][1]:
                return unknown("meta_mismatch", d, meta["store_id"])
            bad = self._compare_tables(conn, d)
            if bad:
                return unknown(bad, d, meta["store_id"])
            try:
                ev_seq, ev_hash = self._evidence_head(conn)
            except ReplayError as exc:
                return unknown(f"log_integrity:{exc}", d, meta["store_id"])
            if anchor_cause:
                return unknown(anchor_cause, d, meta["store_id"])
            if anchor.get("anchor_version") != ANCHOR_VERSION or set(anchor) != set(self._anchor_members()):
                return unknown("anchor_invalid", d, meta["store_id"])
            if anchor["store_id"] != meta["store_id"]:
                return unknown("anchor_store_mismatch", d, meta["store_id"])
            if not all(_int_ok(anchor.get(k), 0, SEQ_MAX) for k in ("head_sequence", "evidence_head_sequence", "floor_min_trust_snapshot_version", "generation_ceiling")):
                return unknown("anchor_invalid", d, meta["store_id"])
            if anchor["head_sequence"] > d.head_sequence:
                return unknown("anchor_ahead", d, meta["store_id"])
            if entries[anchor["head_sequence"] - 1][1] != anchor["head_entry_hash"]:
                return unknown("anchor_hash_mismatch", d, meta["store_id"])
            if captured is None or any(anchor[k] != v for k, v in captured.items()):
                return unknown("anchor_digest_mismatch", d, meta["store_id"])
            if anchor["evidence_head_sequence"] > ev_seq:
                return unknown("anchor_evidence_ahead", d, meta["store_id"])
            if anchor["evidence_head_sequence"] and conn.execute("SELECT evidence_hash FROM selection_evidence_v1 WHERE evidence_sequence=?",
                                                                 (anchor["evidence_head_sequence"],)).fetchone()[0] != anchor["evidence_head_hash"]:
                return unknown("anchor_evidence_hash_mismatch", d, meta["store_id"])
            floors, min_version = self._floors(conn)
            if anchor["head_sequence"] == d.head_sequence and anchor["floor_min_epochs_digest"] != _floor_digest(floors):
                return unknown("anchor_floor_mismatch", d, meta["store_id"])
            if (min_version or 0) < anchor["floor_min_trust_snapshot_version"]:
                return unknown("anchor_floor_mismatch", d, meta["store_id"])
            return StoreStatus("known", None, d.head_sequence, d.head_hash, meta["store_id"]), d
        except sqlite3.Error as exc:
            return unknown(f"store_unreadable:{type(exc).__name__}")

    @staticmethod
    def _anchor_members() -> tuple:
        return ("anchor_version", "store_id", "head_sequence", "head_entry_hash", "evidence_head_sequence", "evidence_head_hash", "generation_ceiling",
                "scope_required_digest", "lineage_digest", "highwater_digest", "revoked_set_digest", "floor_min_epochs_digest",
                "floor_min_trust_snapshot_version", "anchored_at")

    def verify(self) -> StoreStatus:
        conn = self._open()
        try:
            conn.execute("BEGIN")
            status, _ = self._verify(conn)
            conn.execute("COMMIT")
            return status
        finally:
            conn.close()

    def _require_known(self, conn: sqlite3.Connection) -> Derived:
        status, derived = self._verify(conn)
        if status.state != "known":
            raise SelectionRefusal("store_unknown", "HOLD", cause=status.cause)
        return derived

    # -- anchor ---------------------------------------------------------------------------------------------------------------
    def _anchor_for(self, conn: sqlite3.Connection, d: Derived, store_id: str, now: datetime) -> dict:
        floors, min_version = self._floors(conn)
        ev_seq, ev_hash = self._evidence_head(conn)
        return {"anchor_version": ANCHOR_VERSION, "store_id": store_id, "head_sequence": d.head_sequence, "head_entry_hash": d.head_hash,
                "evidence_head_sequence": ev_seq, "evidence_head_hash": ev_hash, **digests_of(d), "floor_min_epochs_digest": _floor_digest(floors),
                "floor_min_trust_snapshot_version": min_version or 0, "anchored_at": _utc(now)}

    def _advance_anchor(self, anchor: Optional[dict]) -> bool:
        """After the commit: advance the host anchor through the cross-process monotonic compare-and-write. The commit has already happened, so a
        failure (lock unavailable, malformed anchor) cannot undo it: it returns False, the anchor is NOT modified, and the next verification either
        accepts a store that is ahead of an intact older anchor (disclosed limit) or reports UNKNOWN for a malformed one."""
        if anchor is None:
            return False
        try:
            return self._anchor.advance(anchor) in ("written", "unchanged")
        except Exception:
            return False

    # -- transaction runner ---------------------------------------------------------------------------------------------------
    def _run(self, transition: str, body, *, baseline: bool = False) -> TransitionResult:
        conn = self._open()
        ctx: dict = {"known": False}
        try:
            conn.execute("BEGIN IMMEDIATE")
            now = self._dest._trusted_now()
            if baseline:
                derived = None
            else:
                derived = self._require_known(conn)
                ctx["known"] = True
            result, anchor = body(conn, derived, now, ctx)
            self._stage("before_commit")
            conn.execute("COMMIT")
        except SelectionRefusal as exc:
            with contextlib.suppress(Exception):
                conn.execute("ROLLBACK")
            raise
        except BaseException:
            with contextlib.suppress(Exception):
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return dataclasses.replace(result, anchor_advanced=self._advance_anchor(anchor))

    def _check_reasons(self) -> None:
        label, sha = self._reasons if isinstance(self._reasons, tuple) and len(self._reasons) == 2 else (None, None)
        if type(label) is not str or label != REASONS_LABEL:
            raise SelectionRefusal("field_invalid", cause="reasons_registry_label")
        if type(sha) is not str or sha != REASONS_SHA256:
            raise SelectionRefusal("field_invalid", cause="reasons_registry_sha256")

    # -- entry append ---------------------------------------------------------------------------------------------------------
    def _append(self, conn: sqlite3.Connection, d: Derived, now: datetime, **fields) -> tuple:
        self._check_reasons()
        entry = build_entry(sequence=d.head_sequence + 1, prev_entry_hash=d.head_hash, recorded_at=_utc(now),
                            reasons_registry_label=self._reasons[0], reasons_registry_sha256=self._reasons[1], **fields)
        entry_hash = entry_hash_of(entry)
        try:
            apply_entry(d, entry, entry_hash)
        except ReplayError as exc:
            raise SelectionRefusal("store_unknown", "HOLD", cause=f"internal_transition_violation:{exc}") from exc
        conn.execute("INSERT INTO selection_log_v1(activation_sequence, entry_hash, entry_json) VALUES(?,?,?)",
                     (entry["activation_sequence"], entry_hash, canonical_bytes(entry).decode("ascii")))
        self._stage("after_log_insert")
        return entry, entry_hash

    def _upsert_generation(self, conn, gsd: str, value: int) -> None:
        conn.execute("INSERT INTO selection_generation_v1(governed_scope_digest, policy_generation) VALUES(?,?) "
                     "ON CONFLICT(governed_scope_digest) DO UPDATE SET policy_generation=excluded.policy_generation", (gsd, value))

    def _upsert_highwater(self, conn, skd: str, epoch: int) -> None:
        conn.execute("INSERT INTO selection_highwater_v1(hw_key, hw_kind, high_water_epoch) VALUES(?,'scope',?) "
                     "ON CONFLICT(hw_key) DO UPDATE SET high_water_epoch=excluded.high_water_epoch", (skd, epoch))

    def _finish(self, conn, d: Derived, now: datetime) -> dict:
        store_id = conn.execute("SELECT store_id FROM selection_store_meta_v1").fetchone()[0]
        return self._anchor_for(conn, d, store_id, now)

    # -- public writers -------------------------------------------------------------------------------------------------------
    def sync_selection(self, kind: str, *, trust: Any = None, determination_ref: Optional[str] = None, governed_scope: Any = None,
                       min_trust_snapshot_version: Optional[int] = None) -> TransitionResult:
        """Log-chained `sync`-class transitions: `baseline` (genesis), `snapshot` (also the revoke path), `ratchet_provision`, `non_participation`.
        None of them activates, withdraws, selects or authorizes an effect."""
        if kind == "baseline":
            return self._run("sync_baseline", lambda c, d, n, x: self._do_baseline(c, n, determination_ref), baseline=True)
        if kind == "snapshot":
            reg = _registry()
            return self._run("sync_snapshot", lambda c, d, n, x: self._do_snapshot(c, d, n, x, reg, trust, min_trust_snapshot_version))
        if kind in ("ratchet_provision", "non_participation"):
            return self._run(f"sync_{kind}", lambda c, d, n, x: self._do_determination(c, d, n, x, kind, governed_scope, determination_ref))
        raise SelectionRefusal("field_invalid", cause="sync_kind")

    def activate_selection(self, *, record_text: str, selection_text: str, context: Any, trust: Any, verifier: Any) -> TransitionResult:
        reg = _registry()
        return self._run("activate", lambda c, d, n, x: self._do_select(c, d, n, x, reg, "activate", record_text, selection_text, context, trust, verifier))

    def withdraw_selection(self, *, selection_text: str, context: Any, trust: Any, verifier: Any) -> TransitionResult:
        reg = _registry()
        return self._run("withdraw", lambda c, d, n, x: self._do_select(c, d, n, x, reg, "withdraw", None, selection_text, context, trust, verifier))

    # -- transition bodies ----------------------------------------------------------------------------------------------------
    def _do_baseline(self, conn, now, determination_ref) -> tuple:
        if not _digest_ok(determination_ref):
            raise SelectionRefusal("field_invalid", cause="determination_ref")
        status, _ = self._verify(conn)
        if status.cause != "not_baselined":
            raise SelectionRefusal("store_unknown", "HOLD", cause=status.cause if status.state == "unknown" else "already_baselined")
        existing = None
        try:
            existing = self._anchor.read()
        except ReplayError:
            existing = "unreadable"
        if existing is not None or self._anchor.path.exists():
            raise SelectionRefusal("store_unknown", "HOLD", cause="genesis_refused_anchor_exists")
        d = Derived()
        entry, h = self._append(conn, d, now, entry_type="sync", cause="baseline", baseline_floor=0, determination_ref=determination_ref)
        conn.execute("INSERT INTO selection_store_meta_v1(singleton, store_id, store_state, schema_version, baseline_sequence, baseline_ref) "
                     "VALUES(1,?,'known','ocs-s4/store/1',1,?)", ("store-" + uuid.uuid4().hex, h))
        return TransitionResult("sync", 1, h, (h,), None, None, None, False), self._finish(conn, d, now)

    def _do_determination(self, conn, d, now, ctx, kind, governed_scope, determination_ref) -> tuple:
        gsd = governed_scope_digest(governed_scope)
        ctx["gsd"] = gsd
        if not _digest_ok(determination_ref):
            raise SelectionRefusal("field_invalid", cause="determination_ref")
        row = d.rows.get(gsd)
        if kind == "ratchet_provision":
            if gsd in d.ratchet:
                raise SelectionRefusal("field_invalid", cause="ratchet_already_set")
            if gsd in d.non_participation:
                raise SelectionRefusal("field_invalid", cause="non_participation_determined")
            state = row["state"] if row else "none"
            entry, h = self._append(conn, d, now, entry_type="sync", cause="ratchet_provision", governed_scope_digest=gsd, participation="required",
                                    state_before=state, state_after=state, ratchet_after=True, determination_ref=determination_ref)
            conn.execute("INSERT INTO selection_scope_required_v1(governed_scope_digest, set_sequence, scope_json) VALUES(?,?,?)",
                         (gsd, entry["activation_sequence"], canonical_bytes(list(_scope_tuple(governed_scope))).decode("ascii")))
        else:
            if gsd in d.ratchet or gsd in d.rows or gsd in d.lineage or gsd in d.history:
                raise SelectionRefusal("field_invalid", cause="authority_history_exists")
            entry, h = self._append(conn, d, now, entry_type="sync", cause="non_participation", governed_scope_digest=gsd, participation="optional",
                                    state_before="none", state_after="none", ratchet_after=False, determination_ref=determination_ref)
        return TransitionResult("sync", entry["activation_sequence"], h, (h,), None, None, None, False), self._finish(conn, d, now)

    def _do_snapshot(self, conn, d, now, ctx, reg, trust, min_trust_snapshot_version) -> tuple:
        try:
            norm = reg.ident.trust_snapshot_from_verified(trust)
            fingerprint = reg.ident.trust_snapshot_fingerprint(norm)
            version = norm["version"]
            valid_until = reg.common.parse_timestamp(norm["valid_until"])
            new_pairs = {(p[0], p[1]) for p in norm["revoked_selections"]}
            new_keys = set(norm["revoked_key_ids"])
            new_floors = {p[0]: p[1] for p in norm["min_epochs"]}
        except SelectionRefusal:
            raise
        except Exception as exc:
            raise SelectionRefusal("trust_snapshot_invalid", cause="snapshot_shape") from exc
        if valid_until is None or now >= valid_until:
            raise SelectionRefusal("trust_snapshot_expired")
        floors, stored_min = self._floors(conn)
        if min_trust_snapshot_version is not None:
            if not _int_ok(min_trust_snapshot_version, 1, SEQ_MAX):
                raise SelectionRefusal("trust_snapshot_invalid", cause="external_floor_invalid")
            if stored_min is not None and min_trust_snapshot_version < stored_min:
                raise SelectionRefusal("trust_snapshot_invalid", cause="external_floor_lowered")
        floor_v = max(stored_min or 0, min_trust_snapshot_version or 0) or None
        if floor_v is not None and version < floor_v:
            raise SelectionRefusal("trust_snapshot_version_stale")
        if d.snapshot is not None:
            if version < d.snapshot[0] or (version == d.snapshot[0] and fingerprint != d.snapshot[1]):
                raise SelectionRefusal("trust_snapshot_version_stale")
            if version == d.snapshot[0]:  # identical redelivery: accepted, authority unchanged
                return TransitionResult("sync", None, None, (), None, None, None, False), None
        if not d.revoked_pairs <= new_pairs or not d.revoked_keys <= new_keys:
            raise SelectionRefusal("trust_snapshot_invalid", cause="revocation_omitted")
        if any(new_floors.get(sid, 0) < floor for sid, floor in floors.items()):
            raise SelectionRefusal("trust_snapshot_invalid", cause="min_epoch_floor_lowered")
        hashes = []
        entry, h = self._append(conn, d, now, entry_type="sync", snapshot_version=version, snapshot_fingerprint=fingerprint)
        hashes.append(h)
        conn.execute("INSERT INTO selection_highwater_v1(hw_key, hw_kind, snapshot_version, snapshot_fingerprint, valid_until, min_epochs_json, "
                     "min_trust_snapshot_version, snapshot_json) VALUES('snapshot','snapshot',?,?,?,?,?,?) ON CONFLICT(hw_key) DO UPDATE SET "
                     "snapshot_version=excluded.snapshot_version, snapshot_fingerprint=excluded.snapshot_fingerprint, valid_until=excluded.valid_until, "
                     "min_epochs_json=excluded.min_epochs_json, min_trust_snapshot_version=excluded.min_trust_snapshot_version, "
                     "snapshot_json=excluded.snapshot_json",
                     (version, fingerprint, norm["valid_until"], json.dumps(sorted([k, v] for k, v in new_floors.items())), floor_v,
                      canonical_bytes(norm).decode("ascii")))
        for pair in sorted(new_pairs - d.revoked_pairs):
            hashes.append(self._revoke_pair(conn, d, now, pair))
        for key in sorted(new_keys - d.revoked_keys):
            hashes.extend(self._revoke_key(conn, d, now, key))
        return TransitionResult("sync", entry["activation_sequence"], hashes[0], tuple(hashes), None, None, None, False), self._finish(conn, d, now)

    def _revoke_pair(self, conn, d, now, pair) -> str:
        sid, epoch = pair
        gsd = next((g for g, s in d.lineage.items() if s == sid), None)
        snap = dict(snapshot_version=d.snapshot[0], snapshot_fingerprint=d.snapshot[1])
        if gsd is None:
            entry, h = self._append(conn, d, now, entry_type="revoke", selection_id=sid, selection_epoch=epoch, **snap)
            conn.execute("INSERT INTO selection_revoked_v1(kind, selection_id, epoch, log_sequence) VALUES('pair',?,?,?)", (sid, epoch, entry["activation_sequence"]))
            return h
        row = d.rows[gsd]
        current = epoch == row["selection_epoch"]
        changes = current and row["state"] != "revoked"
        gen = next_generation(d.generation.get(gsd, d.baseline_floor), saturating=True) if changes else None
        entry, h = self._append(
            conn, d, now, entry_type="revoke", governed_scope_digest=gsd, scope_key_digest=row["scope_key_digest"], selection_id=sid, selection_epoch=epoch,
            selection_digest=row["selection_digest"] if current else None, policy_generation=gen, participation=row["participation"] if False else None,
            **snap) if False else self._append_revoke_row(conn, d, now, gsd, row, sid, epoch, current, changes, gen, snap, "none", None)
        conn.execute("INSERT INTO selection_revoked_v1(kind, selection_id, epoch, log_sequence) VALUES('pair',?,?,?)", (sid, epoch, entry["activation_sequence"]))
        self._sync_row(conn, d, gsd, entry)
        return h

    def _append_revoke_row(self, conn, d, now, gsd, row, sid, epoch, current, changes, gen, snap, cause, key) -> tuple:
        # `participation`, `state_before/after` and `ratchet_after` are non-null for a row revoke; read BEFORE `_append` mutates `row`.
        before = row["state"]
        return self._append(conn, d, now, entry_type="revoke", cause=cause, governed_scope_digest=gsd, scope_key_digest=row["scope_key_digest"],
                            selection_id=sid, selection_epoch=epoch, selection_digest=row["selection_digest"] if current else None, policy_generation=gen,
                            participation=row["participation"], state_before=before, state_after="revoked" if changes else before,
                            ratchet_after=gsd in d.ratchet, issuer_key_id=key, **snap)

    def _revoke_key(self, conn, d, now, key) -> list:
        snap = dict(snapshot_version=d.snapshot[0], snapshot_fingerprint=d.snapshot[1])
        affected = sorted(g for g, r in d.rows.items() if r["issuer_key_id"] == key and r["state"] != "revoked")
        if not affected:
            entry, h = self._append(conn, d, now, entry_type="revoke", cause="issuer_key_revoked", issuer_key_id=key, **snap)
            conn.execute("INSERT INTO selection_revoked_v1(kind, key_id, log_sequence) VALUES('key',?,?)", (key, entry["activation_sequence"]))
            return [h]
        hashes = []
        for gsd in affected:
            row = d.rows[gsd]
            gen = next_generation(d.generation.get(gsd, d.baseline_floor), saturating=True)
            sid, epoch = row["selection_id"], row["selection_epoch"]
            pair_new = (sid, epoch) not in d.revoked_pairs
            entry, h = self._append_revoke_row(conn, d, now, gsd, row, sid, epoch, True, True, gen, snap, "issuer_key_revoked", key)
            hashes.append(h)
            conn.execute("INSERT OR IGNORE INTO selection_revoked_v1(kind, key_id, log_sequence) VALUES('key',?,?)", (key, entry["activation_sequence"]))
            if pair_new:
                conn.execute("INSERT OR IGNORE INTO selection_revoked_v1(kind, selection_id, epoch, log_sequence) VALUES('pair',?,?,?)",
                             (sid, epoch, entry["activation_sequence"]))
            self._sync_row(conn, d, gsd, entry)
        return hashes

    def _sync_row(self, conn, d, gsd, entry) -> None:
        row = d.rows[gsd]
        conn.execute("UPDATE selection_activation_v1 SET activation_state=?, transition_ref=?, policy_generation=?, last_sequence=? WHERE governed_scope_digest=?",
                     (row["state"], row["transition_ref"], row["generation"], entry["activation_sequence"], gsd))
        if entry["policy_generation"] is not None:
            self._upsert_generation(conn, gsd, entry["policy_generation"])

    def _do_select(self, conn, d, now, ctx, reg, mode, record_text, selection_text, context, trust, verifier) -> tuple:
        if type(selection_text) is not str or (mode == "activate" and type(record_text) is not str):
            raise SelectionRefusal("field_invalid", cause="input_not_text")
        if type(verifier) is not reg.verifier_cls:
            raise SelectionRefusal("verifier_unavailable", cause="verifier_not_admitted")
        v4 = _declares_v4(selection_text)  # M3: an `r08-v4` envelope takes the r08-v4 Registry entry points
        v3 = (not v4) and _declares_v3(selection_text)  # M2 PR-2: an `r08-v3` envelope takes the r08-v3 Registry entry points; everything else is unchanged r08-v2
        composite = v3 or v4
        regv = _registry_v4() if v4 else (_registry_v3() if v3 else None)
        want_family = FAMILY_F1ABCD if v4 else (FAMILY_F1ABC if v3 else reg.f1ab.FAMILY_F1AB)
        if v4:
            sel = regv.sel.parse_selection_envelope_v4(selection_text)
        else:
            sel = regv.sel.parse_selection_envelope_v3(selection_text) if v3 else reg.sel.parse_selection_envelope_v2(selection_text)
        if not sel.ok:
            raise SelectionRefusal(sel.code if sel.code in reg.decision.v2_codes.CODES_V2_REV2 else "field_invalid", cause="selection_parse")
        rec = None
        if mode == "activate":
            if v4:
                rec = regv.record.validate_record_text_v4(record_text)
            else:
                rec = regv.record.validate_record_text_v3(record_text) if v3 else reg.decision.validate_record_text_v2(record_text)
            if not rec.ok:
                raise SelectionRefusal(rec.code, cause="record")
        floors, stored_min = self._floors(conn)
        last_accepted = d.snapshot if d.snapshot is not None else None
        verify_fn = regv.verify.verify_selection_v4 if v4 else (regv.verify.verify_selection_v3 if v3 else reg.verify.verify_selection_v2)
        report = verify_fn(sel, context, trust, verifier, now, rec, None, min_trust_snapshot_version=stored_min, last_accepted_snapshot=last_accepted)
        if mode == "withdraw":
            # Registry reports an authenticated, in-scope, in-time withdrawal as the typed refusal `selection_withdrawn` (never ok=True).
            # Any other layer failing is a real refusal. A withdrawal that verified ok=True is inconsistent and refused.
            layers = getattr(report, "layers", None)
            accepted = (getattr(report, "ok", None) is False and getattr(report, "code", None) == "selection_withdrawn"
                        and getattr(report, "authenticated", False) is True and isinstance(layers, Mapping)
                        and all(v == reg.verify.PASS for k, v in layers.items() if k not in ("disposition", "record_binding")))
            if not accepted:
                code = getattr(report, "code", None)
                raise SelectionRefusal(code if code in reg.decision.v2_codes.CODES_V2_REV2 and code != "selection_withdrawn" else "field_invalid",
                                       cause="verification")
        elif getattr(report, "ok", False) is not True:
            raise SelectionRefusal(report.code if report.code in reg.decision.v2_codes.CODES_V2_REV2 else "field_invalid", cause="verification")
        stmt = sel.statement
        if (not isinstance(stmt, dict) or report.selection_digest != sel.statement_digest or report.participation != stmt.get("participation")
                or report.operationally_authorized is not False):
            raise SelectionRefusal("field_invalid", cause="report_inconsistent")
        want = "select" if mode == "activate" else "withdraw"
        sigs = sel.signatures
        if (stmt.get("disposition") != want or stmt.get("family") != want_family or not isinstance(sigs, (list, tuple)) or len(sigs) != 1
                or not isinstance(sigs[0], dict) or not _identifier_ok(sigs[0].get("key_id"))):
            raise SelectionRefusal("field_invalid", cause="statement_form")
        scope = dict(zip(SCOPE_NAMES, (stmt["consumer_id"], stmt["scope"]["tenant"], stmt["scope"]["institution"], stmt["scope"]["authority_domain"],
                                       stmt["operation"], stmt["family"])))
        sid, epoch, participation, key_id = stmt["selection_id"], stmt["epoch"], stmt["participation"], sigs[0]["key_id"]
        gsd, skd = governed_scope_digest(scope), scope_key_digest(sid, scope)
        ctx.update(gsd=gsd, skd=skd, sid=sid, epoch=epoch, digest=sel.statement_digest)
        # the presented snapshot must be exactly the synced one (so durable revocations are never behind the verification)
        fingerprint = reg.ident.trust_snapshot_fingerprint_from_verified(trust)
        if d.snapshot is None or d.snapshot != (trust.version, fingerprint):
            raise SelectionRefusal("trust_snapshot_invalid", cause="snapshot_not_synced")
        snap = dict(snapshot_version=d.snapshot[0], snapshot_fingerprint=d.snapshot[1])
        row = d.rows.get(gsd)
        ratchet_now = gsd in d.ratchet
        if key_id in d.revoked_keys:
            raise SelectionRefusal("issuer_key_revoked")
        if (sid, epoch) in d.revoked_pairs:
            raise SelectionRefusal("selection_revoked")
        if d.lineage.get(gsd, sid) != sid:
            raise SelectionRefusal("selection_conflict", cause="lineage_replacement_prohibited")
        if any(g != gsd and s == sid for g, s in d.lineage.items()):
            raise SelectionRefusal("selection_conflict", cause="selection_id_bound_to_other_scope")
        if mode == "activate" and gsd in d.non_participation:
            raise SelectionRefusal("participation_mismatch", cause="non_participation_determined")
        if mode == "withdraw":
            if row is None:
                raise SelectionRefusal("not_activated")
            if row["state"] != "active":
                raise SelectionRefusal("selection_revoked" if row["state"] == "revoked" else "selection_withdrawn")
        if epoch <= d.highwater.get(skd, 0) or epoch < floors.get(sid, 0):
            raise SelectionRefusal("selection_epoch_stale")
        sup = stmt.get("supersedes")
        current = None if row is None else {"selection_id": row["selection_id"], "epoch": row["selection_epoch"], "statement_digest": row["selection_digest"]}
        if (sup is None) != (current is None) or (sup is not None and {k: sup.get(k) for k in current} != current):
            raise SelectionRefusal("selection_supersession_invalid", cause="not_the_current_row")
        if ratchet_now and participation == "optional":
            raise SelectionRefusal("participation_mismatch", cause="scope_required")
        if mode == "activate":
            if composite and participation != "required":  # an optional composite selection could leave an unbound claim a caller-policy fallback
                raise SelectionRefusal("participation_mismatch", cause="f1abc_requires_required_participation")
            self._refuse_family_coexistence(conn, scope)
        counter = d.generation.get(gsd, d.baseline_floor)
        supersedes = None if current is None else {"selection_id": current["selection_id"], "epoch": current["epoch"], "selection_digest": current["statement_digest"]}
        if mode == "withdraw":
            gen = next_generation(counter, saturating=True)
            entry, h = self._append(conn, d, now, entry_type="withdraw", governed_scope_digest=gsd, scope_key_digest=skd, selection_id=sid,
                                    selection_epoch=epoch, selection_digest=sel.statement_digest, policy_generation=gen, supersedes=supersedes,
                                    participation=participation, state_before="active", state_after="withdrawn", ratchet_after=ratchet_now,
                                    issuer_key_id=key_id, **snap)
            conn.execute("UPDATE selection_activation_v1 SET selection_epoch=?, selection_digest=?, participation=?, activation_state='withdrawn', "
                         "transition_ref=?, policy_generation=?, last_sequence=? WHERE governed_scope_digest=?",
                         (epoch, sel.statement_digest, participation, h, gen, entry["activation_sequence"], gsd))
            self._upsert_highwater(conn, skd, epoch)
            self._upsert_generation(conn, gsd, gen)
            return TransitionResult("withdraw", entry["activation_sequence"], h, (h,), None, h, gen, False), self._finish(conn, d, now)
        payload = rec.record["payload"]
        try:
            record_policy_digest = _record_policy_digest(want_family, payload)
        except SelectionRefusal:
            raise
        except Exception as exc:
            raise SelectionRefusal("composition_record_invalid", cause="record_payload") from exc
        gen = next_generation(counter, saturating=False)
        new_ratchet = ratchet_now or participation == "required"
        entry, h = self._append(conn, d, now, entry_type="activate", governed_scope_digest=gsd, scope_key_digest=skd, selection_id=sid, selection_epoch=epoch,
                                selection_digest=sel.statement_digest, policy_generation=gen, supersedes=supersedes, participation=participation,
                                state_before=row["state"] if row else "none", state_after="active", ratchet_after=new_ratchet, issuer_key_id=key_id, **snap)
        eff = stmt["effective"]
        pin = stmt["record"]
        conn.execute(
            "INSERT INTO selection_activation_v1(governed_scope_digest, scope_key_digest, scope_json, selection_id, selection_epoch, selection_digest, "
            "participation, valid_from, valid_until, issuer_key_id, record_id, record_version, record_digest, record_policy_digest, activation_ref, "
            "transition_ref, policy_generation, activation_state, snapshot_version, snapshot_fingerprint, last_sequence, statement_json, record_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,'active',?,?,?,?,?) ON CONFLICT(governed_scope_digest) DO UPDATE SET "
            "scope_key_digest=excluded.scope_key_digest, selection_id=excluded.selection_id, selection_epoch=excluded.selection_epoch, "
            "selection_digest=excluded.selection_digest, participation=excluded.participation, valid_from=excluded.valid_from, "
            "valid_until=excluded.valid_until, issuer_key_id=excluded.issuer_key_id, record_id=excluded.record_id, record_version=excluded.record_version, "
            "record_digest=excluded.record_digest, record_policy_digest=excluded.record_policy_digest, activation_ref=excluded.activation_ref, "
            "transition_ref=NULL, policy_generation=excluded.policy_generation, activation_state='active', snapshot_version=excluded.snapshot_version, "
            "snapshot_fingerprint=excluded.snapshot_fingerprint, last_sequence=excluded.last_sequence, statement_json=excluded.statement_json, "
            "record_json=excluded.record_json",
            (gsd, skd, canonical_bytes(list(_scope_tuple(scope))).decode("ascii"), sid, epoch, sel.statement_digest, participation, eff["valid_from"],
             eff["valid_until"], key_id, pin["record_id"], pin["record_version"], pin["record_digest"], record_policy_digest, h, gen,
             d.snapshot[0], d.snapshot[1], entry["activation_sequence"], canonical_bytes(stmt).decode("ascii"), canonical_bytes(rec.record).decode("ascii")))
        conn.execute("INSERT OR IGNORE INTO selection_lineage_v1(governed_scope_digest, selection_id) VALUES(?,?)", (gsd, sid))
        self._upsert_highwater(conn, skd, epoch)
        self._upsert_generation(conn, gsd, gen)
        if new_ratchet and not ratchet_now:
            conn.execute("INSERT INTO selection_scope_required_v1(governed_scope_digest, set_sequence, scope_json) VALUES(?,?,?)",
                         (gsd, entry["activation_sequence"], canonical_bytes(list(_scope_tuple(scope))).decode("ascii")))
        return TransitionResult("activate", entry["activation_sequence"], h, (h,), h, None, gen, False), self._finish(conn, d, now)

    @staticmethod
    def _refuse_family_coexistence(conn: sqlite3.Connection, scope: Mapping[str, str]) -> None:
        """M2 PR-2 coexistence rule: two Registry families must never independently authorize the same consumer, tenant, institution, authority
        domain and operation. An activation is refused (`selection_conflict`) while an `active` row of ANOTHER family holds that tuple, so the older
        family must be withdrawn (or revoked) first. The gap between the withdrawal and the new activation fails closed (the withdrawn row is
        terminal and, for f1abc, the durable ratchet refuses unbound claims). Expiry does not release a family: only `withdrawn`/`revoked` does."""
        mine = tuple(scope[n] for n in SCOPE_NAMES[:5])
        for (scope_json,) in conn.execute("SELECT scope_json FROM selection_activation_v1 WHERE activation_state='active'"):
            tup = json.loads(scope_json)
            if tuple(tup[:5]) == mine and tup[5] != scope["family"]:
                raise SelectionRefusal("selection_conflict", cause="family_coexistence")

    # -- effect-time guard (V05) -----------------------------------------------------------------------------------------------
    def check_effect(self, conn: sqlite3.Connection, claim: Any, now: datetime, policy_effective_digest: Optional[str],
                     policy_effective_sets: Optional[dict] = None, *, pin_scope: Optional[dict] = None,
                     policy_effective_limits: Optional[dict] = None, policy_effective_targets: Optional[dict] = None) -> dict:
        """The lock-time selection guard (ADR 7, contract 7.4). Called by `execute_claim_atomic` INSIDE its `BEGIN IMMEDIATE`, after the lock-time
        clock and before the effect insert, on the SAME connection. Reads only local rows (and the small local anchor file); no network, registry call
        or subprocess. Returns bounded evidence on success, raises a typed `SelectionRefusal` (a `PermissionError`) otherwise. It writes nothing.

        F-01 (r08-v2 Amendment 3): for a bound claim the five sets of the policy the executor evaluates (`policy_effective_sets`, derived by the
        executor from the same policy object as `policy_effective_digest`) must each be a subset of the corresponding set of the authenticated
        activated record payload, so the claim supplier is not the only enforcement point of Registry narrowing."""
        status, d = self._verify(conn)
        if status.state != "known":
            raise SelectionRefusal("store_unknown", "HOLD", cause=status.cause)
        binding = getattr(claim, "policy_binding", None)
        head = {"log_head_sequence": d.head_sequence, "log_head_hash": d.head_hash}
        if pin_scope is not None and binding is None:
            # F-A1: a pin-required destination never reaches the legacy (unbound) path.
            raise SelectionRefusal("policy_identity_mismatch", cause="pin_required_binding_absent")
        if binding is None:
            return self._check_unbound(conn, claim, d, now, head)
        try:
            from agent_control_plane import canonical_policy_binding as cpb
        except Exception as exc:
            raise SelectionRefusal("verifier_unavailable", cause="binding_validator_unavailable") from exc
        defect = cpb.binding_defect(binding)
        if defect is not None:
            raise SelectionRefusal("field_invalid", cause=f"binding:{defect}")
        sc = binding["scope"]
        scope = {"consumer_id": binding["consumer_id"], "tenant": sc["tenant"], "institution": sc["institution"],
                 "authority_domain": sc["authority_domain"], "operation": binding["operation"], "family": binding["family"]}
        if pin_scope is not None:
            # F-A1 (Amendment 4 section 5 step 6): the governed scope is built from the AUTHENTICATED PIN, not from the claim. Only `family`
            # (and `selection_id` below) come from the digest-verified binding. Every later check is unchanged.
            if type(pin_scope) is not dict or set(pin_scope) != {"consumer_id", "tenant", "institution", "authority_domain", "operation"}:
                raise SelectionRefusal("field_invalid", cause="pin_scope")
            scope = {**pin_scope, "family": binding["family"]}
        gsd, sid, epoch = governed_scope_digest(scope), binding["selection_id"], binding["selection_epoch"]
        skd = scope_key_digest(sid, scope)
        row = d.rows.get(gsd)
        if row is None:
            raise SelectionRefusal("not_activated", cause="no_row_for_scope")
        if d.lineage.get(gsd) != sid or row["scope_key_digest"] != skd:
            raise SelectionRefusal("selection_conflict", cause="lineage_binding")
        act = conn.execute("SELECT valid_from, valid_until, record_id, record_version, record_digest, record_policy_digest, record_json "
                           "FROM selection_activation_v1 WHERE governed_scope_digest=?", (gsd,)).fetchone()
        if row["state"] == "withdrawn":
            raise SelectionRefusal("selection_withdrawn")
        if row["state"] == "revoked":
            raise SelectionRefusal("issuer_key_revoked" if row["issuer_key_id"] in d.revoked_keys else "selection_revoked")
        # carried activation_ref: the chain gives the digest an order (ADR 9.2)
        # The claim carries NO separate generation member (ADR 7.2): `activation_ref` fixes the policy_generation (ADR 4.2). The comparison is
        # therefore made on the generation the carried reference fixed, read from the log entry it names, against the Runtime-owned counter,
        # all inside this transaction. It is never inferred from selection_epoch.
        chain = {h: (kind, gen) for kind, h, gen in self._chain_refs(conn, gsd)}
        counter = d.generation.get(gsd)
        if binding["activation_ref"] != row["activation_ref"]:
            carried = chain.get(binding["activation_ref"])
            if carried is not None and carried[0] == "activate" and type(carried[1]) is int and type(counter) is int and carried[1] < counter:
                raise SelectionRefusal("selection_epoch_stale", "REFUSE_STALE", cause="stale_policy_generation")
            if carried is not None:
                raise SelectionRefusal("selection_epoch_stale", "REFUSE_STALE", cause="earlier_activation")
            raise SelectionRefusal("policy_identity_mismatch", cause="activation_ref")
        fixed = chain.get(row["activation_ref"])
        if fixed is None or fixed[0] != "activate" or type(fixed[1]) is not int or fixed[1] != counter or fixed[1] != row["generation"]:
            raise SelectionRefusal("store_unknown", "HOLD", cause="policy_generation_inconsistent")
        if epoch != row["selection_epoch"]:
            raise SelectionRefusal("selection_epoch_stale" if epoch < row["selection_epoch"] else "policy_identity_mismatch",
                                   cause="selection_epoch")
        if (binding["selection_digest"] != row["selection_digest"]):
            raise SelectionRefusal("policy_identity_mismatch", cause="selection_digest")
        if (binding["record_id"], binding["record_version"], binding["record_digest"]) != (act["record_id"], act["record_version"], act["record_digest"]):
            raise SelectionRefusal("record_identity_mismatch")
        floors, floor_version = self._floors(conn)
        if epoch != d.highwater.get(row["scope_key_digest"]) or epoch < floors.get(sid, 0):
            raise SelectionRefusal("selection_epoch_stale", "REFUSE_STALE", cause="high_water_or_floor")
        if (sid, epoch) in d.revoked_pairs:
            raise SelectionRefusal("selection_revoked")
        if row["issuer_key_id"] in d.revoked_keys:
            raise SelectionRefusal("issuer_key_revoked")
        valid_from, valid_until = self._ts(act["valid_from"]), self._ts(act["valid_until"])
        if now < valid_from:
            raise SelectionRefusal("selection_not_yet_effective")
        if now >= valid_until:
            raise SelectionRefusal("selection_expired")
        if row["participation"] != "required" and gsd in d.ratchet:
            raise SelectionRefusal("participation_mismatch", cause="scope_required")
        snap = self._snapshot_row(conn)
        if snap is None or d.snapshot is None:
            raise SelectionRefusal("store_unknown", "HOLD", cause="no_trust_snapshot")
        version, fingerprint = binding["trust_snapshot_version"], binding["trust_snapshot_fingerprint"]
        if version > d.snapshot[0]:
            raise SelectionRefusal("trust_snapshot_version_stale", "HOLD", cause="carried_snapshot_ahead_of_durable")
        if version < max(floor_version or 0, 1) or dict(self._snapshot_versions(conn)).get(version) != fingerprint:
            raise SelectionRefusal("trust_snapshot_version_stale", "REFUSE_STALE")
        if now >= self._ts(snap["valid_until"]):
            raise SelectionRefusal("trust_snapshot_expired", "HOLD")
        composite = binding["family"] == FAMILY_F1ABC
        composite_d = binding["family"] == FAMILY_F1ABCD
        if composite_d:
            # M3: the identity is the nine-member digest of the policy the executor will evaluate (sets, ceilings, target prefixes and match mode),
            # computed here from the same policy object's values.
            policy_effective_digest = self._f1abcd_policy_digest(policy_effective_sets, policy_effective_limits, policy_effective_targets)
        elif composite:
            # M2 PR-3: the identity is the seven-member digest of the policy the executor will evaluate (its five sets AND its two ceilings),
            # computed here from the same policy object's values. The f1ab-shaped `policy_effective_digest` is not used for this family.
            policy_effective_digest = self._f1abc_policy_digest(policy_effective_sets, policy_effective_limits)
        if policy_effective_digest is None:
            raise SelectionRefusal("verifier_unavailable", cause="policy_digest_unavailable")
        if policy_effective_digest != binding["effective_policy_digest"]:
            raise SelectionRefusal("policy_identity_mismatch", cause="effective_policy_digest")
        if composite_d:
            self._check_policy_containment_f1abcd(act, policy_effective_digest, policy_effective_sets, policy_effective_limits, policy_effective_targets)
        elif composite:
            self._check_policy_containment_f1abc(act, policy_effective_digest, policy_effective_sets, policy_effective_limits)
        else:
            self._check_policy_containment(act, policy_effective_digest, policy_effective_sets)
        return {"result": "binding_verified", "applied": True, "selection_id": sid, "selection_epoch": epoch, "activation_ref": row["activation_ref"],
                "policy_generation": d.generation.get(gsd), "participation": row["participation"], "scope_required": gsd in d.ratchet,
                "trust_snapshot_version": version, "trust_snapshot_fingerprint": fingerprint, "effective_policy_digest": policy_effective_digest,
                "checked_at": _utc(now), **head, "independent_attestation": False}

    def _check_policy_containment(self, act: sqlite3.Row, policy_effective_digest: str, policy_effective_sets: Optional[dict]) -> None:
        """F-01: every set of the executor's effective policy must be inside the same set of the activated Registry record. Reads only the row the
        guard just matched (already verified by `_verify` in this transaction); the Registry sets come from that authenticated record payload and
        never from the claim or the executor. Local data only: no I/O."""
        if not isinstance(policy_effective_sets, dict) or set(policy_effective_sets) != set(sa_fields()):
            raise SelectionRefusal("verifier_unavailable", cause="policy_sets_unavailable")
        try:
            reg = _registry()
            executor = {k: _clean_set(policy_effective_sets[k]) for k in sa_fields()}
            executor_digest = reg.ident.effective_policy_digest(reg.f1ab.FAMILY_F1AB, {k: sorted(executor[k]) for k in sa_fields()})
        except SelectionRefusal:
            raise
        except Exception as exc:
            raise SelectionRefusal("policy_identity_mismatch", cause="policy_sets_malformed") from exc
        if executor_digest != policy_effective_digest:
            # The sets handed to the guard are not the policy whose digest the executor bound: never accepted.
            raise SelectionRefusal("policy_identity_mismatch", cause="policy_sets_digest")
        # The Registry side: re-authenticate the payload of THIS row before using it (defence in depth over `_verify`).
        try:
            record_json = act["record_json"]
            rec = json.loads(record_json)
            body = {k: v for k, v in rec.items() if k != "record_digest"}
            if (canonical_bytes(rec).decode("ascii") != record_json or rec.get("record_digest") != act["record_digest"]
                    or "sha256:" + hashlib.sha256(canonical_bytes(body)).hexdigest() != act["record_digest"]):
                raise ValueError("record bytes")
            payload = rec["payload"]
            registry_sets = {k: _clean_set(payload[k]) for k in sa_fields()}
            if reg.ident.effective_policy_digest(reg.f1ab.FAMILY_F1AB, {k: sorted(registry_sets[k]) for k in sa_fields()}) != act["record_policy_digest"]:
                raise ValueError("record policy digest")
        except Exception as exc:
            raise SelectionRefusal("store_unknown", "HOLD", cause="record_payload_unavailable") from exc
        for field in sa_fields():
            if not executor[field] <= registry_sets[field]:
                raise SelectionRefusal("policy_identity_mismatch", cause=f"policy_not_subset:{field}")

    @staticmethod
    def _f1abc_limits(limits: Any) -> dict:
        """The executor policy's two ceilings, exactly as held: an exact `int` (never a float, bool or subclass) inside the Registry range. No coercion,
        rounding or clamping: anything else is refused. `max_effects` of the executor policy is at least 1 (a policy cannot be built for deny-all)."""
        if not isinstance(limits, dict) or set(limits) != set(F1ABC_CEILINGS):
            raise SelectionRefusal("verifier_unavailable", cause="policy_limits_unavailable")
        amount, effects = limits["max_amount"], limits["max_effects"]
        if type(amount) is not int or type(effects) is not int:
            raise SelectionRefusal("composition_caller_invalid", cause="policy_limits_not_exact_integers")
        if not 0 <= amount <= F1ABC_AMOUNT_MAX or not 1 <= effects <= F1ABC_EFFECTS_MAX:
            raise SelectionRefusal("composition_caller_invalid", cause="policy_limits_out_of_range")
        return {"max_amount": amount, "max_effects": effects}

    def _f1abc_policy_digest(self, sets: Any, limits: Any) -> str:
        if not isinstance(sets, dict) or set(sets) != set(sa_fields()):
            raise SelectionRefusal("verifier_unavailable", cause="policy_sets_unavailable")
        lim = self._f1abc_limits(limits)
        try:
            regv = _registry_v3()
            return regv.ident.effective_policy_digest(
                FAMILY_F1ABC, {**{k: sorted(_clean_set(sets[k])) for k in sa_fields()}, **lim})
        except SelectionRefusal:
            raise
        except Exception as exc:
            raise SelectionRefusal("policy_identity_mismatch", cause="policy_sets_malformed") from exc

    def _check_policy_containment_f1abc(self, act: sqlite3.Row, policy_effective_digest: str, sets: dict, limits: dict) -> None:
        """M2 PR-3: all seven members of the executor's effective policy must be inside the authenticated activated r08-v3 record: each of the five
        sets a subset, `max_amount <= record max_amount`, `max_effects <= record max_effects` (record `max_effects` of 0 admits nothing: deny-all).
        The Registry side is re-authenticated from THIS row (defence in depth over `_verify`) and never taken from the claim or the executor."""
        lim = self._f1abc_limits(limits)
        executor = {k: _clean_set(sets[k]) for k in sa_fields()}
        try:
            record_json = act["record_json"]
            rec = json.loads(record_json)
            body = {k: v for k, v in rec.items() if k != "record_digest"}
            if (canonical_bytes(rec).decode("ascii") != record_json or rec.get("record_digest") != act["record_digest"]
                    or "sha256:" + hashlib.sha256(canonical_bytes(body)).hexdigest() != act["record_digest"]
                    or rec.get("contract_version") != CONTRACT_V3 or rec.get("family") != FAMILY_F1ABC):
                raise ValueError("record bytes")
            payload = rec["payload"]
            if _record_policy_digest(FAMILY_F1ABC, payload) != act["record_policy_digest"]:
                raise ValueError("record policy digest")
            registry_sets = {k: _clean_set(payload[k]) for k in sa_fields()}
            ceilings = {k: payload[k] for k in F1ABC_CEILINGS}
            if any(type(v) is not int for v in ceilings.values()):
                raise ValueError("record ceilings")
        except Exception as exc:
            raise SelectionRefusal("store_unknown", "HOLD", cause="record_payload_unavailable") from exc
        for field in sa_fields():
            if not executor[field] <= registry_sets[field]:
                raise SelectionRefusal("policy_identity_mismatch", cause=f"policy_not_subset:{field}")
        for field in F1ABC_CEILINGS:
            if lim[field] > ceilings[field]:
                raise SelectionRefusal("policy_identity_mismatch", cause=f"policy_exceeds_ceiling:{field}")

    @staticmethod
    def _f1abcd_targets(targets: Any) -> dict:
        """The executor policy's target members exactly as held: a non-empty tuple/list of non-empty `str` prefixes and an exact `str` mode. Nothing is
        coerced or defaulted; anything else is refused."""
        if not isinstance(targets, dict) or set(targets) != set(F1ABCD_TARGETS):
            raise SelectionRefusal("verifier_unavailable", cause="policy_targets_unavailable")
        prefixes, mode = targets["allowed_target_prefixes"], targets["target_match"]
        if type(prefixes) not in (list, tuple) or not prefixes or any(type(x) is not str or not x for x in prefixes) or type(mode) is not str:
            raise SelectionRefusal("composition_caller_invalid", cause="policy_targets_malformed")
        return {"allowed_target_prefixes": sorted(set(prefixes)), "target_match": mode}

    def _f1abcd_policy_digest(self, sets: Any, limits: Any, targets: Any) -> str:
        if not isinstance(sets, dict) or set(sets) != set(sa_fields()):
            raise SelectionRefusal("verifier_unavailable", cause="policy_sets_unavailable")
        lim = self._f1abc_limits(limits)
        tgt = self._f1abcd_targets(targets)
        try:
            regv = _registry_v4()
            return regv.ident.effective_policy_digest(FAMILY_F1ABCD, {**{k: sorted(_clean_set(sets[k])) for k in sa_fields()}, **lim, **tgt})
        except SelectionRefusal:
            raise
        except Exception as exc:
            raise SelectionRefusal("policy_identity_mismatch", cause="policy_targets_malformed") from exc

    def _check_policy_containment_f1abcd(self, act: sqlite3.Row, policy_effective_digest: str, sets: dict, limits: dict, targets: dict) -> None:
        """M3: all nine members of the executor's effective policy must be inside the authenticated activated r08-v4 record: the five sets by subset,
        the two ceilings by `<=`, and the accepted target set by CONTAINMENT: every executor prefix, under the executor's mode, must lie inside the
        accepted set of some record prefix under the record's mode (Registry `v4_f1abcd.contained`, the exact predicate verified exhaustively against
        the evaluator rule). An executor mode weaker than the record's can only pass when each prefix is still contained. The Registry side is
        re-authenticated from THIS row and never taken from the claim or the executor; a record with no prefixes admits nothing (deny-all)."""
        lim = self._f1abc_limits(limits)
        tgt = self._f1abcd_targets(targets)
        executor = {k: _clean_set(sets[k]) for k in sa_fields()}
        try:
            record_json = act["record_json"]
            rec = json.loads(record_json)
            body = {k: v for k, v in rec.items() if k != "record_digest"}
            if (canonical_bytes(rec).decode("ascii") != record_json or rec.get("record_digest") != act["record_digest"]
                    or "sha256:" + hashlib.sha256(canonical_bytes(body)).hexdigest() != act["record_digest"]
                    or rec.get("contract_version") != CONTRACT_V4 or rec.get("family") != FAMILY_F1ABCD):
                raise ValueError("record bytes")
            payload = rec["payload"]
            if _record_policy_digest(FAMILY_F1ABCD, payload) != act["record_policy_digest"]:
                raise ValueError("record policy digest")
            registry_sets = {k: _clean_set(payload[k]) for k in sa_fields()}
            ceilings = {k: payload[k] for k in F1ABC_CEILINGS}
            if any(type(v) is not int for v in ceilings.values()):
                raise ValueError("record ceilings")
            record_prefixes, record_mode = payload["allowed_target_prefixes"], payload["target_match"]
            if type(record_prefixes) is not list or type(record_mode) is not str:
                raise ValueError("record targets")
            contained = _registry_v4().fam.contained
            if record_mode not in _registry_v4().fam.MODES:
                raise ValueError("record mode")
        except Exception as exc:
            raise SelectionRefusal("store_unknown", "HOLD", cause="record_payload_unavailable") from exc
        for field in sa_fields():
            if not executor[field] <= registry_sets[field]:
                raise SelectionRefusal("policy_identity_mismatch", cause=f"policy_not_subset:{field}")
        for field in F1ABC_CEILINGS:
            if lim[field] > ceilings[field]:
                raise SelectionRefusal("policy_identity_mismatch", cause=f"policy_exceeds_ceiling:{field}")
        if tgt["target_match"] not in _registry_v4().fam.MODES:
            raise SelectionRefusal("composition_caller_invalid", cause="policy_target_mode_invalid")
        for entry in tgt["allowed_target_prefixes"]:
            if not any(contained(tgt["target_match"], entry, record_mode, r) for r in record_prefixes):
                raise SelectionRefusal("policy_identity_mismatch", cause="policy_targets_not_contained")

    def _check_unbound(self, conn: sqlite3.Connection, claim: Any, d: Derived, now: datetime, head: dict) -> dict:
        """Contract 7.4 line 8: an omitted binding is never an intentionally absent optional policy."""
        tenant, institution, domain = getattr(claim, "tenant_id", None), claim.institution_id, claim.authority_domain
        scopes = {}
        for gsd, scope_json in conn.execute("SELECT governed_scope_digest, scope_json FROM selection_scope_required_v1"):
            scopes[gsd] = json.loads(scope_json)
        windows = {}
        for r in conn.execute("SELECT governed_scope_digest, scope_json, valid_from, valid_until FROM selection_activation_v1"):
            scopes.setdefault(r["governed_scope_digest"], json.loads(r["scope_json"]))
            windows[r["governed_scope_digest"]] = (self._ts(r["valid_from"]), self._ts(r["valid_until"]))
        reasons = []
        for gsd, tup in sorted(scopes.items()):
            if tup[2] != institution or tup[3] != domain or (tenant is not None and tup[1] != tenant):
                continue  # the claim carries no consumer, operation or family: every scope inside its tenant/institution/domain may apply
            row = d.rows.get(gsd)
            if gsd in d.ratchet:
                raise SelectionRefusal("selection_missing", cause="scope_required_without_binding")
            if row["state"] == "revoked":
                raise SelectionRefusal("selection_missing", cause="revoked_scope_without_binding")
            if row["state"] == "active" and now < windows[gsd][1]:  # not yet effective or inside the window: only a PAST valid_until may fall back
                raise SelectionRefusal("selection_missing", cause="active_selection_without_binding")
            reasons.append("withdrawn" if row["state"] == "withdrawn" else "expired")
        reason = "withdrawn" if "withdrawn" in reasons else "expired" if reasons else "never_activated"
        return {"result": "fallback_caller_policy", "applied": False, "registry_code": "selection_missing" if reasons else "not_activated",
                "fallback": "caller_policy", "fallback_reason": reason, "checked_at": _utc(now), **head, "independent_attestation": False}

    @staticmethod
    def _ts(value: str) -> datetime:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

    def _chain_refs(self, conn: sqlite3.Connection, gsd: str) -> list:
        out = []
        for raw, h in conn.execute("SELECT entry_json, entry_hash FROM selection_log_v1 ORDER BY activation_sequence"):
            e = json.loads(raw)
            if e["governed_scope_digest"] == gsd and e["entry_type"] in ("activate", "withdraw"):
                out.append((e["entry_type"], h, e["policy_generation"]))
        return out

    def _snapshot_versions(self, conn: sqlite3.Connection) -> list:
        out = []
        for (raw,) in conn.execute("SELECT entry_json FROM selection_log_v1 ORDER BY activation_sequence"):
            e = json.loads(raw)
            if e["entry_type"] == "sync" and e["cause"] == "none":
                out.append((e["snapshot_version"], e["snapshot_fingerprint"]))
        return out

    # -- read interface -------------------------------------------------------------------------------------------------------
    def read_state(self, governed_scope: Any) -> SelectionState:
        """Verified, immutable, read-only view of one governed scope. Raises `store_unknown` (HOLD) when the store cannot be verified."""
        scope = _scope_tuple(governed_scope)
        gsd = governed_scope_digest(governed_scope)
        conn = self._open()
        try:
            conn.execute("BEGIN")
            d = self._require_known(conn)
            row = conn.execute("SELECT * FROM selection_activation_v1 WHERE governed_scope_digest=?", (gsd,)).fetchone()
            store_id = conn.execute("SELECT store_id FROM selection_store_meta_v1").fetchone()[0]
            conn.execute("COMMIT")
        finally:
            conn.close()
        common = dict(store_state="known", store_id=store_id, governed_scope_digest=gsd, scope=scope, scope_required=gsd in d.ratchet,
                      non_participation_determined=gsd in d.non_participation, log_head_sequence=d.head_sequence, log_head_hash=d.head_hash,
                      reasons_registry_label=REASONS_LABEL, reasons_registry_sha256=REASONS_SHA256)
        if row is None:
            return SelectionState(status="not_activated", scope_key_digest=None, selection_id=None, selection_epoch=None, selection_digest=None,
                                  participation=None, valid_from=None, valid_until=None, issuer_key_id=None, record_id=None, record_version=None,
                                  record_digest=None, effective_policy_digest=None, activation_ref=None, transition_ref=None,
                                  policy_generation=d.generation.get(gsd), trust_snapshot_version=None, trust_snapshot_fingerprint=None, **common)
        return SelectionState(status=row["activation_state"], scope_key_digest=row["scope_key_digest"], selection_id=row["selection_id"],
                              selection_epoch=row["selection_epoch"], selection_digest=row["selection_digest"], participation=row["participation"],
                              valid_from=row["valid_from"], valid_until=row["valid_until"], issuer_key_id=row["issuer_key_id"], record_id=row["record_id"],
                              record_version=row["record_version"], record_digest=row["record_digest"], effective_policy_digest=row["record_policy_digest"],
                              activation_ref=row["activation_ref"], transition_ref=row["transition_ref"], policy_generation=row["policy_generation"],
                              trust_snapshot_version=row["snapshot_version"], trust_snapshot_fingerprint=row["snapshot_fingerprint"], **common)


def sa_fields() -> tuple:
    return ("allowed_actions", "allowed_adapters", "allowed_units", "allowed_institutions", "allowed_authority_domains")


def _clean_set(value: Any) -> frozenset:
    """A policy set as a frozenset of non-empty strings; anything else is malformed. The Registry digest function applies the full value rules."""
    if not isinstance(value, (set, frozenset, list, tuple)) or any(type(m) is not str or not m for m in value):
        raise ValueError("policy set malformed")
    return frozenset(value)


def policy_sets_of(policy: Any) -> Optional[dict]:
    """The five sets of the policy an executor will evaluate, as sorted lists (None when they cannot be read). Derived from the same policy
    object as `policy_digest_of`; the guard checks that the sets reproduce the digest, so the two cannot diverge."""
    try:
        return {k: sorted(getattr(policy, k)) for k in sa_fields()}
    except Exception:
        return None


def policy_limits_of(policy: Any) -> Optional[dict]:
    """The two numeric ceilings of the policy an executor will evaluate, EXACTLY as held (no coercion; the f1abc guard refuses anything that is
    not an exact in-range integer). None when they cannot be read. Grants nothing."""
    try:
        return {"max_amount": policy.max_amount, "max_effects": policy.max_effects}
    except Exception:
        return None


def policy_targets_of(policy: Any) -> Optional[dict]:
    """The target members of the policy an executor will evaluate, EXACTLY as held (None when they cannot be read). The f1abcd guard refuses
    anything that is not a non-empty prefix tuple and an exact mode `str`. Grants nothing."""
    try:
        return {"allowed_target_prefixes": policy.allowed_target_prefixes, "target_match": policy.target_match}
    except Exception:
        return None


def policy_digest_of(policy: Any) -> Optional[str]:
    """Registry effective-policy digest of the five sets an executor policy carries (None when it cannot be computed). Used by the executor to
    bind the policy it will evaluate to the claim-carried `effective_policy_digest`; it grants nothing."""
    try:
        reg = _registry()
        return reg.ident.effective_policy_digest(reg.f1ab.FAMILY_F1AB, {k: sorted(getattr(policy, k)) for k in sa_fields()})
    except Exception:
        return None
