from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .safe_executor import (
    DurableRefundDestination,
    ExecutionResult,
    FrozenEnvelope,
    LocalDestinationExecutor,
    LocalExecutionPolicy,
    commitment,
)

OPENSHELL_ADAPTER_VERSION = "0.1.0"
OPENSHELL_UPSTREAM_RELEASE = "v0.1.2"
OPENSHELL_UPSTREAM_COMMIT = "6648bd0c290efbc41ba131ee9831ee45cd431f94"

# Fixed adapter-owned program. Authorized operation data is passed only as a
# positional argument; caller/model text is never evaluated as shell source.
_FIXED_OPERATION_SCRIPT = """set -eu
umask 077
printf '%s' "$1" > .cognous-operation.b64
cat .cognous-operation.b64
"""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OpenShellProfile:
    """Immutable local execution-environment profile.

    This is a local narrowing constraint, not institutional authorization.
    """

    image: str = "nvcr.io/nvidia/base/ubuntu:24.04"
    executable: str = "/bin/sh"
    cpu: str = "500m"
    memory: str = "256Mi"
    timeout_seconds: int = 20
    workspace: str = "default"
    workdir: str | None = None
    network_destinations: tuple[str, ...] = ()
    credential_refs: tuple[str, ...] = ()
    policy_json: str = _canonical_json(
        {
            "version": 1,
            "filesystem_policy": {
                "include_workdir": True,
                "read_only": ["/bin", "/usr", "/lib", "/etc"],
                "read_write": [],
            },
            "landlock": {"compatibility": "hard_requirement"},
            "network_policies": {},
        }
    )

    def policy(self) -> dict:
        value = json.loads(self.policy_json)
        if not isinstance(value, dict):
            raise ValueError("OpenShell policy must be an object")
        return value

    @property
    def policy_digest(self) -> str:
        return _sha256_text(_canonical_json(self.policy()))

    @property
    def profile_digest(self) -> str:
        return commitment(
            {
                "adapter_version": OPENSHELL_ADAPTER_VERSION,
                "upstream_release": OPENSHELL_UPSTREAM_RELEASE,
                "upstream_commit": OPENSHELL_UPSTREAM_COMMIT,
                "image": self.image,
                "executable": self.executable,
                "cpu": self.cpu,
                "memory": self.memory,
                "timeout_seconds": self.timeout_seconds,
                "workspace": self.workspace,
                "workdir": self.workdir,
                "network_destinations": list(self.network_destinations),
                "credential_refs": list(self.credential_refs),
                "policy_digest": self.policy_digest,
            }
        )

    def validate(self) -> None:
        if not self.image or not self.executable:
            raise ValueError("OpenShell image and executable are required")
        if type(self.timeout_seconds) is not int or self.timeout_seconds < 1:
            raise ValueError("OpenShell timeout_seconds must be a positive integer")
        if self.credential_refs:
            raise ValueError(
                "synthetic OpenShell adapter does not accept credential references"
            )
        if self.network_destinations:
            raise ValueError(
                "synthetic OpenShell adapter does not authorize network destinations"
            )
        policy = self.policy()
        if policy.get("version") != 1:
            raise ValueError("OpenShell policy schema version must be 1")
        landlock = policy.get("landlock")
        if not isinstance(landlock, dict) or landlock.get("compatibility") != "hard_requirement":
            raise ValueError("OpenShell adapter requires Landlock hard_requirement")
        network = policy.get("network_policies")
        if network not in ({}, None):
            raise ValueError("synthetic OpenShell adapter requires deny-by-default network policy")


@dataclass(frozen=True)
class OpenShellCommandResult:
    returncode: int
    stdout: str
    stderr: str


class OpenShellRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: int,
        env: Mapping[str, str],
    ) -> OpenShellCommandResult: ...


class SubprocessOpenShellRunner:
    """Invoke the official OpenShell CLI without a shell."""

    _HOST_ENV_ALLOWLIST = (
        "HOME",
        "PATH",
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "OPENSHELL_CONFIG_DIR",
        "OPENSHELL_TELEMETRY_ENABLED",
    )

    def __init__(self, binary: str = "openshell"):
        self.binary = binary

    def host_env(self) -> dict[str, str]:
        env = {
            key: os.environ[key]
            for key in self._HOST_ENV_ALLOWLIST
            if key in os.environ
        }
        env.setdefault("PATH", os.environ.get("PATH", ""))
        env["OPENSHELL_TELEMETRY_ENABLED"] = "false"
        return env

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: int,
        env: Mapping[str, str],
    ) -> OpenShellCommandResult:
        if not argv or argv[0] != self.binary:
            raise ValueError("OpenShell runner only executes the configured binary")
        completed = subprocess.run(
            list(argv),
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=dict(env),
        )
        return OpenShellCommandResult(
            completed.returncode, completed.stdout, completed.stderr
        )


class OpenShellEvidenceStore:
    """Durable environment-attempt evidence, separate from destination effects."""

    _UNRESOLVED = {
        "starting",
        "sandbox_started",
        "process_dispatched",
        "unknown",
        "cancel_requested",
    }

    def __init__(self, destination: DurableRefundDestination):
        self.root = destination.root
        raw = self.root / "openshell-evidence.sqlite3"
        if raw.exists() and raw.is_symlink():
            raise PermissionError("OpenShell evidence database must not be a symlink")
        self.path = raw.resolve(strict=False)
        if self.root != self.path.parent and self.root not in self.path.parents:
            raise PermissionError("OpenShell evidence path escapes destination root")
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS openshell_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    effect_id TEXT NOT NULL,
                    operation_digest TEXT NOT NULL,
                    spec_digest TEXT NOT NULL,
                    sandbox_name TEXT NOT NULL,
                    sandbox_id TEXT,
                    state TEXT NOT NULL,
                    upstream_release TEXT NOT NULL,
                    upstream_commit TEXT NOT NULL,
                    runtime_version TEXT,
                    requested_policy_digest TEXT NOT NULL,
                    base_policy_digest TEXT,
                    effective_policy_digest_before TEXT,
                    effective_policy_digest_after TEXT,
                    evidence_json TEXT NOT NULL,
                    error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_openshell_effect
                    ON openshell_attempts(effect_id, created_at);
                """
            )

    def begin(
        self,
        *,
        attempt_id: str,
        effect_id: str,
        operation_digest: str,
        spec_digest: str,
        sandbox_name: str,
        requested_policy_digest: str,
        evidence: dict,
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO openshell_attempts(
                    attempt_id,effect_id,operation_digest,spec_digest,sandbox_name,
                    state,upstream_release,upstream_commit,requested_policy_digest,
                    evidence_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    attempt_id,
                    effect_id,
                    operation_digest,
                    spec_digest,
                    sandbox_name,
                    "starting",
                    OPENSHELL_UPSTREAM_RELEASE,
                    OPENSHELL_UPSTREAM_COMMIT,
                    requested_policy_digest,
                    _canonical_json(evidence),
                    now,
                    now,
                ),
            )

    def update(self, attempt_id: str, *, state: str, **fields: object) -> None:
        allowed = {
            "sandbox_id",
            "runtime_version",
            "base_policy_digest",
            "effective_policy_digest_before",
            "effective_policy_digest_after",
            "evidence_json",
            "error",
        }
        unexpected = set(fields) - allowed
        if unexpected:
            raise ValueError(f"unsupported OpenShell evidence fields: {sorted(unexpected)}")
        assignments = ["state = ?", "updated_at = ?"]
        values: list[object] = [state, time.time()]
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            values.append(
                _canonical_json(value) if key == "evidence_json" and isinstance(value, dict) else value
            )
        values.append(attempt_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE openshell_attempts SET {', '.join(assignments)} WHERE attempt_id = ?",
                values,
            )

    def latest_unresolved(self, effect_id: str) -> dict | None:
        placeholders = ",".join("?" for _ in self._UNRESOLVED)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM openshell_attempts
                WHERE effect_id = ? AND state IN ({placeholders})
                ORDER BY created_at DESC LIMIT 1
                """,
                (effect_id, *sorted(self._UNRESOLVED)),
            ).fetchone()
        return dict(row) if row is not None else None

    def get(self, attempt_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM openshell_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        return dict(row) if row is not None else None


class OpenShellExecutionEnvironment:
    """Optional OpenShell v0.1.2 wrapper beneath the Cognous executor.

    OpenShell constrains the synthetic workload environment. Institutional
    authority remains with the Control Plane and destination delivery remains
    independently represented by DurableRefundDestination.
    """

    def __init__(
        self,
        *,
        destination: DurableRefundDestination,
        policy: LocalExecutionPolicy,
        profile: OpenShellProfile | None = None,
        runner: OpenShellRunner | None = None,
    ):
        self.destination = destination
        self.policy = policy
        self.profile = profile or OpenShellProfile()
        self.profile.validate()
        self.runner = runner or SubprocessOpenShellRunner()
        self.evidence = OpenShellEvidenceStore(destination)
        self.local = LocalDestinationExecutor(destination, policy)

    def _host_env(self) -> dict[str, str]:
        if isinstance(self.runner, SubprocessOpenShellRunner):
            return self.runner.host_env()
        return {"PATH": os.environ.get("PATH", "")}

    def _operation_payload(self, snapshot: FrozenEnvelope) -> str:
        raw = _canonical_json(
            {
                "effect_id": snapshot.effect_id,
                "operation": snapshot.operation.canonical_dict(),
                "operation_digest": snapshot.operation.digest,
                "profile_digest": self.profile.profile_digest,
            }
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii")

    def _spec_digest(self, snapshot: FrozenEnvelope) -> str:
        return commitment(
            {
                "adapter_version": OPENSHELL_ADAPTER_VERSION,
                "profile_digest": self.profile.profile_digest,
                "effect_id": snapshot.effect_id,
                "operation_digest": snapshot.operation.digest,
                "executable": self.profile.executable,
                "argv_shape": [
                    "-c",
                    _sha256_text(_FIXED_OPERATION_SCRIPT),
                    "cognous-operation",
                    _sha256_text(self._operation_payload(snapshot)),
                ],
                "target": snapshot.operation.target,
                "payload_commitment": snapshot.operation.payload_commitment,
                "workdir": self.profile.workdir,
                "network_destinations": list(self.profile.network_destinations),
                "credential_refs": list(self.profile.credential_refs),
                "cpu": self.profile.cpu,
                "memory": self.profile.memory,
                "timeout_seconds": self.profile.timeout_seconds,
                "image": self.profile.image,
                "policy_digest": self.profile.policy_digest,
                "upstream_release": OPENSHELL_UPSTREAM_RELEASE,
                "upstream_commit": OPENSHELL_UPSTREAM_COMMIT,
            }
        )

    def _sandbox_name(self, snapshot: FrozenEnvelope, attempt_id: str) -> str:
        effect_part = hashlib.sha256(snapshot.effect_id.encode("utf-8")).hexdigest()[:12]
        attempt_part = attempt_id.replace("-", "")[:8]
        return f"cgn-{effect_part}-{attempt_part}"

    def _run(self, argv: Sequence[str], *, timeout: int | None = None) -> OpenShellCommandResult:
        return self.runner.run(
            argv,
            timeout_seconds=timeout or self.profile.timeout_seconds,
            env=self._host_env(),
        )

    def _runtime_version(self) -> str:
        result = self._run(["openshell", "--version"], timeout=10)
        if result.returncode != 0:
            raise RuntimeError("OpenShell version probe failed")
        value = result.stdout.strip() or result.stderr.strip()
        if "0.1.2" not in value:
            raise RuntimeError(
                f"unsupported OpenShell runtime; expected 0.1.2, observed {value!r}"
            )
        return value

    @staticmethod
    def _load_policy(text: str) -> dict:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError(
                "PyYAML is required for OpenShell policy observation"
            ) from exc
        value = yaml.safe_load(text)
        if not isinstance(value, dict):
            raise RuntimeError("OpenShell returned a non-object policy")
        return value

    def _policy_view(self, sandbox_name: str, view: str) -> tuple[dict, str]:
        if view not in {"--base", "--full"}:
            raise ValueError("invalid OpenShell policy view")
        result = self._run(
            ["openshell", "policy", "get", sandbox_name, view],
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"OpenShell policy observation failed for {view}")
        policy = self._load_policy(result.stdout)
        return policy, _sha256_text(_canonical_json(policy))

    @staticmethod
    def _extract_string(value: object, key: str) -> str | None:
        if isinstance(value, dict):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
            for child in value.values():
                found = OpenShellExecutionEnvironment._extract_string(child, key)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = OpenShellExecutionEnvironment._extract_string(child, key)
                if found:
                    return found
        return None

    def _delete_sandbox(self, sandbox_name: str) -> None:
        self._run(["openshell", "sandbox", "delete", sandbox_name], timeout=15)

    def _create_sandbox(self, sandbox_name: str) -> tuple[str | None, str, str]:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".json", delete=False
        ) as handle:
            handle.write(self.profile.policy_json)
            policy_path = handle.name
        try:
            argv = [
                "openshell",
                "sandbox",
                "create",
                "--name",
                sandbox_name,
                "--policy",
                policy_path,
                "--from",
                self.profile.image,
                "--cpu",
                self.profile.cpu,
                "--memory",
                self.profile.memory,
                "--detach",
                "--output",
                "json",
            ]
            result = self._run(argv, timeout=max(60, self.profile.timeout_seconds))
        finally:
            Path(policy_path).unlink(missing_ok=True)
        if result.returncode != 0:
            raise RuntimeError(
                "OpenShell sandbox creation failed: " + result.stderr.strip()[:500]
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("OpenShell sandbox create returned invalid JSON") from exc
        sandbox_id = self._extract_string(payload, "id")
        base, base_digest = self._policy_view(sandbox_name, "--base")
        if _canonical_json(base) != _canonical_json(self.profile.policy()):
            raise PermissionError("OpenShell base policy differs from requested policy")
        _, effective_digest = self._policy_view(sandbox_name, "--full")
        return sandbox_id, base_digest, effective_digest

    def execute_snapshot(
        self, snapshot: FrozenEnvelope, *, simulate: str | None = None
    ) -> ExecutionResult:
        self.policy.check(snapshot.operation)

        existing = self.destination.observe_bound(snapshot)
        if existing["state"] in {"applied", "partial"}:
            return self.local.execute_snapshot(snapshot, simulate=simulate)

        unresolved = self.evidence.latest_unresolved(snapshot.effect_id)
        if unresolved is not None:
            return ExecutionResult(
                status="unknown",
                decision_id=snapshot.decision_id,
                effect_id=snapshot.effect_id,
                attempt_id=unresolved["attempt_id"],
                attempted=False,
                acknowledged=False,
                observed_state=existing["state"],
                newly_executed=False,
                observation=existing,
                error="prior OpenShell attempt is unresolved; observe before retry",
            )

        attempt_id = snapshot.requested_attempt_id or str(uuid.uuid4())
        spec_digest = self._spec_digest(snapshot)
        sandbox_name = self._sandbox_name(snapshot, attempt_id)
        evidence = {
            "adapter_version": OPENSHELL_ADAPTER_VERSION,
            "profile_digest": self.profile.profile_digest,
            "spec_digest": spec_digest,
            "image": self.profile.image,
            "executable": self.profile.executable,
            "workdir": self.profile.workdir,
            "cpu": self.profile.cpu,
            "memory": self.profile.memory,
            "timeout_seconds": self.profile.timeout_seconds,
            "network_destinations": list(self.profile.network_destinations),
            "credential_refs": list(self.profile.credential_refs),
            "target": snapshot.operation.target,
            "payload_commitment": snapshot.operation.payload_commitment,
        }
        self.evidence.begin(
            attempt_id=attempt_id,
            effect_id=snapshot.effect_id,
            operation_digest=snapshot.operation.digest,
            spec_digest=spec_digest,
            sandbox_name=sandbox_name,
            requested_policy_digest=self.profile.policy_digest,
            evidence=evidence,
        )

        sandbox_created = False
        try:
            runtime_version = self._runtime_version()
            sandbox_id, base_digest, effective_before = self._create_sandbox(sandbox_name)
            sandbox_created = True
            self.evidence.update(
                attempt_id,
                state="sandbox_started",
                sandbox_id=sandbox_id,
                runtime_version=runtime_version,
                base_policy_digest=base_digest,
                effective_policy_digest_before=effective_before,
            )

            encoded = self._operation_payload(snapshot)
            command = [
                "openshell",
                "sandbox",
                "exec",
                sandbox_name,
                "--",
                self.profile.executable,
                "-c",
                _FIXED_OPERATION_SCRIPT,
                "cognous-operation",
                encoded,
            ]
            self.evidence.update(attempt_id, state="process_dispatched")
            try:
                result = self._run(command)
            except subprocess.TimeoutExpired as exc:
                self.evidence.update(
                    attempt_id,
                    state="unknown",
                    error=f"OpenShell exec timeout: {exc}",
                )
                return ExecutionResult(
                    status="unknown",
                    decision_id=snapshot.decision_id,
                    effect_id=snapshot.effect_id,
                    attempt_id=attempt_id,
                    attempted=True,
                    acknowledged=False,
                    observed_state="absent",
                    newly_executed=False,
                    error="OpenShell process timeout; outcome requires observation",
                )

            if result.returncode != 0:
                self.evidence.update(
                    attempt_id,
                    state="process_failed",
                    error=result.stderr.strip()[:1000],
                )
                return ExecutionResult(
                    status="failed",
                    decision_id=snapshot.decision_id,
                    effect_id=snapshot.effect_id,
                    attempt_id=attempt_id,
                    attempted=True,
                    acknowledged=True,
                    observed_state="absent",
                    newly_executed=False,
                    error="OpenShell process exited nonzero",
                )

            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if not lines or lines[-1] != encoded:
                self.evidence.update(
                    attempt_id,
                    state="unknown",
                    error="OpenShell process acknowledgement did not match frozen operation",
                )
                return ExecutionResult(
                    status="unknown",
                    decision_id=snapshot.decision_id,
                    effect_id=snapshot.effect_id,
                    attempt_id=attempt_id,
                    attempted=True,
                    acknowledged=False,
                    observed_state="absent",
                    newly_executed=False,
                    error="OpenShell acknowledgement mismatch; hold",
                )

            base_after, base_after_digest = self._policy_view(sandbox_name, "--base")
            _, effective_after = self._policy_view(sandbox_name, "--full")
            if (
                _canonical_json(base_after) != _canonical_json(self.profile.policy())
                or base_after_digest != base_digest
                or effective_after != effective_before
            ):
                self.evidence.update(
                    attempt_id,
                    state="unknown",
                    effective_policy_digest_after=effective_after,
                    error="OpenShell policy changed during execution",
                )
                return ExecutionResult(
                    status="unknown",
                    decision_id=snapshot.decision_id,
                    effect_id=snapshot.effect_id,
                    attempt_id=attempt_id,
                    attempted=True,
                    acknowledged=True,
                    observed_state="absent",
                    newly_executed=False,
                    error="OpenShell policy changed during execution; hold",
                )

            self.evidence.update(
                attempt_id,
                state="environment_observed",
                effective_policy_digest_after=effective_after,
            )

            bound = replace(snapshot, requested_attempt_id=attempt_id)
            local_result = self.local.execute_snapshot(bound, simulate=simulate)
            terminal_state = (
                "destination_applied"
                if local_result.observed_state == "applied"
                else "destination_" + local_result.status
            )
            self.evidence.update(
                attempt_id,
                state=terminal_state,
                evidence_json={
                    **evidence,
                    "sandbox_id": sandbox_id,
                    "runtime_version": runtime_version,
                    "base_policy_digest": base_digest,
                    "effective_policy_digest": effective_after,
                    "destination_status": local_result.status,
                    "destination_observed_state": local_result.observed_state,
                },
                error=local_result.error,
            )
            return local_result
        except PermissionError as exc:
            self.evidence.update(attempt_id, state="denied", error=str(exc))
            return ExecutionResult(
                status="denied",
                decision_id=snapshot.decision_id,
                effect_id=snapshot.effect_id,
                attempt_id=attempt_id,
                attempted=sandbox_created,
                acknowledged=False,
                observed_state="absent",
                newly_executed=False,
                error=str(exc),
            )
        except FileNotFoundError:
            self.evidence.update(
                attempt_id, state="unsupported", error="OpenShell binary not found"
            )
            return ExecutionResult(
                status="failed",
                decision_id=snapshot.decision_id,
                effect_id=snapshot.effect_id,
                attempt_id=attempt_id,
                attempted=False,
                acknowledged=False,
                observed_state="absent",
                newly_executed=False,
                error="OpenShell v0.1.2 runtime is not installed",
            )
        except Exception as exc:
            state = "unknown" if sandbox_created else "startup_failed"
            self.evidence.update(attempt_id, state=state, error=str(exc))
            return ExecutionResult(
                status="unknown" if sandbox_created else "failed",
                decision_id=snapshot.decision_id,
                effect_id=snapshot.effect_id,
                attempt_id=attempt_id,
                attempted=sandbox_created,
                acknowledged=False,
                observed_state="absent",
                newly_executed=False,
                error=str(exc),
            )
        finally:
            record = self.evidence.get(attempt_id)
            if sandbox_created and record is not None and record["state"] not in self.evidence._UNRESOLVED:
                try:
                    self._delete_sandbox(sandbox_name)
                except Exception:
                    # Effect/delivery state remains authoritative; cleanup failure
                    # is residual environment evidence, not proof of reversal.
                    pass

    def request_cancel(self, effect_id: str) -> dict:
        """Request sandbox stop and observe it; never claim reversal/prevention."""
        record = self.evidence.latest_unresolved(effect_id)
        if record is None:
            return {"requested": False, "reason": "no unresolved OpenShell attempt"}
        attempt_id = record["attempt_id"]
        sandbox_name = record["sandbox_name"]
        self.evidence.update(attempt_id, state="cancel_requested")
        stop = self._run(["openshell", "sandbox", "stop", sandbox_name], timeout=15)
        observed = self._run(
            ["openshell", "sandbox", "get", sandbox_name, "--output", "json"],
            timeout=15,
        )
        observation = {
            "stop_returncode": stop.returncode,
            "get_returncode": observed.returncode,
            "sandbox": observed.stdout.strip()[:4000],
        }
        self.evidence.update(
            attempt_id,
            state="unknown",
            evidence_json=observation,
            error=None
            if stop.returncode == 0 and observed.returncode == 0
            else "cancellation request or observation failed",
        )
        return {
            "requested": True,
            "attempt_id": attempt_id,
            "observation": observation,
            "effect_prevented_or_reversed": False,
        }
