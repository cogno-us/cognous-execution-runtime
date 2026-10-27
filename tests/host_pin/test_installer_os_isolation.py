"""Real OS-identity test: the installer runs as one throwaway user; a second user (the executor stand-in) cannot write the pin artifacts.

This demonstrates what OS file permissions do for a deployment that uses separate identities. It does NOT show that the Python API is an
isolation boundary (it is not). Skipped (INCONCLUSIVE, never PASS) unless root with useradd and setpriv.
"""
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from engine import authorization_pin_format as F
from pin_helpers import active, terminal

REPO = Path(__file__).resolve().parents[2]
CHILD = Path(__file__).with_name("child.py")

pytestmark = [pytest.mark.os_isolation,
              pytest.mark.skipif(not (hasattr(os, "geteuid") and os.geteuid() == 0 and shutil.which("useradd") and shutil.which("userdel")
                                      and shutil.which("setpriv")), reason="needs root, useradd/userdel and setpriv (INCONCLUSIVE, not a pass)")]

PROBE = r'''
import fcntl, json, os, sqlite3, sys
store, anchor, fences, log, fence = sys.argv[1:6]
store_dir, anchor_dir = os.path.dirname(store), os.path.dirname(anchor)
def attempt(fn):
    try:
        fn(); return "ALLOWED"
    except PermissionError: return "denied"
    except OSError as exc: return "denied:" + str(exc.errno)
    except sqlite3.Error as exc: return "denied:sqlite:" + str(exc)
def create(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(fd)
def sqlite_write():
    c = sqlite3.connect(store, timeout=0); c.execute("CREATE TABLE IF NOT EXISTS intruder(x)"); c.commit()
r = {}
for label, path in (("store", store), ("anchor", anchor), ("fence", fence), ("log", log)):
    r[label + "_open_write"] = attempt(lambda p=path: os.close(os.open(p, os.O_WRONLY)))
    r[label + "_append"] = attempt(lambda p=path: os.close(os.open(p, os.O_WRONLY | os.O_APPEND)))
    r[label + "_unlink"] = attempt(lambda p=path: os.unlink(p))
    r[label + "_rename"] = attempt(lambda p=path: os.rename(p, p + ".moved"))
    r[label + "_chmod"] = attempt(lambda p=path: os.chmod(p, 0o666))
r["store_sqlite_rw_write"] = attempt(sqlite_write)
r["journal_create"] = attempt(lambda: create(store + "-journal"))
r["store_dir_create"] = attempt(lambda: create(os.path.join(store_dir, "x")))
r["anchor_dir_create_tmp"] = attempt(lambda: create(anchor + ".tmp.1"))
r["anchor_replace"] = attempt(lambda: (create(os.path.join(anchor_dir, "evil")), os.replace(os.path.join(anchor_dir, "evil"), anchor)))
r["fence_dir_create"] = attempt(lambda: create(os.path.join(fences, "evil.fence")))
r["fence_dir_unlink_all"] = attempt(lambda: [os.unlink(os.path.join(fences, n)) for n in os.listdir(fences)])
# what the executor identity MAY do: read-only open, read the anchor, shared flock on a read-only descriptor
c = sqlite3.connect("file:" + store + "?mode=ro", uri=True)
r["ro_row_count"] = c.execute("SELECT COUNT(*) FROM authorization_pin_v1").fetchone()[0]
c.close()
r["anchor_read_len"] = len(open(anchor, "rb").read())
fd = os.open(fence, os.O_RDONLY)
fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB); r["shared_flock_ro_fd"] = "ok"
print(json.dumps(r))
'''


def run_as(user, *argv, **kw):
    return subprocess.run(["setpriv", f"--reuid={user}", f"--regid={user}", "--clear-groups", *argv], capture_output=True, text=True, timeout=60,
                          env={**os.environ, "PYTHONPATH": f"{REPO}:{REPO / 'src'}", "PYTHONDONTWRITEBYTECODE": "1"}, cwd=REPO, **kw)


def test_installer_identity_writes_and_executor_identity_cannot():
    suffix = secrets.token_hex(3)
    inst_user, exec_user = f"pininst{suffix}", f"pinexec{suffix}"
    base = Path(tempfile.mkdtemp(prefix="pin-os-", dir="/tmp"))
    created = []
    try:
        for name in (inst_user, exec_user):
            subprocess.run(["useradd", "-M", "-s", "/usr/sbin/nologin", "-U", name], check=True)
            created.append(name)
        os.chmod(base, 0o755)
        for d in ("pin", "anchor"):
            (base / d).mkdir(mode=0o755)
            shutil.chown(base / d, user=inst_user, group=inst_user)
            os.chmod(base / d, 0o755)
        store, fences, anchor, log = base / "pin/pins.sqlite3", base / "pin/fences", base / "anchor/pins.anchor.json", base / "pin/installer.log"
        spec = {"store": str(store), "fences": str(fences), "anchor": str(anchor), "log": str(log), "store_id": "store-os",
                "assessments": [active("dec-1"), active("dec-2"), terminal("dec-2")]}
        done = run_as(inst_user, sys.executable, str(CHILD), "run", json.dumps(spec))
        assert done.returncode == 0, done.stderr
        assert done.stdout.split() == ["installed", "installed", "complete", "custody-ok"]
        for p in (store, anchor, log, fences, F.fence_path(fences, "dec-1")):
            assert p.stat().st_uid == int(subprocess.check_output(["id", "-u", inst_user]))
        before = {p: p.read_bytes() for p in (store, anchor, log)}
        probe = run_as(exec_user, sys.executable, "-c", PROBE, str(store), str(anchor), str(fences), str(log), str(F.fence_path(fences, "dec-1")))
        assert probe.returncode == 0, probe.stderr
        result = json.loads(probe.stdout)
        denied = {k: v for k, v in result.items() if k not in ("ro_row_count", "anchor_read_len", "shared_flock_ro_fd")}
        assert denied and all(v.startswith("denied") for v in denied.values()), denied
        assert result["ro_row_count"] == 3 and result["anchor_read_len"] > 0 and result["shared_flock_ro_fd"] == "ok"
        assert {p: p.read_bytes() for p in (store, anchor, log)} == before                  # nothing was altered
        assert not Path(str(store) + "-journal").exists() and sorted(os.listdir(fences)) == sorted(
            F.fence_path(fences, d).name for d in ("dec-1", "dec-2"))
        # the installer identity can still operate afterwards
        again = run_as(inst_user, sys.executable, str(CHILD), "run", json.dumps({**spec, "assessments": [active("dec-3")]}))
        assert again.returncode == 0 and again.stdout.split() == ["installed", "custody-ok"], again.stderr
    finally:
        for name in created:
            subprocess.run(["userdel", "-f", name], capture_output=True)
            subprocess.run(["groupdel", name], capture_output=True)
        shutil.rmtree(base, ignore_errors=True)
