"""Runtime-side guard for the trusted authorization pin (Registry r08-v2 Amendment 4, F-A1 implementation, worker C).

STATUS: BUILDER_TESTED candidate component. Non-authorizing. It does not claim certification, qualification or production readiness
(RELEASE / PUBLICATION / PRODUCTION: HOLD).

What this module is: the READ-ONLY half of the pin protocol. It reads an installer-owned pin store (SQLite, opened with URI ``mode=ro``), an
installer-owned anchor file and a per-decision advisory fence file, and enforces them for one claim inside the destination's existing atomic
effect transaction. It implements NO function that creates, changes, removes or revokes a pin, an anchor or a fence file, and it opens none of
them for writing. Isolation between the claim supplier, the installer and this executor is provided ONLY by the deployment's OS identities and
file permissions (Amendment 4 P-1 to P-5); an ordinary Python function boundary is not an isolation boundary and this module does not claim
one. A supplier that can run arbitrary code as the executor identity is out of scope (Amendment 4 section 2).

It adds no second authorization engine: it compares claim members with the pin, then hands the PIN-derived governed scope to the unchanged
Registry selection guard (``SelectionAuthorityStore.check_effect(..., pin_scope=...)``). All formats and the section 7 decision table come from
``engine.authorization_pin_format`` (frozen); nothing is re-implemented here.

Expected pin store layout (the installer owns it; this reader depends on exactly these names):
  table ``authorization_pin_v1``: columns ``pin_epoch`` (INTEGER), ``decision_id`` (TEXT), ``row_json`` (TEXT, the canonical row of the format
  module); table ``authorization_pin_meta_v1``: columns ``key`` and ``value`` with keys ``profile`` and ``pin_store_id``.
"""
from __future__ import annotations

import dataclasses
import math
import os
import sqlite3
import stat
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from engine import authorization_pin_format as fmt
from engine.registry_selection_authority import SelectionRefusal

try:  # POSIX advisory locks; a platform without them cannot take the fence and therefore HOLDs.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

_MAX_ANCHOR_BYTES = 1 << 20
_SQLITE_TIMEOUT_SECONDS = 2.0


@dataclasses.dataclass(frozen=True)
class AuthorizationPinConfig:
    """Host configuration of a pin-required destination. Fixed at destination construction; no claim or caller value can set or redirect it."""
    pin_store_path: Path
    fence_dir: Path
    anchor_path: Path
    pin_store_id: str
    fence_deadline_seconds: float = 2.0

    def __post_init__(self) -> None:
        for name in ("pin_store_path", "fence_dir", "anchor_path"):
            value = getattr(self, name)
            if not isinstance(value, (str, Path)) or not str(value):
                raise ValueError(f"{name} must be a non-empty path")
            object.__setattr__(self, name, Path(value))
        if type(self.pin_store_id) is not str or not self.pin_store_id:
            raise ValueError("pin_store_id must be a non-empty string")
        deadline = self.fence_deadline_seconds
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline) or deadline <= 0:
            raise ValueError("fence_deadline_seconds must be a positive finite number")


def _hold(cause: str) -> SelectionRefusal:
    return SelectionRefusal(fmt.CODE_UNKNOWN, "HOLD", cause=cause)


def _refuse(cause: str) -> SelectionRefusal:
    return SelectionRefusal(fmt.CODE_MISMATCH, "REFUSE", cause=cause)


def _fence_unavailable() -> SelectionRefusal:
    return SelectionRefusal(fmt.CODE_UNAVAILABLE, "HOLD", cause=fmt.CAUSE_FENCE_UNAVAILABLE)


@dataclasses.dataclass(frozen=True)
class PinLookup:
    anchor: dict
    row: Optional[dict]          # the current row of the decision (highest pin_epoch); None when the verified store has no row for it


class PinReader:
    """Read-only access to the pin store and the anchor. Every failure maps to HOLD ``store_unknown`` with an Amendment 4 cause."""

    def __init__(self, config: AuthorizationPinConfig):
        self._config = config

    # --- anchor -----------------------------------------------------------------------------------------------------------
    def read_anchor(self) -> Optional[dict]:
        """The decoded anchor, or None when it is absent, unreadable or malformed (the caller treats None as ``pin_anchor_unavailable``)."""
        try:
            fd = os.open(self._config.anchor_path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            return None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            data = os.read(fd, _MAX_ANCHOR_BYTES + 1)
        except OSError:
            return None
        finally:
            os.close(fd)
        if len(data) > _MAX_ANCHOR_BYTES:
            return None
        try:
            return fmt.anchor_decode(data)
        except fmt.PinFormatError:
            return None

    # --- pin store --------------------------------------------------------------------------------------------------------
    def _open(self) -> sqlite3.Connection:
        """Read-only connection (``mode=ro``): this destination holds no write handle to the pin store."""
        uri = self._config.pin_store_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=_SQLITE_TIMEOUT_SECONDS, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _decode(record: sqlite3.Row) -> dict:
        row = fmt.decode_row(record["row_json"])
        if row["pin_epoch"] != record["pin_epoch"] or row["decision_id"] != record["decision_id"]:
            raise fmt.PinFormatError("row:column_mismatch")
        return row

    def _head(self, conn: sqlite3.Connection) -> fmt.Head:
        record = conn.execute(f"SELECT pin_epoch, decision_id, row_json FROM {fmt.PIN_TABLE} ORDER BY pin_epoch DESC LIMIT 1").fetchone()
        if record is None:
            return fmt.EMPTY_HEAD
        row = self._decode(record)
        return fmt.Head(row["pin_epoch"], row["pin_digest"], row["decision_id"], row["status"])

    def _rows_from(self, conn: sqlite3.Connection, epoch: int) -> list[dict]:
        return [self._decode(r) for r in conn.execute(
            f"SELECT pin_epoch, decision_id, row_json FROM {fmt.PIN_TABLE} WHERE pin_epoch >= ? ORDER BY pin_epoch ASC", (epoch,))]

    def _rows_for(self, conn: sqlite3.Connection, decision_id: str) -> list[dict]:
        return [self._decode(r) for r in conn.execute(
            f"SELECT pin_epoch, decision_id, row_json FROM {fmt.PIN_TABLE} WHERE decision_id = ? ORDER BY pin_epoch ASC", (decision_id,))]

    def _run(self, fn):
        """Run ``fn(conn)`` on a fresh read-only connection inside one read snapshot; map every storage or format failure to HOLD."""
        conn = None
        try:
            conn = self._open()
            conn.execute("BEGIN")
            return fn(conn)
        except SelectionRefusal:
            raise
        except (sqlite3.Error, OSError, fmt.PinFormatError, KeyError, TypeError, ValueError) as exc:
            raise _hold(fmt.CAUSE_STORE_UNREADABLE) from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass

    def read_head(self) -> fmt.Head:
        return self._run(self._head)

    def rows_from(self, epoch: int) -> list[dict]:
        return self._run(lambda conn: self._rows_from(conn, epoch))

    def rows_for(self, decision_id: str) -> list[dict]:
        return self._run(lambda conn: self._rows_for(conn, decision_id))

    # --- admission --------------------------------------------------------------------------------------------------------
    def barrier(self, decision_id: str) -> None:
        """Anchor-only admission read (Amendment 4 section 6.2 steps 0 and 2): HOLD when a terminal intent covers ``decision_id``.

        Unreadable anchors are NOT decided here (the full verification inside the transaction HOLDs every decision)."""
        anchor = self.read_anchor()
        if anchor is None:
            return
        intent = anchor["intent"]
        if intent is not None and intent["action"] == "terminal" and intent["decision_id"] == decision_id:
            raise _hold(fmt.CAUSE_PENDING)

    def lookup(self, decision_id: str) -> PinLookup:
        """Verify anchor and store (section 5 step 1) and return the decision's current row (steps 2 and 3).

        meta -> ``anchor_admission`` -> ``chain_defect`` over ``rows_from(max(committed, 1))`` -> ``decision_state(rows_for(decision_id))``."""
        anchor = self.read_anchor()
        if anchor is None:
            raise _hold(fmt.CAUSE_ANCHOR_UNAVAILABLE)
        if anchor["pin_store_id"] != self._config.pin_store_id:
            raise _hold(fmt.CAUSE_ANCHOR_INCONSISTENT)

        def verify(conn: sqlite3.Connection) -> PinLookup:
            meta = {r["key"]: r["value"] for r in conn.execute(f"SELECT key, value FROM {fmt.META_TABLE}")}
            if meta.get("profile") != fmt.PIN_PROFILE:
                raise _hold(fmt.CAUSE_STORE_UNREADABLE)
            if meta.get("pin_store_id") != self._config.pin_store_id:
                raise _hold(fmt.CAUSE_ANCHOR_INCONSISTENT)
            head = self._head(conn)
            admission = fmt.anchor_admission(anchor, head, decision_id)
            if admission.verdict != "accept":
                raise _hold(admission.cause or fmt.CAUSE_ANCHOR_INCONSISTENT)
            rows = self._rows_from(conn, max(anchor["committed_epoch"], 1))
            if fmt.chain_defect(rows, anchor) is not None or (rows[-1]["pin_epoch"] if rows else 0) != head.epoch:
                raise _hold(fmt.CAUSE_CHAIN_BROKEN)
            current, conflict = fmt.decision_state(self._rows_for(conn, decision_id))
            if conflict is not None:
                raise _hold(conflict)
            return PinLookup(anchor, current)

        return self._run(verify)


# ---------------------------------------------------------------------------------------------------------------------------------
# Per-decision shared fence
# ---------------------------------------------------------------------------------------------------------------------------------
class SharedFence:
    """A held shared ``flock`` on a read-only descriptor of the installer-owned per-decision fence file. ``release`` is idempotent."""

    def __init__(self, fd: int):
        self._fd: Optional[int] = fd

    @property
    def held(self) -> bool:
        return self._fd is not None

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                os.close(fd)   # closing the descriptor releases the lock even if LOCK_UN failed
            except OSError:
                pass


def acquire_shared_fence(config: AuthorizationPinConfig, decision_id: str) -> SharedFence:
    """Step 1 of section 6.2: ``LOCK_SH|LOCK_NB`` retried until the deadline. An absent file, a non-regular file or a timeout is a HOLD
    ``verifier_unavailable`` / ``pin_fence_unavailable``. The path derives from ``decision_id`` only through the fixed hash."""
    if fcntl is None:
        raise _fence_unavailable()
    try:
        path = fmt.fence_path(config.fence_dir, decision_id)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    except (OSError, fmt.PinFormatError) as exc:
        raise _fence_unavailable() from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise _fence_unavailable()
        deadline = time.monotonic() + config.fence_deadline_seconds
        delay = 0.002
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                return SharedFence(fd)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise _fence_unavailable() from None
                time.sleep(delay)
                delay = min(delay * 2, 0.05)
            except OSError as exc:
                raise _fence_unavailable() from exc
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------------------------------------------------------------
# Claim vs pin (section 5 steps 2 to 5)
# ---------------------------------------------------------------------------------------------------------------------------------
#: (claim attribute, pin payload member), in the order the freeze lists them.
_LINEAGE = (("effect_id", "effect_id"), ("action_id", "action_id"), ("tenant_id", "tenant_id"), ("institution_id", "institution_id"),
            ("authority_domain", "authority_domain"), ("grant_id", "grant_id"), ("grant_revision", "grant_revision"))


@dataclasses.dataclass(frozen=True)
class PinAdmission:
    """Result of a passed pin step: the PIN-derived governed scope (for the Registry guard) and the receipt member."""
    pin_scope: dict
    receipt: dict


def check_claim_against_pin(lookup: PinLookup, claim: Any, now: datetime) -> PinAdmission:
    """Section 5 steps 2 to 5 for one claim. ``lookup`` was produced under the shared fence inside the effect transaction. Raises a typed
    ``SelectionRefusal`` (REFUSE ``policy_identity_mismatch``, or HOLD) and never skips a step."""
    binding = getattr(claim, "policy_binding", None)
    if binding is None:
        raise _refuse(fmt.CAUSE_REQUIRED_BINDING_ABSENT)       # an omitted binding cannot bypass the pin; there is no legacy fallback
    row = lookup.row
    if row is None:
        raise _refuse(fmt.CAUSE_ABSENT)
    if row["status"] != "active":
        raise _refuse(fmt.CAUSE_STATUS_PREFIX + row["status"])
    pin = row["payload"]
    try:
        not_before, expires_at = fmt.parse_instant(pin["not_before"]), fmt.parse_instant(pin["expires_at"])
    except fmt.PinFormatError as exc:
        raise _hold(fmt.CAUSE_STORE_UNREADABLE) from exc
    if not (not_before <= now < expires_at):
        raise _refuse(fmt.CAUSE_NOT_VALID_NOW)
    for claim_attr, member in _LINEAGE:
        if getattr(claim, claim_attr, None) != pin[member]:
            raise _refuse(fmt.CAUSE_LINEAGE_PREFIX + member)
    try:
        from agent_control_plane import canonical_policy_binding as cpb
    except Exception as exc:
        raise SelectionRefusal(fmt.CODE_UNAVAILABLE, "HOLD", cause="binding_validator_unavailable") from exc
    defect = cpb.binding_defect(binding)
    if defect is not None:
        raise SelectionRefusal("field_invalid", cause=f"binding:{defect}")
    scope = binding["scope"]
    pairs = (("consumer_id", binding["consumer_id"], pin["consumer_id"]), ("operation", binding["operation"], pin["operation"]),
             ("scope.tenant", scope["tenant"], pin["tenant_id"]), ("scope.institution", scope["institution"], pin["institution_id"]),
             ("scope.authority_domain", scope["authority_domain"], pin["authority_domain"]),
             ("selection_epoch", binding["selection_epoch"], pin["selection_epoch"]),
             ("activation_ref", binding["activation_ref"], pin["activation_ref"]),
             ("binding_digest", cpb.binding_digest(binding), pin["binding_digest"]))   # computed here, never read from the claim
    for member, claimed, pinned in pairs:
        if claimed != pinned:
            raise _refuse(fmt.CAUSE_BINDING_PREFIX + member)
    pin_scope = {"consumer_id": pin["consumer_id"], "tenant": pin["tenant_id"], "institution": pin["institution_id"],
                 "authority_domain": pin["authority_domain"], "operation": pin["operation"]}
    receipt = {"pin_epoch": row["pin_epoch"], "pin_digest": row["pin_digest"], "anchor_committed_epoch": lookup.anchor["committed_epoch"],
               "payload_digest": fmt.payload_digest(pin)}
    return PinAdmission(pin_scope, receipt)
