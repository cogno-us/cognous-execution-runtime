"""Component-local byte-identity checks: valid flows export the same bytes before and after the grant-status, envelope
binding and exception-type changes.

Each flow is run with fixed identities, its export (export_execution_artifacts) and the destination's table set are
canonicalised with wall-clock members removed (created_at, committed_at, started_at come from time.time()), and the
SHA-256 of the canonical bytes is compared with the value recorded by running this same file on main eeb4583a, the
revision before the change. Flows: the default adapter-route destination, the opt-in operation key
(operation-identity/0.1) and the opt-in dedupe-ttl/0.1 destination (N4: execute, in-progress, refused second effect, landed
duplicate). The atomic route's valid flow is covered with the Control Plane package in
test_atomic_route_valid_flow_identity.py.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3

import pytest

from engine.dedupe_destination import DedupeTtlOperationDestination
from engine.operation_identity_destination import OperationIdentityDestination
from engine.producer_contract import export_execution_artifacts
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION, DurableRefundDestination, ExecutionEnvelope, LocalDestinationExecutor, snapshot_envelope)
from test_producer_contract import operation, policy

OP = "op-order-100/refund-1"
WALL_CLOCK = {"created_at", "committed_at", "started_at"}


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in WALL_CLOCK}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def tables(dest):
    with sqlite3.connect(dest.path) as conn:
        return sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"))


def digest(value) -> str:
    raw = json.dumps(_strip(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def envelope(effect_id, attempt_id, *, max_effects=1):
    op = dataclasses.replace(operation(), effective_max_effects=max_effects)
    return ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-" + effect_id, effect_id, op, attempt_id)


def run(dest, env, **kwargs):
    pol = dataclasses.replace(policy(env.operation), max_effects=env.operation.effective_max_effects)
    result = LocalDestinationExecutor(dest, pol).execute_snapshot(snapshot_envelope(env), **kwargs)
    return {"status": result.status, "error": result.error,
            "export": export_execution_artifacts(env, result, dest, repository_revision="byte-identity")}


def flow_default(tmp_path):
    dest = DurableRefundDestination(tmp_path / "d")
    env = envelope("effect-1", "attempt-1")
    first = run(dest, env)
    again = run(dest, envelope("effect-1", "attempt-2"))
    return {"runs": [first, again], "tables": tables(dest)}


def flow_operation_key(tmp_path):
    dest = OperationIdentityDestination(tmp_path / "d")
    first = run(dest, envelope("effect-a", "attempt-a", max_effects=2), operation_id=OP)
    second = run(dest, envelope("effect-b", "attempt-b", max_effects=2), operation_id=OP)
    return {"runs": [first, second], "tables": tables(dest)}


class Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


def flow_dedupe_ttl(tmp_path):
    clock = Clock()
    dest = DedupeTtlOperationDestination(tmp_path / "d", dedupe_ttl_seconds=600, clock=clock)
    first = run(dest, envelope("effect-a", "attempt-a", max_effects=2), operation_id=OP)
    second = run(dest, envelope("effect-b", "attempt-b", max_effects=2), operation_id=OP)
    clock.now += 600
    dest.defer_next_commit()
    held = run(dest, envelope("effect-c", "attempt-c", max_effects=2), operation_id=OP)
    in_progress = run(dest, envelope("effect-d", "attempt-d", max_effects=2), operation_id=OP)
    dest.land()
    landed = run(dest, envelope("effect-c", "attempt-e", max_effects=2), operation_id=OP)
    return {"runs": [first, second, held, in_progress, landed], "tables": tables(dest)}


# Recorded on main eeb4583a (before the change) by running this file's flows there.
RECORDED = {
    "default": "e1ae4aac001ace61c73fac30cdc187bca993902d6c6986d3317a33c754219a42",
    "operation_key": "7dfc68d0fcd3dd1e600c7d311eb733ba06f6cefb051df2f0694c550c2136e05a",
    "dedupe_ttl": "9933cc9a8238f5c399b323028333462d70e0bece87a47d1cba27b8b833f3fea4",
}
FLOWS = {"default": flow_default, "operation_key": flow_operation_key, "dedupe_ttl": flow_dedupe_ttl}


@pytest.mark.parametrize("name", sorted(FLOWS))
def test_default_valid_flow_export_bytes_are_unchanged(tmp_path, name):
    assert digest(FLOWS[name](tmp_path)) == RECORDED[name]


def test_flows_exercise_the_expected_outcomes(tmp_path):
    assert [r["status"] for r in flow_default(tmp_path / "a")["runs"]] == ["executed", "reconciled"]
    assert [(r["status"], r["error"]) for r in flow_operation_key(tmp_path / "b")["runs"]] == [
        ("executed", None), ("failed", "operation_already_committed")]
    assert [(r["status"], r["error"]) for r in flow_dedupe_ttl(tmp_path / "c")["runs"]] == [
        ("executed", None), ("failed", "operation_already_committed"),
        ("unknown", "synthetic acknowledgement lost; commit has not landed yet"),
        ("unknown", "destination_in_progress"), ("reconciled", None)]
