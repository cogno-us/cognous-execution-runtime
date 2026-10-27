"""Shared helpers for the host installer tests (no production use)."""
import json
import sqlite3
from pathlib import Path

from engine import authorization_pin_format as F
from host.authorization_pin import InstallerConfig, TrustedAuthorizationInstaller, PIN_EXPORT_PROFILE

VEC = json.loads((Path(__file__).parent.parent / "fixtures" / "authorization_pin_vectors.json").read_text())


def payload(decision_id="dec-1", **over):
    value = {**VEC["payload"], "decision_id": decision_id, **over}
    assert F.payload_defect(value) is None
    return value


def active(decision_id="dec-1", **over):
    return {"profile": PIN_EXPORT_PROFILE, "decision_id": decision_id, "state": "active", "payload": payload(decision_id, **over),
            "terminal_status": None, "reason": "test"}


def terminal(decision_id="dec-1", status="revoked"):
    return {"profile": PIN_EXPORT_PROFILE, "decision_id": decision_id, "state": "terminal", "payload": None,
            "terminal_status": status, "reason": "test"}


def make_config(root: Path, clock, **kw) -> InstallerConfig:
    return InstallerConfig(pin_store_path=root / "pin" / "pins.sqlite3", fence_dir=root / "pin" / "fences", anchor_path=root / "anchor" / "pins.anchor.json",
                           log_path=root / "pin" / "installer.log", pin_store_id="store-1", clock=clock, **kw)


def reader_state(cfg: InstallerConfig):
    """What an executor-side reader sees, read independently of the installer: (anchor, head, all rows). Uses SQLite mode=ro."""
    anchor = F.anchor_decode(cfg.anchor_path.read_bytes())
    conn = sqlite3.connect(f"file:{cfg.pin_store_path}?mode=ro", uri=True)
    try:
        rows = [F.decode_row(r[0]) for r in conn.execute(f"SELECT row_json FROM {F.PIN_TABLE} ORDER BY pin_epoch")]
    finally:
        conn.close()
    head = F.EMPTY_HEAD if not rows else F.Head(rows[-1]["pin_epoch"], rows[-1]["pin_digest"], rows[-1]["decision_id"], rows[-1]["status"])
    return anchor, head, rows


def admission(cfg, decision_id):
    anchor, head, _ = reader_state(cfg)
    return F.anchor_admission(anchor, head, decision_id)


def chain_ok(cfg):
    anchor, head, rows = reader_state(cfg)
    tail = [r for r in rows if r["pin_epoch"] >= max(anchor["committed_epoch"], 1)]
    return F.chain_defect(tail, anchor) is None and all(F.row_defect(r) is None for r in rows)


class Crash(Exception):
    """Raised from a stage hook to simulate a process crash at that point (the process-level kill variants use real SIGKILL)."""


def crash_at(stage):
    def hook(name):
        if name == stage:
            raise Crash(stage)
    return hook
