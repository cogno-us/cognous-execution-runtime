"""Trusted host installer for the authorization pin (Registry r08-v2 Amendment 4, sections 3, 6 and 7).

Isolation statement. Nothing in this module is an isolation boundary. Writer isolation comes ONLY from the deployment's OS identities: the
installer runs as its own OS user, owns the pin store, anchor, fence directory and log, and the executor identity has no write access to them.
This Python API, its docstrings and ``assert_custody`` (a best-effort self-check) grant or prove no isolation.

Scope. It is the only code that writes the pin store, the anchor and the fence files. It imports every format, validator and the admission
table from ``engine.authorization_pin_format``; it implements none of them. It never opens the effect database, makes no network call, holds at
most one fence, adds no reason code and has no degraded mode: a terminal row is committed ONLY while the exclusive per-decision fence is held
(see ``_ExclusiveFence`` and ``_commit_terminal_row``). It is non-authorizing: it records Control Plane assessments, it does not decide them.
RELEASE / PUBLICATION / PRODUCTION: HOLD.

Fault-injection seams (tests only): ``_stage(name)`` calls ``stage_hook(name)`` when set; the stage names are listed in ``STAGES``.
"""
from __future__ import annotations

import dataclasses
import errno
import fcntl
import json
import os
import sqlite3
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from engine import authorization_pin_format as F

#: Export profile string of the Control Plane assessment (freeze section 3). Declared here only as a string; Worker A owns the producer.
PIN_EXPORT_PROFILE = "ocs-s4/authorization-pin-export/0.1"

#: Named fault-injection points, in the order they occur. A test hook may raise (or kill the process) at any of them.
STAGES = (
    "before_intent",                 # nothing written yet (case A)
    "after_intent_durable",          # intent durable, no fence requested (case B)
    "after_fence_created",           # install only: fence file exists
    "after_exclusive_fence",         # terminal: exclusive fence held, nothing verified (case C)
    "after_verify",                  # terminal: verified under the fence
    "row_inserted_uncommitted",      # row inserted inside the open transaction (case D, hot-journal shape)
    "before_row_commit",             # immediately before COMMIT (case D)
    "after_row_commit",              # row durable, anchor not yet completed (case E)
    "after_log_committed",           # committed log record written
    "after_anchor_complete",         # anchor completed, log not yet marked complete
)


class InstallerError(RuntimeError):
    """The installer cannot start or must not continue (anchor missing or inconsistent, store unreadable). No automatic repair exists."""


class CustodyError(InstallerError):
    """Best-effort custody self-check failed. This is NOT the isolation boundary."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclasses.dataclass(frozen=True)
class InstallerConfig:
    pin_store_path: Path
    fence_dir: Path
    anchor_path: Path
    log_path: Path
    pin_store_id: str
    exclusive_deadline_seconds: float = 5.0
    clock: Callable[[], datetime] = _utc_now


@dataclasses.dataclass(frozen=True)
class InstallResult:
    outcome: str          # installed | noop | complete | pending_blocked | refused | reconciled
    decision_id: Optional[str]
    pin_epoch: Optional[int]
    detail: str


OUTCOMES = ("installed", "noop", "complete", "pending_blocked", "refused", "reconciled")


class _ExclusiveFence:
    """Proof of a held exclusive per-decision fence. Only ``_acquire_exclusive`` constructs it; ``_commit_terminal_row`` requires it."""

    def __init__(self, decision_id: str, fd: int):
        self.decision_id, self._fd, self.held = decision_id, fd, True

    def release(self) -> None:
        if self.held:
            self.held = False
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)


class TrustedAuthorizationInstaller:
    stage_hook: Optional[Callable[[str], None]] = None

    def __init__(self, config: InstallerConfig):
        self.config = config
        self._lock = threading.RLock()
        self._sleep: Callable[[float], None] = time.sleep
        self._monotonic: Callable[[], float] = time.monotonic
        self.stage_hook = None
        self._conn: Optional[sqlite3.Connection] = None
        if type(config.pin_store_id) is not str or not config.pin_store_id:
            raise InstallerError("pin_store_id")
        if not config.exclusive_deadline_seconds >= 0:
            raise InstallerError("exclusive_deadline_seconds")
        for directory in (config.fence_dir, config.pin_store_path.parent, config.anchor_path.parent, config.log_path.parent):
            self._ensure_dir(directory)
        self._bootstrap()

    # ------------------------------------------------------------------ seams and small helpers
    def _stage(self, name: str) -> None:
        if name not in STAGES:
            raise AssertionError(f"unknown stage {name}")
        hook = self.stage_hook
        if hook is not None:
            hook(name)

    @staticmethod
    def _ensure_dir(path: Path) -> None:
        if not path.is_dir():
            path.mkdir(mode=0o755, parents=True, exist_ok=True)
            os.chmod(path, 0o755)

    def _now(self) -> str:
        return self.config.clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    # ------------------------------------------------------------------ pin store
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.config.pin_store_path), isolation_level=None, timeout=0)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _init_store(self) -> None:
        existed = self.config.pin_store_path.exists()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {F.PIN_TABLE} (pin_epoch INTEGER PRIMARY KEY, decision_id TEXT NOT NULL, status TEXT NOT NULL, "
                "row_json TEXT NOT NULL, pin_digest TEXT NOT NULL)")
            conn.execute(f"CREATE TABLE IF NOT EXISTS {F.META_TABLE} (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute(f"INSERT OR IGNORE INTO {F.META_TABLE}(key, value) VALUES ('profile', ?)", (F.PIN_PROFILE,))
            conn.execute(f"INSERT OR IGNORE INTO {F.META_TABLE}(key, value) VALUES ('pin_store_id', ?)", (self.config.pin_store_id,))
            # Defense in depth ONLY (not the boundary): a writer with file access can drop them. The boundary is OS identity.
            for verb in ("UPDATE", "DELETE"):
                conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {F.PIN_TABLE}_no_{verb.lower()} BEFORE {verb} ON {F.PIN_TABLE} "
                    "BEGIN SELECT RAISE(ABORT, 'authorization pin store is append-only'); END")
            conn.execute("COMMIT")
        finally:
            conn.close()
        os.chmod(self.config.pin_store_path, 0o644)
        if not existed:
            self._fsync_dir(self.config.pin_store_path.parent)

    def _meta(self, conn: sqlite3.Connection) -> dict:
        return dict(conn.execute(f"SELECT key, value FROM {F.META_TABLE}").fetchall())

    def _rows(self, conn: sqlite3.Connection, *, from_epoch: int = 1, decision_id: Optional[str] = None) -> list[dict]:
        if decision_id is None:
            cur = conn.execute(f"SELECT pin_epoch, decision_id, status, row_json, pin_digest FROM {F.PIN_TABLE} WHERE pin_epoch >= ? ORDER BY pin_epoch",
                               (from_epoch,))
        else:
            cur = conn.execute(f"SELECT pin_epoch, decision_id, status, row_json, pin_digest FROM {F.PIN_TABLE} WHERE decision_id = ? ORDER BY pin_epoch",
                               (decision_id,))
        rows = []
        for epoch, dec, status, text, digest in cur.fetchall():
            row = F.decode_row(text)
            if (row["pin_epoch"], row["decision_id"], row["status"], row["pin_digest"]) != (epoch, dec, status, digest):
                raise F.PinFormatError("row:columns")
            rows.append(row)
        return rows

    def _head(self, conn: sqlite3.Connection) -> F.Head:
        found = conn.execute(f"SELECT row_json FROM {F.PIN_TABLE} ORDER BY pin_epoch DESC LIMIT 1").fetchone()
        if found is None:
            return F.EMPTY_HEAD
        row = F.decode_row(found[0])
        return F.Head(row["pin_epoch"], row["pin_digest"], row["decision_id"], row["status"])

    def _store_is_empty(self) -> bool:
        if not self.config.pin_store_path.exists():
            return True
        conn = self._connect()
        try:
            has = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (F.PIN_TABLE,)).fetchone()
            return has is None or conn.execute(f"SELECT COUNT(*) FROM {F.PIN_TABLE}").fetchone()[0] == 0
        finally:
            conn.close()

    # ------------------------------------------------------------------ anchor
    def _read_anchor(self) -> Optional[dict]:
        try:
            return F.anchor_decode(self.config.anchor_path.read_bytes())
        except (OSError, F.PinFormatError):
            return None

    def _write_anchor(self, anchor: dict) -> None:
        """Atomic replace: temp file in the same directory, fsync file, os.replace, fsync directory."""
        data = F.anchor_encode(anchor)
        path = self.config.anchor_path
        tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.fchmod(fd, 0o644)
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        self._fsync_dir(path.parent)

    def _anchor_with(self, base: dict, *, committed_epoch: Optional[int] = None, head_digest: Optional[str] = None, intent: Any = "keep") -> dict:
        return F.make_anchor(
            pin_store_id=self.config.pin_store_id,
            committed_epoch=base["committed_epoch"] if committed_epoch is None else committed_epoch,
            committed_head_digest=base["committed_head_digest"] if head_digest is None else head_digest,
            intent=base["intent"] if intent == "keep" else intent,
            anchored_at=self._now())

    def _bootstrap(self) -> None:
        cfg = self.config
        anchor = self._read_anchor() if cfg.anchor_path.exists() else None
        if not cfg.anchor_path.exists():
            if not self._store_is_empty():
                raise InstallerError(f"{F.CAUSE_ANCHOR_UNAVAILABLE}: anchor absent with a non-empty pin store (operator re-baseline required)")
            if any(p.name.startswith(cfg.anchor_path.name) for p in cfg.anchor_path.parent.iterdir()):
                raise InstallerError(f"{F.CAUSE_ANCHOR_UNAVAILABLE}: anchor absent but anchor files remain (operator re-baseline required)")
            if any(cfg.fence_dir.iterdir()):
                raise InstallerError(f"{F.CAUSE_ANCHOR_UNAVAILABLE}: anchor absent but fence files exist (operator re-baseline required)")
            self._init_store()
            self._write_anchor(F.make_anchor(pin_store_id=cfg.pin_store_id, committed_epoch=0, committed_head_digest=F.ZERO_DIGEST,
                                             intent=None, anchored_at=self._now()))
            return
        if anchor is None:
            raise InstallerError(f"{F.CAUSE_ANCHOR_UNAVAILABLE}: anchor unreadable or malformed (no automatic repair)")
        if anchor["pin_store_id"] != cfg.pin_store_id:
            raise InstallerError(f"{F.CAUSE_ANCHOR_INCONSISTENT}: pin_store_id differs from configuration")
        if not cfg.pin_store_path.exists():
            raise InstallerError(f"{F.CAUSE_STORE_UNREADABLE}: pin store absent while an anchor exists (no automatic repair)")
        for stale in cfg.anchor_path.parent.glob(cfg.anchor_path.name + ".tmp.*"):
            stale.unlink()      # an uncommitted temp file from a crashed atomic replace; never the anchor itself
        self._init_store()      # idempotent; opening read-write also recovers a hot rollback journal
        conn = self._connect()
        try:
            meta = self._meta(conn)
        finally:
            conn.close()
        if meta.get("profile") != F.PIN_PROFILE or meta.get("pin_store_id") != cfg.pin_store_id:
            raise InstallerError(f"{F.CAUSE_STORE_UNREADABLE}: meta mismatch")

    # ------------------------------------------------------------------ state view
    def _state(self) -> tuple[Optional[dict], F.Head, Optional[str]]:
        """(anchor, head, defect). ``defect`` is a cause string when the installer must refuse (anchor or store unusable, chain broken)."""
        anchor = self._read_anchor()
        if anchor is None:
            return None, F.EMPTY_HEAD, F.CAUSE_ANCHOR_UNAVAILABLE
        if anchor["pin_store_id"] != self.config.pin_store_id:
            return anchor, F.EMPTY_HEAD, F.CAUSE_ANCHOR_INCONSISTENT
        conn = self._connect()
        try:
            head = self._head(conn)
            rows = self._rows(conn, from_epoch=max(anchor["committed_epoch"], 1))
        except (sqlite3.Error, F.PinFormatError, OSError):
            return anchor, F.EMPTY_HEAD, F.CAUSE_STORE_UNREADABLE
        finally:
            conn.close()
        adm = F.anchor_admission(anchor, head, "")      # decision-independent causes: hold_all
        if adm.verdict == "hold_all":
            return anchor, head, adm.cause
        if anchor["intent"] is None and head.epoch != anchor["committed_epoch"]:
            return anchor, head, F.CAUSE_ANCHOR_INCONSISTENT     # store ahead of a stale anchor: the installer refuses, operator re-baselines
        chain = F.chain_defect(rows, anchor)
        if chain is not None:
            return anchor, head, chain
        return anchor, head, None

    def _decision_rows(self, decision_id: str) -> tuple[list[dict], Optional[str]]:
        conn = self._connect()
        try:
            rows = self._rows(conn, decision_id=decision_id)
        except (sqlite3.Error, F.PinFormatError):
            return [], F.CAUSE_STORE_UNREADABLE
        finally:
            conn.close()
        current, conflict = F.decision_state(rows)
        return rows, conflict

    # ------------------------------------------------------------------ log
    def _log(self, *, action: str, decision_id: Optional[str], pin_epoch: Optional[int], pin_digest: Optional[str],
             intent_digest: Optional[str], outcome: str, anchor_before: Optional[dict], anchor_after: Optional[dict], detail: str = "") -> None:
        record = {"recorded_at": self._now(), "action": action, "decision_id": decision_id, "pin_epoch": pin_epoch, "pin_digest": pin_digest,
                  "intent_digest": intent_digest, "outcome": outcome,
                  "anchor_before": anchor_before["anchor_digest"] if anchor_before else None,
                  "anchor_after": anchor_after["anchor_digest"] if anchor_after else None}
        if detail:
            record["detail"] = detail
        new = not self.config.log_path.exists()
        fd = os.open(self.config.log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, F.canonical_bytes(record) + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        if new:
            self._fsync_dir(self.config.log_path.parent)

    def read_log(self) -> list[dict]:
        if not self.config.log_path.exists():
            return []
        return [json.loads(line) for line in self.config.log_path.read_text(encoding="utf-8").splitlines() if line]

    def _logged_row(self, pin_digest: str) -> bool:
        return any(r.get("pin_digest") == pin_digest and r.get("outcome") in ("committed", "complete", "reconciled") for r in self.read_log())

    # ------------------------------------------------------------------ public surface
    def pending(self) -> Optional[dict]:
        anchor = self._read_anchor()
        return None if anchor is None else anchor["intent"]

    def assert_custody(self) -> None:
        """Best-effort self-check: store, anchor, fence dir and log owned by the effective uid with no group/world write bit, and no ancestor that
        is group/world-writable (without sticky) or owned by a third party. NOT the isolation boundary: OS identities of the deployment are."""
        euid = os.geteuid()
        cfg = self.config
        for path in (cfg.pin_store_path, cfg.anchor_path, cfg.fence_dir, cfg.log_path, *cfg.fence_dir.glob("*.fence")):
            try:
                st = path.stat()
            except OSError as exc:
                raise CustodyError(f"cannot stat {path}: {exc}") from exc
            if st.st_uid != euid or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise CustodyError(f"{path}: not exclusively writable by the installer identity")
        for start in {cfg.pin_store_path.parent, cfg.fence_dir, cfg.anchor_path.parent, cfg.log_path.parent}:
            for ancestor in (start.resolve(), *start.resolve().parents):
                st = ancestor.stat()
                if st.st_uid not in (euid, 0):
                    raise CustodyError(f"{ancestor}: owned by a third identity")
                if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH) and not (st.st_mode & stat.S_ISVTX and ancestor != start.resolve()):
                    raise CustodyError(f"{ancestor}: group or world writable")

    def reconcile(self) -> list[InstallResult]:
        """Mandatory start-up reconcile. Completes a recorded intent from persisted state; never clears a terminal intent without verified
        completion; no abandon operation exists for a terminal intent. It does not re-contact the Control Plane: the intent was validated against
        persisted authority when it was recorded (6.3 step 1), and completing it can only remove authority."""
        with self._lock:
            return self._reconcile()

    def apply_assessment(self, assessment: dict) -> InstallResult:
        """The only entry that consumes Control Plane output. Reconciles first; refuses a malformed assessment or any unusable state."""
        with self._lock:
            problem = self._assessment_defect(assessment)
            decision_id = assessment.get("decision_id") if type(assessment) is dict and type(assessment.get("decision_id")) is str else None
            if problem is not None:
                return self._refuse("assessment", decision_id, problem)
            reconciled = self._reconcile()
            blocked = [r for r in reconciled if r.outcome in ("pending_blocked", "refused")]
            if blocked and blocked[0].outcome == "refused":
                return self._refuse(assessment["state"], decision_id, blocked[0].detail)
            if blocked or self.pending() is not None:
                return self._refuse(assessment["state"], decision_id, "pending intent unresolved" + (f": {blocked[0].detail}" if blocked else ""))
            anchor, head, defect = self._state()
            if defect is not None:
                return self._refuse(assessment["state"], decision_id, defect)
            if assessment["state"] == "active":
                return self._install(assessment, anchor, head)
            return self._terminal(assessment, anchor, head)

    # ------------------------------------------------------------------ assessment validation
    @staticmethod
    def _assessment_defect(assessment: Any) -> Optional[str]:
        if type(assessment) is not dict:
            return "assessment"
        if assessment.get("profile") != PIN_EXPORT_PROFILE:
            return "profile"
        if type(assessment.get("decision_id")) is not str or not assessment["decision_id"]:
            return "decision_id"
        state = assessment.get("state")
        if state == "active":
            defect = F.payload_defect(assessment.get("payload"))
            if defect is not None:
                return f"payload.{defect}"
            if assessment["payload"]["decision_id"] != assessment["decision_id"]:
                return "decision_id"
            if assessment.get("terminal_status") is not None:
                return "terminal_status"
            return None
        if state == "terminal":
            if assessment.get("terminal_status") not in F.TERMINAL_STATUSES:
                return "terminal_status"
            if assessment.get("payload") is not None:
                return "payload"
            return None
        return "state"

    def _refuse(self, action: str, decision_id: Optional[str], detail: str) -> InstallResult:
        anchor = self._read_anchor()
        self._log(action=action, decision_id=decision_id, pin_epoch=None, pin_digest=None, intent_digest=None, outcome="refused",
                  anchor_before=anchor, anchor_after=anchor, detail=detail)
        return InstallResult("refused", decision_id, None, detail)

    # ------------------------------------------------------------------ install
    def _install(self, assessment: dict, anchor: dict, head: F.Head) -> InstallResult:
        payload, decision_id = assessment["payload"], assessment["decision_id"]
        rows, conflict = self._decision_rows(decision_id)
        if conflict is not None:
            return self._refuse("install", decision_id, conflict)
        if rows:
            current = rows[-1]
            if current["status"] in F.TERMINAL_STATUSES:
                return self._refuse("install", decision_id, f"re-activation after terminal row ({current['status']}) refused")
            if F.payload_digest(current["payload"]) == F.payload_digest(payload):
                self._log(action="install", decision_id=decision_id, pin_epoch=current["pin_epoch"], pin_digest=current["pin_digest"],
                          intent_digest=None, outcome="noop", anchor_before=anchor, anchor_after=anchor)
                return InstallResult("noop", decision_id, current["pin_epoch"], "identical active payload")
            return self._refuse("install", decision_id, "conflicting payload for an existing decision")
        self._stage("before_intent")
        epoch = anchor["committed_epoch"] + 1
        intent = F.make_intent(epoch=epoch, decision_id=decision_id, action="install", status="active",
                               provenance_ref=f"{decision_id}:{payload['approval_commitment']}")
        with_intent = self._anchor_with(anchor, intent=intent)
        self._write_anchor(with_intent)
        self._stage("after_intent_durable")
        self._ensure_fence_file(decision_id)
        self._stage("after_fence_created")
        row = F.make_row(pin_epoch=epoch, decision_id=decision_id, status="active", payload=payload,
                         provenance={"intent_digest": intent["intent_digest"], "reason": "install:active"}, prev_pin_digest=head.digest)
        self._insert_row(row)
        self._log(action="install", decision_id=decision_id, pin_epoch=epoch, pin_digest=row["pin_digest"], intent_digest=intent["intent_digest"],
                  outcome="committed", anchor_before=anchor, anchor_after=with_intent)
        self._stage("after_log_committed")
        done = self._anchor_with(with_intent, committed_epoch=epoch, head_digest=row["pin_digest"], intent=None)
        self._write_anchor(done)
        self._stage("after_anchor_complete")
        self._log(action="install", decision_id=decision_id, pin_epoch=epoch, pin_digest=row["pin_digest"], intent_digest=intent["intent_digest"],
                  outcome="complete", anchor_before=with_intent, anchor_after=done)
        return InstallResult("installed", decision_id, epoch, "active pin installed")

    def _ensure_fence_file(self, decision_id: str) -> Path:
        path = F.fence_path(self.config.fence_dir, decision_id)
        if not path.exists():
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                os.fchmod(fd, 0o644)
                os.fsync(fd)
            finally:
                os.close(fd)
            self._fsync_dir(self.config.fence_dir)
        return path

    def _insert_row(self, row: dict) -> None:
        """Append one ACTIVE row (install only). Terminal rows go only through ``_commit_terminal_row``."""
        if row["status"] != "active":
            raise AssertionError("terminal rows are committed only under the exclusive fence")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._conn = conn
            self._write_row(conn, row)
            self._stage("row_inserted_uncommitted")
            self._stage("before_row_commit")
            conn.execute("COMMIT")
        except BaseException:
            self._rollback(conn)
            raise
        finally:
            self._conn = None
            conn.close()
        self._stage("after_row_commit")

    @staticmethod
    def _rollback(conn: sqlite3.Connection) -> None:
        try:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    @staticmethod
    def _write_row(conn: sqlite3.Connection, row: dict) -> None:
        conn.execute(f"INSERT INTO {F.PIN_TABLE}(pin_epoch, decision_id, status, row_json, pin_digest) VALUES (?, ?, ?, ?, ?)",
                     (row["pin_epoch"], row["decision_id"], row["status"], F.encode_row(row), row["pin_digest"]))

    # ------------------------------------------------------------------ terminal
    def _terminal(self, assessment: dict, anchor: dict, head: F.Head) -> InstallResult:
        decision_id, status = assessment["decision_id"], assessment["terminal_status"]
        rows, conflict = self._decision_rows(decision_id)
        if conflict is not None:
            return self._refuse("terminal", decision_id, conflict)
        if not rows:
            return self._refuse("terminal", decision_id, "no pin exists for this decision")
        current = rows[-1]
        if current["status"] in F.TERMINAL_STATUSES:
            if current["status"] == status:
                self._log(action="terminal", decision_id=decision_id, pin_epoch=current["pin_epoch"], pin_digest=current["pin_digest"],
                          intent_digest=None, outcome="noop", anchor_before=anchor, anchor_after=anchor)
                return InstallResult("noop", decision_id, current["pin_epoch"], "already terminal with the same status")
            return self._refuse("terminal", decision_id, f"decision already terminal as {current['status']}")
        self._stage("before_intent")
        epoch = anchor["committed_epoch"] + 1
        intent = F.make_intent(epoch=epoch, decision_id=decision_id, action="terminal", status=status,
                               provenance_ref=f"{decision_id}:{current['payload']['approval_commitment']}")
        with_intent = self._anchor_with(anchor, intent=intent)
        self._write_anchor(with_intent)                      # T_int: durable before the fence is requested
        self._log(action="terminal", decision_id=decision_id, pin_epoch=epoch, pin_digest=None, intent_digest=intent["intent_digest"],
                  outcome="intent_recorded", anchor_before=anchor, anchor_after=with_intent)
        self._stage("after_intent_durable")
        return self._finish_terminal(with_intent)

    def _acquire_exclusive(self, decision_id: str) -> Optional[_ExclusiveFence]:
        """flock(LOCK_EX|LOCK_NB) with exponential backoff until the deadline. None means the deadline passed (the caller commits nothing)."""
        path = self._ensure_fence_file(decision_id)
        fd = os.open(path, os.O_RDWR)
        deadline, delay = self._monotonic() + self.config.exclusive_deadline_seconds, 0.005
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return _ExclusiveFence(decision_id, fd)
            except OSError as exc:
                if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                    os.close(fd)
                    raise
            if self._monotonic() >= deadline:
                os.close(fd)
                return None
            self._sleep(delay)
            delay = min(delay * 2, 0.25)

    def _finish_terminal(self, anchor_with_intent: dict) -> InstallResult:
        """6.3 steps 4 to 9 for the intent recorded in the anchor (H == C). The only path to a terminal commit."""
        intent = anchor_with_intent["intent"]
        decision_id = intent["decision_id"]
        fence = self._acquire_exclusive(decision_id)
        if fence is None:
            self._log(action="terminal", decision_id=decision_id, pin_epoch=intent["epoch"], pin_digest=None, intent_digest=intent["intent_digest"],
                      outcome="pending_blocked", anchor_before=anchor_with_intent, anchor_after=anchor_with_intent,
                      detail=f"exclusive fence not acquired within {self.config.exclusive_deadline_seconds}s")
            return InstallResult("pending_blocked", decision_id, intent["epoch"], "exclusive fence not acquired; intent preserved, nothing committed")
        try:
            self._stage("after_exclusive_fence")
            anchor, head, defect = self._state()               # verify under the exclusive fence
            if defect is not None:
                return self._refuse("terminal", decision_id, defect)
            if anchor != anchor_with_intent or anchor["intent"] != intent or head.epoch != anchor["committed_epoch"]:
                return self._refuse("terminal", decision_id, F.CAUSE_ANCHOR_INCONSISTENT)
            rows, conflict = self._decision_rows(decision_id)
            if conflict is not None or not rows or rows[-1]["status"] != "active":
                return self._refuse("terminal", decision_id, conflict or "decision has no active pin or already has a terminal row")
            self._stage("after_verify")
            row = F.make_row(pin_epoch=intent["epoch"], decision_id=decision_id, status=intent["status"], payload=rows[-1]["payload"],
                             provenance={"intent_digest": intent["intent_digest"], "reason": f"terminal:{intent['status']}"},
                             prev_pin_digest=head.digest)
            self._commit_terminal_row(fence, row)               # T_c
            self._log(action="terminal", decision_id=decision_id, pin_epoch=row["pin_epoch"], pin_digest=row["pin_digest"],
                      intent_digest=intent["intent_digest"], outcome="committed", anchor_before=anchor, anchor_after=anchor)
            self._stage("after_log_committed")
            done = self._anchor_with(anchor, committed_epoch=row["pin_epoch"], head_digest=row["pin_digest"], intent=None)
            self._write_anchor(done)
            self._stage("after_anchor_complete")
            self._log(action="terminal", decision_id=decision_id, pin_epoch=row["pin_epoch"], pin_digest=row["pin_digest"],
                      intent_digest=intent["intent_digest"], outcome="complete", anchor_before=anchor, anchor_after=done)
            return InstallResult("complete", decision_id, row["pin_epoch"], f"terminal {row['status']} committed under the exclusive fence")
        finally:
            fence.release()

    def _commit_terminal_row(self, fence: _ExclusiveFence, row: dict) -> None:
        """Commit a terminal row. Requires a held exclusive fence for exactly this decision; there is no other caller and no bypass."""
        if not isinstance(fence, _ExclusiveFence) or not fence.held or fence.decision_id != row["decision_id"] or row["status"] not in F.TERMINAL_STATUSES:
            raise AssertionError("terminal commit without the exclusive fence")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._conn = conn
            self._write_row(conn, row)
            self._stage("row_inserted_uncommitted")
            self._stage("before_row_commit")
            conn.execute("COMMIT")
        except BaseException:
            self._rollback(conn)
            raise
        finally:
            self._conn = None
            conn.close()
        self._stage("after_row_commit")

    # ------------------------------------------------------------------ reconcile
    def _reconcile(self) -> list[InstallResult]:
        anchor, head, defect = self._state()
        if defect is not None:
            return [InstallResult("refused", None, None, defect)]
        intent = anchor["intent"]
        if intent is None:
            return []
        decision_id = intent["decision_id"]
        if head.epoch == anchor["committed_epoch"]:                   # B, D, F, H: the intended row is not committed
            if intent["action"] == "terminal":
                return [self._finish_terminal(anchor)]
            # An install intent grants nothing and creates no barrier; its payload is not recoverable from the descriptor, so the intent is
            # cleared with evidence and the assessment must be re-submitted. (Terminal intents are never cleared this way.)
            done = self._anchor_with(anchor, intent=None)
            self._write_anchor(done)
            self._log(action="install", decision_id=decision_id, pin_epoch=intent["epoch"], pin_digest=None, intent_digest=intent["intent_digest"],
                      outcome="reconciled", anchor_before=anchor, anchor_after=done, detail="uncommitted install intent cleared; resubmit assessment")
            return [InstallResult("reconciled", decision_id, None, "uncommitted install intent cleared; resubmit the assessment")]
        # E: the row at the intent epoch is committed, the anchor completion is pending
        conn = self._connect()
        try:
            row = self._rows(conn, from_epoch=head.epoch)[0]
        finally:
            conn.close()
        if row["provenance"]["intent_digest"] != intent["intent_digest"] or row["decision_id"] != decision_id or row["status"] != intent["status"]:
            return [InstallResult("refused", decision_id, intent["epoch"], F.CAUSE_ANCHOR_INCONSISTENT)]
        done = self._anchor_with(anchor, committed_epoch=row["pin_epoch"], head_digest=row["pin_digest"], intent=None)
        self._write_anchor(done)
        if not self._logged_row(row["pin_digest"]):         # the log is reconstructed from the row, never the reverse
            self._log(action=intent["action"], decision_id=decision_id, pin_epoch=row["pin_epoch"], pin_digest=row["pin_digest"],
                      intent_digest=intent["intent_digest"], outcome="reconciled", anchor_before=anchor, anchor_after=done,
                      detail="log record reconstructed from the committed pin row")
        self._log(action=intent["action"], decision_id=decision_id, pin_epoch=row["pin_epoch"], pin_digest=row["pin_digest"],
                  intent_digest=intent["intent_digest"], outcome="complete", anchor_before=anchor, anchor_after=done)
        return [InstallResult("reconciled", decision_id, row["pin_epoch"], "anchor completed from the committed row")]
