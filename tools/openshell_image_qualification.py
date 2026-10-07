#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bounded qualification of the packaged synthetic OpenShell worker image.

This exercises the exact image worker entry point without OpenShell. Docker is
only the container transport here; live OpenShell transport/confinement remain
separate, unverified boundaries.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from engine.openshell_environment import OpenShellRefundDestination
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION,
    ExecutionEnvelope,
    ExecutionOperation,
    commitment,
    snapshot_envelope,
)


def _operation(*, target: str = "urn:cognous:synthetic-account:17", grant_id: str = "grant-worker17") -> ExecutionOperation:
    payload = {"refund_reason": "worker17-qualification", "case_id": "case-17"}
    return ExecutionOperation(
        actor="worker17",
        principal="synthetic-principal",
        institution_id="urn:cognous:synthetic-institution",
        authority_domain="synthetic-refunds",
        manifest_id="urn:cognous:manifest:refund",
        manifest_version="1.1",
        manifest_digest="sha256:" + "1" * 64,
        proposal_commitment="sha256:" + "2" * 64,
        action_id="refund.issue",
        adapter_id="moltbot-safe.synthetic-refund",
        target=target,
        payload=payload,
        payload_commitment=commitment(payload),
        requested_permissions=("refund:write",),
        amount=17.25,
        unit="USD",
        effects=1,
        authority_context_id="urn:cognous:authority-context:worker17",
        requirement_id="urn:cognous:requirement:refund",
        grant_id=grant_id,
        grant_revision="1",
        effective_max_effects=10,
    )


def _request(effect_id: str, *, target: str = "urn:cognous:synthetic-account:17", mode: str = "commit") -> tuple[dict[str, Any], Any]:
    envelope = ExecutionEnvelope(
        version=EXECUTION_ENVELOPE_VERSION,
        decision_id=f"decision-{effect_id}",
        effect_id=effect_id,
        operation=_operation(target=target),
    )
    snapshot = snapshot_envelope(envelope)
    return {
        "version": "0.1.0",
        "mode": mode,
        "snapshot": dataclasses.asdict(snapshot),
    }, snapshot


def _docker(image: str, state: Path, command: list[str], *, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker", "run", "--rm",
            "--user", "1000:1000",
            "--mount", f"type=bind,src={state},dst=/var/lib/cognous",
            image,
            *command,
        ],
        input=stdin,
        text=True,
        capture_output=True,
        check=check,
        timeout=60,
    )


def _worker(image: str, state: Path, request: dict[str, Any], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _docker(
        image,
        state,
        ["/usr/local/bin/python3", "-I", "/opt/cognous/openshell_worker.py"],
        stdin=json.dumps(request, allow_nan=False, separators=(",", ":")),
        check=check,
    )


def _json_stdout(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    value = json.loads(proc.stdout)
    if type(value) is not dict:
        raise AssertionError("worker output must be a JSON object")
    return value


def _seed_partial(image: str, state: Path, request: dict[str, Any]) -> None:
    code = r"""
import json, sys
sys.path.insert(0, "/opt/cognous")
from engine.safe_executor import DurableRefundDestination, FrozenEnvelope, FrozenOperation, validate_snapshot
request = json.loads(sys.stdin.read())
value = dict(request["snapshot"])
op = dict(value.pop("operation"))
op["requested_permissions"] = tuple(op["requested_permissions"])
snapshot = FrozenEnvelope(operation=FrozenOperation(**op), **value)
validate_snapshot(snapshot)
DurableRefundDestination("/var/lib/cognous").commit(snapshot, simulate="partial")
"""
    _docker(
        image,
        state,
        ["/usr/local/bin/python3", "-I", "-c", code],
        stdin=json.dumps(request, allow_nan=False, separators=(",", ":")),
    )


def _rows(state: Path) -> list[dict[str, Any]]:
    path = state / "refunds.sqlite3"
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM effects ORDER BY effect_id")]


def _packaging_checks(image: str, state: Path) -> dict[str, Any]:
    uid = _docker(image, state, ["/usr/bin/id", "-u"]).stdout.strip()
    gid = _docker(image, state, ["/usr/bin/id", "-g"]).stdout.strip()
    if (uid, gid) != ("1000", "1000"):
        raise AssertionError(f"image must run as 1000:1000, got {uid}:{gid}")

    writable = _docker(
        image,
        state,
        ["/usr/local/bin/python3", "-c", "from pathlib import Path; p=Path('/var/lib/cognous/worker17-write'); p.write_text('ok'); p.unlink()"],
    )
    if writable.returncode != 0:
        raise AssertionError("intended state path is not writable")

    protected = _docker(
        image,
        state,
        ["/usr/local/bin/python3", "-c", "from pathlib import Path; Path('/opt/cognous/should-not-write').write_text('x')"],
        check=False,
    )
    if protected.returncode == 0:
        raise AssertionError("/opt/cognous unexpectedly writable by configured nonroot user")

    license_paths = [
        "/opt/cognous/LICENSE",
        "/opt/cognous/LICENSE-APACHE-2.0",
        "/opt/cognous/NOTICE",
        "/opt/cognous/engine/LICENSE",
    ]
    license_check = _docker(
        image,
        state,
        ["/usr/local/bin/python3", "-c",
         "from pathlib import Path; import sys; paths=sys.argv[1:]; missing=[p for p in paths if not Path(p).is_file() or Path(p).stat().st_size == 0]; print('\\n'.join(missing)); raise SystemExit(bool(missing))",
         *license_paths],
    )
    if license_check.stdout.strip():
        raise AssertionError("required license/notice files missing")

    return {
        "configured_uid": int(uid),
        "configured_gid": int(gid),
        "state_path_writable": True,
        "code_path_nonwritable": True,
        "license_paths": license_paths,
    }


def qualify(image: str, state: Path) -> dict[str, Any]:
    state.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o777)

    first_request, first_snapshot = _request("effect-worker17-applied")
    first = _json_stdout(_worker(image, state, first_request))
    if first.get("duplicate") is not False:
        raise AssertionError("first worker commit must be new")
    first_observation = first.get("observation")
    OpenShellRefundDestination._validate_observation(first_snapshot, first_observation)
    if first_observation["state"] != "applied":
        raise AssertionError("first commit must observe applied")

    duplicate = _json_stdout(_worker(image, state, first_request))
    if duplicate.get("duplicate") is not True:
        raise AssertionError("same-operation duplicate must be suppressed")
    OpenShellRefundDestination._validate_observation(first_snapshot, duplicate["observation"])

    observe_request = dict(first_request)
    observe_request["mode"] = "observe"
    restarted_observation = _json_stdout(_worker(image, state, observe_request))
    OpenShellRefundDestination._validate_observation(first_snapshot, restarted_observation)
    if restarted_observation != first_observation:
        raise AssertionError("restart observation changed destination evidence")

    conflict_request, _ = _request(
        "effect-worker17-applied",
        target="urn:cognous:synthetic-account:conflict",
    )
    conflict = _worker(image, state, conflict_request, check=False)
    if conflict.returncode == 0:
        raise AssertionError("conflicting reuse of effect_id was accepted")

    partial_request, partial_snapshot = _request("effect-worker17-partial")
    _seed_partial(image, state, partial_request)
    partial_observe = dict(partial_request)
    partial_observe["mode"] = "observe"
    partial = _json_stdout(_worker(image, state, partial_observe))
    OpenShellRefundDestination._validate_observation(partial_snapshot, partial)
    if partial["state"] != "partial":
        raise AssertionError("partial destination row was not preserved as partial")

    # Negative adapter vectors are derived from actual worker bytes.
    missing = json.loads(json.dumps(first_observation))
    del missing["destination_state"]["state"]
    try:
        OpenShellRefundDestination._validate_observation(first_snapshot, missing)
    except PermissionError:
        missing_rejected = True
    else:
        raise AssertionError("missing embedded state was accepted")

    contradictory = json.loads(json.dumps(first_observation))
    contradictory["destination_state"]["target"] = "urn:cognous:synthetic-account:attacker"
    # Deliberately retain the copied operation digest: it must not authenticate
    # contradictory destination content.
    try:
        OpenShellRefundDestination._validate_observation(first_snapshot, contradictory)
    except PermissionError:
        contradictory_rejected = True
    else:
        raise AssertionError("copied digest authenticated contradictory content")

    rows = _rows(state)
    if len(rows) != 2:
        raise AssertionError(f"expected exactly two retained effects, got {len(rows)}")
    by_id = {row["effect_id"]: row for row in rows}
    applied = by_id["effect-worker17-applied"]
    partial_row = by_id["effect-worker17-partial"]
    expected_applied = first_observation["destination_state"]
    if applied["operation_digest"] != expected_applied["operation_digest"]:
        raise AssertionError("stored operation digest differs from worker observation")
    if applied["target"] != first_snapshot.operation.target or float(applied["amount"]) != float(first_snapshot.operation.amount):
        raise AssertionError("stored applied row differs from exact operation")
    if applied["state"] != "applied" or partial_row["state"] != "partial":
        raise AssertionError("stored destination states are incorrect")

    packaging = _packaging_checks(image, state)
    return {
        "image": image,
        "worker_entrypoint": ["/usr/local/bin/python3", "-I", "/opt/cognous/openshell_worker.py"],
        "host_worker": "not executed by this script",
        "container_image": "executed",
        "mocked_openshell_transport": "not used; Docker carried exact worker stdin/stdout",
        "live_openshell_execution": "unexecuted",
        "live_confinement_enforcement": "unexecuted",
        "normal_commit": first,
        "duplicate_same_operation": duplicate,
        "restart_observation": restarted_observation,
        "partial_observation": partial,
        "conflicting_effect_id_rejected": True,
        "negative_vectors": {
            "missing_embedded_state_rejected": missing_rejected,
            "contradictory_content_with_copied_digest_rejected": contradictory_rejected,
        },
        "destination_rows": rows,
        "packaging": packaging,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--state-dir")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if args.state_dir:
        state = Path(args.state_dir).resolve()
        result = qualify(args.image, state)
    else:
        with tempfile.TemporaryDirectory(prefix="moltbot-openshell-worker17-") as tmp:
            result = qualify(args.image, Path(tmp))

    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
