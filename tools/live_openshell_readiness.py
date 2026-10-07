#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only readiness gate for live OpenShell qualification.

This tool does not provision, execute, stop, delete, publish, or mutate an
OpenShell sandbox. It checks whether an explicitly authorized isolated live
qualification environment already exists and, when a config is supplied,
performs only the adapter's read-only gateway/sandbox inspection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

ACCEPTED_SOURCE_SHA = "ff4eab5228c19f73aeb2a61d48046dd29111c6a9"
REVIEWED_IMAGE_SOURCE_SHA = "112a5c4b8a3337f6afbf52c399e5e1286625876f"
OPEN_SHELL_VERSION = "0.1.2"
OPEN_SHELL_COMMIT = "6648bd0c290efbc41ba131ee9831ee45cd431f94"
EXECUTOR_PRODUCER_PROFILE_VERSION = "2.0.0"
EXECUTION_ENVELOPE_VERSION = "0.2.0"
CONTROL_PLANE_COMMIT = "2ea9528eeb87e14ff10f05de06473122b9df540f"
MANIFEST_COMMIT = "46c950bed37fe3812000895430bc0312d29e37ce"
ALVORADA_COMMIT = "fb3d97938969a89e149e8ff8db2756091d1233fc"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(argv: list[str], *, env: dict[str, str] | None = None, timeout: int = 10) -> dict[str, Any]:
    started = _utcnow()
    try:
        proc = subprocess.run(
            argv,
            text=True,
            capture_output=True,
            check=False,
            env=env,
            timeout=timeout,
        )
        return {
            "argv": argv,
            "started_at": started,
            "finished_at": _utcnow(),
            "exit_code": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": argv,
            "started_at": started,
            "finished_at": _utcnow(),
            "exit_code": None,
            "timeout": True,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "").strip() if isinstance(exc.stderr, str) else "",
        }
    except OSError as exc:
        return {
            "argv": argv,
            "started_at": started,
            "finished_at": _utcnow(),
            "exit_code": None,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def _git_source_sha() -> str | None:
    result = _run(["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"])
    if result.get("exit_code") == 0 and result.get("stdout"):
        return str(result["stdout"])
    return None


def _binary_check(name: str, explicit: str | None = None) -> dict[str, Any]:
    resolved = explicit or shutil.which(name)
    if not resolved:
        return {"status": "missing", "path": None}
    path = Path(resolved).expanduser()
    try:
        path = path.resolve(strict=True)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        return {"status": "unusable", "path": str(path), "error": str(exc)}
    return {"status": "present", "path": str(path), "sha256": digest}


def _container_runtime(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    if not path:
        return {"status": "missing", "path": None}
    result = _run([path, "info"], timeout=15)
    return {
        "status": "usable" if result.get("exit_code") == 0 else "unusable",
        "path": path,
        "command": result,
    }


def _validate_image_digest(value: str | None) -> bool:
    if not value or "@sha256:" not in value:
        return False
    prefix, digest = value.rsplit("@sha256:", 1)
    return bool(prefix) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def _validate_inspected_environment(config: Any, info: dict[str, Any]) -> dict[str, Any]:
    """Apply the accepted adapter's read-only sandbox validation semantics.

    This mirrors OpenShellRefundDestination._inspect after its durable binding
    check, but deliberately omits destination construction and event writes.
    """
    configured_policy = json.loads(config.policy_json)
    expected = {
        "id": config.sandbox_id,
        "name": config.sandbox,
        "workspace": config.workspace,
        "phase": "Ready",
        "current_policy_version": config.policy_version,
        "policy_source": "sandbox",
        "revision": config.policy_version,
        "policy": configured_policy,
    }
    mismatches = {
        key: {"expected": value, "observed": info.get(key)}
        for key, value in expected.items()
        if info.get(key) != value
    }
    if mismatches:
        raise PermissionError(
            "sandbox identity, readiness or policy changed: "
            + ",".join(sorted(mismatches))
        )

    admission_expected = {
        "state": "accepted",
        "policy_version": config.policy_version,
        "policy_hash": config.policy_hash,
        "config_revision": config.config_revision,
        "provider_env_revision": config.provider_env_revision,
    }
    admission = info.get("configuration_admission") or {}
    admission_mismatches = {
        key: {"expected": value, "observed": admission.get(key)}
        for key, value in admission_expected.items()
        if admission.get(key) != value
    }
    if admission_mismatches:
        raise PermissionError(
            "sandbox configuration is not the pinned admitted revision: "
            + ",".join(sorted(admission_mismatches))
        )

    return {
        "configured_or_provisioning_attributed": {
            "sandbox_id": config.sandbox_id,
            "sandbox_name": config.sandbox,
            "workspace": config.workspace,
            "image": config.image,
            "worker_executable": list(config.executable),
            "workdir": config.workdir,
            "cpu": config.cpu,
            "memory": config.memory,
            "policy_version": config.policy_version,
            "policy_hash": config.policy_hash,
            "config_revision": config.config_revision,
            "provider_env_revision": config.provider_env_revision,
            "policy": configured_policy,
            "config_digest": config.digest,
            "evidence_meaning": (
                "values retained from the trusted provisioning/config receipt; "
                "the pinned sandbox get API does not independently attest image, "
                "CPU or memory identity"
            ),
        },
        "observed": {
            "sandbox_id": info.get("id"),
            "sandbox_name": info.get("name"),
            "workspace": info.get("workspace"),
            "phase": info.get("phase"),
            "current_policy_version": info.get("current_policy_version"),
            "policy_source": info.get("policy_source"),
            "revision": info.get("revision"),
            "policy": info.get("policy"),
            "configuration_admission": info.get("configuration_admission"),
            "runtime_evidence": info.get("runtime_evidence"),
        },
        "validation": {
            "adapter_semantics_matched": True,
            "independently_observed": [
                "sandbox_id",
                "sandbox_name",
                "workspace",
                "phase",
                "policy_source",
                "policy_version",
                "policy_content",
                "configuration_admission",
                "gateway_runtime",
            ],
            "not_independently_observed_by_pinned_api": [
                "image_identity",
                "cpu_limit",
                "memory_limit",
                "worker_executable",
                "workdir",
            ],
        },
    }


def _inspect_existing_environment(
    *,
    binary: str,
    home: str,
    config_path: str,
) -> dict[str, Any]:
    from engine.openshell_environment import OpenShellCLI, OpenShellConfig

    value = json.loads(Path(config_path).read_text())
    value["executable"] = tuple(value["executable"])
    config = OpenShellConfig(**value)
    cli = OpenShellCLI(binary, home)
    info = cli.inspect(config)
    validated = _validate_inspected_environment(config, info)
    return {
        "status": "inspected_and_matched",
        **validated,
    }


def assess(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    source_sha = _git_source_sha()
    openshell = _binary_check("openshell", args.binary)
    docker = _container_runtime("docker")
    podman = _container_runtime("podman")
    kvm = {"present": Path("/dev/kvm").exists()}

    blockers: list[str] = []
    if openshell["status"] != "present":
        blockers.append("pinned OpenShell CLI is unavailable")
    if docker["status"] != "usable":
        blockers.append("usable Docker runtime is unavailable")
    if not args.authorized_isolated_environment:
        blockers.append("explicit authorization for an isolated qualification environment was not supplied")
    if not args.config:
        blockers.append("existing qualified sandbox config was not supplied")
    if not args.image:
        blockers.append("immutable worker image digest was not supplied")
    elif not _validate_image_digest(args.image):
        blockers.append("worker image must be supplied as name@sha256:<64 lowercase hex>")

    home_status: dict[str, Any]
    if args.home:
        home = Path(args.home).expanduser()
        home_status = {
            "path": str(home),
            "exists": home.is_dir(),
        }
        if not home.is_dir():
            blockers.append("dedicated OpenShell client HOME is unavailable")
    else:
        home_status = {"path": None, "exists": False}
        blockers.append("dedicated OpenShell client HOME was not supplied")

    inspection: dict[str, Any] = {"status": "unexecuted"}
    if (
        args.authorized_isolated_environment
        and openshell["status"] == "present"
        and args.config
        and args.home
        and Path(args.config).is_file()
        and Path(args.home).is_dir()
    ):
        try:
            inspection = _inspect_existing_environment(
                binary=str(openshell["path"]),
                home=args.home,
                config_path=args.config,
            )
            configured = inspection["configured_or_provisioning_attributed"]
            if args.image and configured.get("image") != args.image:
                blockers.append("supplied image digest does not match configured/provisioning-attributed image")
        except Exception as exc:
            inspection = {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            blockers.append("read-only OpenShell gateway/sandbox inspection failed")
    elif args.config and not Path(args.config).is_file():
        blockers.append("supplied sandbox config file does not exist")

    result = {
        "schema": "cognous.live-openshell-readiness.v1",
        "recorded_at": _utcnow(),
        "classification": "ready" if not blockers else "blocked",
        "live_qualification_executed": False,
        "source": {
            "repository": "cogno-us/moltbot-safe",
            "actual_source_sha": source_sha,
            "accepted_image_qualification_merge": ACCEPTED_SOURCE_SHA,
            "reviewed_image_source": REVIEWED_IMAGE_SOURCE_SHA,
        },
        "contracts": {
            "executor_producer_profile": EXECUTOR_PRODUCER_PROFILE_VERSION,
            "execution_envelope": EXECUTION_ENVELOPE_VERSION,
            "control_plane_commit": CONTROL_PLANE_COMMIT,
            "manifest_commit": MANIFEST_COMMIT,
            "alvorada_commit": ALVORADA_COMMIT,
            "openshell_version": OPEN_SHELL_VERSION,
            "openshell_commit": OPEN_SHELL_COMMIT,
        },
        "operator_inputs": {
            "authorized_isolated_environment": bool(args.authorized_isolated_environment),
            "config_supplied": bool(args.config),
            "home": home_status,
            "configured_image_assertion": args.image,
        },
        "prerequisites": {
            "openshell": openshell,
            "docker": docker,
            "podman": podman,
            "dev_kvm": kvm,
            "platform": {
                "system": platform.system(),
                "machine": platform.machine(),
                "release": platform.release(),
            },
        },
        "read_only_environment_inspection": inspection,
        "blockers": blockers,
        "readiness_meaning": {
            "ready_establishes": [
                "explicit operator opt-in for an isolated qualification environment",
                "pinned OpenShell CLI path/hash is locally available",
                "usable local Docker runtime is available",
                "dedicated client HOME and existing config are available",
                "supplied immutable image digest matches provisioning-attributed config",
                "live gateway reports pinned healthy local Docker runtime",
                "live sandbox identity/readiness/effective policy match config",
                "live configuration admission state/hash/revisions match config",
            ],
            "ready_does_not_establish": [
                "actual sandbox image identity independently observed by OpenShell",
                "actual CPU or memory limits independently observed by OpenShell",
                "worker executable/workdir independently attested by OpenShell",
                "live execution behavior",
                "filesystem or network confinement enforcement",
                "rollback, exactly-once delivery, or production readiness",
            ],
        },
        "qualification_matrix": {
            "packaged_worker_compatibility": "accepted_preexisting_evidence",
            "live_openshell_adapter_execution": "unexecuted",
            "live_confinement_enforcement": "unexecuted",
            "live_timeout_cancellation": "unexecuted",
            "live_network_denial": "unexecuted",
            "live_filesystem_denial": "unexecuted",
            "live_resource_limits": "unexecuted",
        },
        "safety": {
            "provisioned_infrastructure": False,
            "published_image": False,
            "used_production_credentials": False,
            "mutated_sandbox": False,
            "meaning": "readiness only; no live qualification claim",
        },
    }
    return result, 0 if not blockers else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", help="path to the pinned OpenShell CLI")
    parser.add_argument("--home", help="dedicated synthetic OpenShell client HOME")
    parser.add_argument("--config", help="existing provisioned sandbox config JSON")
    parser.add_argument("--image", help="reviewed worker image at immutable name@sha256 digest")
    parser.add_argument(
        "--authorized-isolated-environment",
        action="store_true",
        help="operator attests this gateway/sandbox is explicitly authorized and isolated for qualification",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    result, status = assess(args)
    output = Path(args.output)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
