# SPDX-License-Identifier: Apache-2.0
"""Optional OpenShell 0.1.2 synthetic destination; trusted local host boundary."""
from __future__ import annotations

import json
import hashlib
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

from .safe_executor import DurableRefundDestination, FrozenEnvelope, commitment

ADAPTER_VERSION = "0.1.0"
UPSTREAM_COMMIT = "6648bd0c290efbc41ba131ee9831ee45cd431f94"
COMMAND = ("/usr/bin/env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin",
           "/usr/local/bin/python3", "-I", "/opt/cognous/openshell_worker.py")
POLICY = {
    "version": 1,
    "filesystem_policy": {"include_workdir": False,
                          "read_only": ["/usr", "/lib", "/lib64", "/bin", "/etc", "/opt/cognous"],
                          "read_write": ["/var/lib/cognous"]},
    "landlock": {"compatibility": "hard_requirement"},
    "process": {"run_as_user": "1000", "run_as_group": "1000"},
    "network_policies": {},
}


@dataclass(frozen=True)
class OpenShellConfig:
    gateway: str
    workspace: str
    sandbox: str
    sandbox_id: str
    image: str
    cli_sha256: str
    policy_json: str
    policy_version: int
    policy_hash: str
    config_revision: int
    provider_env_revision: int
    timeout_seconds: int = 15
    cpu: str = "1"
    memory: str = "256Mi"
    runtime: str = "docker"
    executable: tuple[str, ...] = COMMAND
    workdir: str = "/var/lib/cognous"

    def __post_init__(self):
        for value in (self.gateway, self.workspace, self.sandbox):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", value):
                raise ValueError("invalid OpenShell selector")
        if not re.fullmatch(r"[0-9a-f]{64}", self.cli_sha256):
            raise ValueError("pinned CLI SHA-256 required")
        if not self.sandbox_id or not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", self.image):
            raise ValueError("sandbox identity and immutable OCI image digest required")
        if self.runtime != "docker" or self.cpu != "1" or self.memory != "256Mi":
            raise ValueError("only qualified local Docker profile supported")
        if self.executable != COMMAND or self.workdir != "/var/lib/cognous":
            raise ValueError("executable or working directory substitution")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 30:
            raise ValueError("timeout must be an integer from 1 to 30 seconds")
        for value in (self.policy_version, self.config_revision, self.provider_env_revision):
            if type(value) is not int or value < 0:
                raise ValueError("invalid configuration revision")
        if self.policy_version < 1 or not self.policy_hash:
            raise ValueError("active policy identity required")
        # Exact parsed effective policy, including defaults, is pinned below.
        p = json.loads(self.policy_json)
        if p.get("filesystem_policy") != POLICY["filesystem_policy"] or p.get("landlock") != POLICY["landlock"] or p.get("process") != POLICY["process"]:
            raise ValueError("unsupported filesystem/process policy")
        if type(p.get("version")) is not int or p.get("version") != 1 or p.get("network_policies", {}) or p.get("network_middlewares", {}):
            raise ValueError("network access is unsupported in this synthetic profile")
        if set(p) - set(POLICY) - {"network_middlewares"}:
            raise ValueError("unsupported policy controls")

    @property
    def digest(self):
        return commitment({"adapter_version": ADAPTER_VERSION, "upstream_commit": UPSTREAM_COMMIT,
                           **asdict(self)})


class OpenShellCLI:
    """Fixed argv, bounded input, no inherited host secrets or shell evaluation.

    HOME/config directory must be dedicated to this local gateway, with no
    production credentials. Gateway transport credentials stay on the host.
    """
    def __init__(self, binary: str, home: str):
        self.binary = str(Path(binary).resolve(strict=True))
        self.home = str(Path(home).resolve(strict=True))

    def run(self, config, args, *, stdin=None):
        if hashlib.sha256(Path(self.binary).read_bytes()).hexdigest() != config.cli_sha256:
            raise PermissionError("CLI executable digest changed")
        return subprocess.run(
            [self.binary, "--gateway", config.gateway, "--workspace", config.workspace, *args],
            input=stdin, text=True, capture_output=True, check=True,
            timeout=config.timeout_seconds + 15, shell=False,
            env={"HOME": self.home, "PATH": "/usr/local/bin:/usr/bin:/bin",
                 "XDG_CONFIG_HOME": str(Path(self.home) / ".config")},
        ).stdout

    def inspect(self, config):
        runtime = json.loads(self.run(config, ["gateway", "info", "--output", "json"]))
        if (runtime.get("version") != "0.1.2" or runtime.get("status") != "healthy"
                or urlparse(runtime.get("server", "")).hostname not in {"localhost", "127.0.0.1", "::1"}
                or [d.get("capabilities", {}).get("driver_name") for d in runtime.get("compute_drivers", [])] != ["docker"]):
            raise PermissionError("requires pinned 0.1.2 healthy local Docker gateway")
        result = json.loads(self.run(config, ["sandbox", "get", config.sandbox, "--output", "json"]))
        result["runtime_evidence"] = runtime
        return result

    def invoke(self, config, request):
        data = json.dumps(request, allow_nan=False, separators=(",", ":"))
        if len(data.encode()) > 65536:
            raise ValueError("synthetic request exceeds 64 KiB")
        output = self.run(config, ["sandbox", "exec", "--name", config.sandbox,
                         "--workdir", config.workdir, "--timeout", str(config.timeout_seconds),
                         "--no-tty", "--no-login-shell", "--env", "BASH_ENV=/dev/null",
                         "--", *config.executable], stdin=data)
        return json.loads(output)

    def request_stop(self, config):
        # A successful stop request is not proof of rollback or absence.
        return self.run(config, ["sandbox", "stop", config.sandbox])


class OpenShellRefundDestination(DurableRefundDestination):
    """Host attempt journal plus sandbox-owned SQLite destination.

    The host database is NOT evidence of the refund effect. Its dispatch intent
    is conservative: any uncertain submission with no observed row holds forever
    until an operator supplies an independently justified recovery procedure.
    """
    def __init__(self, root, config: OpenShellConfig, cli: OpenShellCLI):
        super().__init__(root)
        self.config = config
        self.cli = cli
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS environment_bindings (
                    effect_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL,
                    operation_digest TEXT NOT NULL, config_digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS environment_events (
                    id INTEGER PRIMARY KEY, effect_id TEXT NOT NULL,
                    kind TEXT NOT NULL, evidence TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS dispatch_intents (
                    effect_id TEXT PRIMARY KEY, config_digest TEXT NOT NULL);
            """)

    def bind(self, snapshot: FrozenEnvelope):
        """Trusted host registration at decision receipt, not authority issuance.

        Call before handing the decision to an untrusted caller. No rebind or
        automatic migration is permitted. Config changes require a new decision.
        """
        expected = (snapshot.effect_id, snapshot.decision_id, snapshot.operation.digest, self.config.digest)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM environment_bindings WHERE effect_id=?", (snapshot.effect_id,)).fetchone()
            if row and tuple(row) != expected:
                raise PermissionError("environment binding cannot be replaced")
            db.execute("INSERT OR IGNORE INTO environment_bindings VALUES(?,?,?,?)", expected)
            db.commit()
        self._event(snapshot, "bound_configuration", {"config": asdict(self.config), "digest": self.config.digest})

    def _event(self, snapshot, kind, evidence):
        with self._connect() as db:
            db.execute("INSERT INTO environment_events(effect_id,kind,evidence) VALUES(?,?,?)",
                       (snapshot.effect_id, kind, json.dumps(evidence, sort_keys=True)))

    def _binding(self, snapshot):
        with self._connect() as db:
            row = db.execute("SELECT * FROM environment_bindings WHERE effect_id=?", (snapshot.effect_id,)).fetchone()
        if row is None or tuple(row) != (snapshot.effect_id, snapshot.decision_id, snapshot.operation.digest, self.config.digest):
            raise PermissionError("missing or substituted environment/operation binding")

    def _inspect(self, snapshot):
        self._binding(snapshot)
        info = self.cli.inspect(self.config)
        c = self.config
        expected = {"id": c.sandbox_id, "name": c.sandbox, "workspace": c.workspace,
                    "phase": "Ready", "current_policy_version": c.policy_version,
                    "policy_source": "sandbox", "revision": c.policy_version,
                    "policy": json.loads(c.policy_json)}
        if any(info.get(k) != v for k, v in expected.items()):
            raise PermissionError("sandbox identity, readiness or policy changed")
        admission = info.get("configuration_admission") or {}
        if any(admission.get(k) != v for k, v in {
            "state": "accepted", "policy_version": c.policy_version,
            "policy_hash": c.policy_hash, "config_revision": c.config_revision,
            "provider_env_revision": c.provider_env_revision}.items()):
            raise PermissionError("sandbox configuration is not the pinned admitted revision")
        self._event(snapshot, "runtime_observed", info)
        return info

    @staticmethod
    def _request(snapshot, mode):
        return {"version": ADAPTER_VERSION, "mode": mode, "snapshot": asdict(snapshot)}

    def observe_bound(self, snapshot):
        self._binding(snapshot)
        try:
            self._inspect(snapshot)
            result = self.cli.invoke(self.config, self._request(snapshot, "observe"))
            if result.get("effect_id") != snapshot.effect_id or result.get("state") not in {"absent", "applied", "partial"}:
                raise ValueError("invalid destination observation")
            if result["state"] != "absent" and result.get("destination_state", {}).get("operation_digest") != snapshot.operation.digest:
                raise PermissionError("effect content substitution")
            with self._connect() as db:
                pending = db.execute("SELECT 1 FROM dispatch_intents WHERE effect_id=?", (snapshot.effect_id,)).fetchone()
            if result["state"] == "absent" and pending:
                result = {"effect_id": snapshot.effect_id, "state": "unknown", "destination_state": {}}
            self._event(snapshot, "destination_observed", result)
            return result
        except PermissionError:
            raise
        except Exception as exc:
            self._event(snapshot, "observation_unavailable", {"error_type": type(exc).__name__})
            return {"effect_id": snapshot.effect_id, "state": "unknown", "destination_state": {}}

    def commit(self, snapshot, *, simulate=None):
        if simulate is not None:
            raise PermissionError("simulation switches unavailable on OpenShell path")
        self._inspect(snapshot)
        # Durable, transactional intent precedes any dispatch; concurrent callers
        # cannot submit again even if the host crashes before receiving an ack.
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM dispatch_intents WHERE effect_id=?", (snapshot.effect_id,)).fetchone():
                raise TimeoutError("prior dispatch unresolved; observe before recovery")
            db.execute("INSERT INTO dispatch_intents VALUES(?,?)", (snapshot.effect_id, self.config.digest))
            db.commit()
        self._event(snapshot, "dispatch_requested", {"operation_digest": snapshot.operation.digest,
                    "config_digest": self.config.digest, "command": COMMAND, "workdir": self.config.workdir})
        try:
            ack = self.cli.invoke(self.config, self._request(snapshot, "commit"))
            self._event(snapshot, "process_acknowledged", {"receipt": ack})
            observed = self.observe_bound(snapshot)
            if observed["state"] not in {"applied", "partial"}:
                raise TimeoutError("process exit does not establish destination delivery")
            if type(ack.get("duplicate")) is not bool:
                raise TimeoutError("invalid destination acknowledgement")
            return {"duplicate": ack["duplicate"], "observation": observed}
        except Exception as exc:
            self._event(snapshot, "dispatch_unknown", {"error_type": type(exc).__name__})
            raise TimeoutError("sandbox dispatch outcome requires destination observation") from exc

    def observe(self, effect_id):
        raise NotImplementedError("OpenShell observation requires the bound full snapshot")

    def effect_count(self, grant_id):
        raise NotImplementedError("host audit database is not the sandbox destination")

    def request_cancel(self, snapshot):
        self._binding(snapshot)
        self._event(snapshot, "cancellation_requested", {})
        try:
            self.cli.request_stop(self.config)
            self._event(snapshot, "stop_acknowledged", {})
        except Exception as exc:
            self._event(snapshot, "stop_unknown", {"error_type": type(exc).__name__})
        try:
            return self.observe_bound(snapshot)
        except PermissionError:
            return {"effect_id": snapshot.effect_id, "state": "unknown", "destination_state": {}}
