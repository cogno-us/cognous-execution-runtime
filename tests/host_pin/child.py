"""Child-process helpers for the installer tests (real processes, real flock, real SIGKILL)."""
import fcntl
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

mode = sys.argv[1]
if mode == "hold":                      # hold a flock on a fence file using a READ-ONLY descriptor, like an executor
    kind, path = sys.argv[2], sys.argv[3]
    fd = os.open(path, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_SH if kind == "sh" else fcntl.LOCK_EX)
    print("ready", flush=True)
    sys.stdin.read()                    # released when the parent closes stdin or kills us
    sys.exit(0)

from host.authorization_pin import InstallerConfig, TrustedAuthorizationInstaller   # noqa: E402

if mode == "kill":                      # run an assessment and SIGKILL self at a stage (optionally leaving a hot journal)
    spec = json.loads(sys.argv[2])
    cfg = InstallerConfig(pin_store_path=Path(spec["store"]), fence_dir=Path(spec["fences"]), anchor_path=Path(spec["anchor"]),
                          log_path=Path(spec["log"]), pin_store_id=spec["store_id"], exclusive_deadline_seconds=2.0,
                          clock=lambda: datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc))
    inst = TrustedAuthorizationInstaller(cfg)

    def hook(name):
        if name == spec["stage"]:
            if spec.get("hot_journal"):
                conn = inst._conn
                conn.execute("PRAGMA cache_size=1")
                blob = "x" * 3000
                for i in range(400):
                    conn.execute("INSERT INTO authorization_pin_v1(pin_epoch, decision_id, status, row_json, pin_digest) VALUES (?,?,?,?,?)",
                                 (1000 + i, "junk", "active", blob, "sha256:" + "1" * 64))
            os.kill(os.getpid(), signal.SIGKILL)
    inst.stage_hook = hook
    inst.apply_assessment(spec["assessment"])
    sys.exit(3)                         # the kill stage was never reached
if mode == "run":                       # run one assessment normally and print the outcome (used by the OS-identity test)
    spec = json.loads(sys.argv[2])
    cfg = InstallerConfig(pin_store_path=Path(spec["store"]), fence_dir=Path(spec["fences"]), anchor_path=Path(spec["anchor"]),
                          log_path=Path(spec["log"]), pin_store_id=spec["store_id"])
    inst = TrustedAuthorizationInstaller(cfg)
    for assessment in spec["assessments"]:
        print(inst.apply_assessment(assessment).outcome, flush=True)
    inst.assert_custody()
    print("custody-ok", flush=True)
