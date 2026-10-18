"""A holder that dies before its commit never leaves a success behind.

The holder is a forked child that exits hard (no exception handling, no rollback code runs) at a fixed point.
Route A: after the attempt is recorded and before the effect is written. Atomic route (needs the Control Plane
local-authority-effect contract, same guard as test_atomic_route_valid_flow_identity.py): after BEGIN IMMEDIATE and
after the effect insert, both before COMMIT. In every case no effect row exists afterwards, the crashed attempt has no
terminal success event, observation and reconciliation report no effect (never applied), and a repeated attempt id is
refused. Controls: a normal run executes, and after a crash on the atomic route the unconsumed claim is still usable once.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION, DurableRefundDestination, ExecutionEnvelope, LocalDestinationExecutor,
    snapshot_envelope)
from test_producer_contract import operation, policy

try:
    import agent_control_plane.local_authority_effect  # noqa: F401
    HAVE_CP = True
except ModuleNotFoundError:
    HAVE_CP = False

needs_cp = pytest.mark.skipif(not HAVE_CP, reason="needs the Control Plane local-authority-effect contract on PYTHONPATH")
SUCCESS = {"executed", "reconciled", "applied"}


def die_in_child(action) -> None:
    pid = os.fork()
    if pid == 0:
        code = 99
        try:
            action()
        except BaseException:
            code = 98
        finally:
            os._exit(code)
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 17, "holder did not die at the fault point"


def rows(path, table):
    with sqlite3.connect(path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class DiesBeforeWrite(DurableRefundDestination):
    def commit(self, snapshot, **kwargs):
        os._exit(17)


def route_a_env():
    op = operation()
    return op, ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)


def test_route_a_holder_dying_before_the_write_leaves_no_effect_and_no_success(tmp_path):
    op, env = route_a_env()
    dying = DiesBeforeWrite(tmp_path / "d")
    snap = snapshot_envelope(env)
    die_in_child(lambda: LocalDestinationExecutor(dying, policy(op)).execute_snapshot(snap))

    dest = DurableRefundDestination(tmp_path / "d")
    assert rows(dest.path, "effects") == 0
    [attempt_id] = [r[0] for r in sqlite3.connect(dest.path).execute("SELECT attempt_id FROM attempts")]
    history = dest.attempt_history(attempt_id)
    assert [e["status"] for e in history] == ["attempted"]
    assert not SUCCESS & {e["status"] for e in history}
    observed = LocalDestinationExecutor(dest, policy(op)).observe_historical(env)
    assert (observed.status, observed.observed_state) == ("observed", "absent")

    snap_same_attempt = snapshot_envelope(ExecutionEnvelope(
        EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op, attempt_id))
    again = LocalDestinationExecutor(dest, policy(op)).execute_snapshot(snap_same_attempt)
    assert again.status == "denied" and not again.newly_executed
    assert [e["status"] for e in dest.attempt_history(attempt_id)] == ["attempted"]


def test_control_route_a_normal_run_executes(tmp_path):
    op, env = route_a_env()
    result = LocalDestinationExecutor(DurableRefundDestination(tmp_path / "d"), policy(op)).execute_snapshot(
        snapshot_envelope(env))
    assert result.status == "executed"


def _atomic(tmp_path, stage):
    import test_atomic_route_valid_flow_identity as flow
    from engine.local_authority_effect import AtomicAuthorityEffectDestination

    class Dies(AtomicAuthorityEffectDestination):
        def _transaction_stage(self, name):
            if name == stage:
                os._exit(17)

    env, dest, claim, ex = flow.setup(tmp_path, cls=Dies)
    die_in_child(lambda: ex.execute(envelope=env, claim_id=claim.claim_id, attempt_id="attempt-1"))
    fresh = AtomicAuthorityEffectDestination(tmp_path / "atomic", clock=lambda: flow.BASE)
    return flow, env, claim, fresh


@needs_cp
@pytest.mark.parametrize("stage", ["after_begin", "after_effect_insert_before_commit"])
def test_atomic_holder_dying_before_commit_leaves_no_effect_and_no_success(tmp_path, stage):
    from engine.atomic_local_control_plane_executor import AtomicLocalControlPlaneExecutor

    flow, env, claim, fresh = _atomic(tmp_path, stage)
    assert rows(fresh.path, "effects") == 0
    assert fresh.claim_state(claim.claim_id) == "issued"
    assert fresh.budget_state("budget-1")["used_effects"] == 0
    ex = AtomicLocalControlPlaneExecutor(workflow=None, destination=fresh, policy=flow.policy(env.operation))
    result = ex.reconcile(claim_id=claim.claim_id, envelope=env)
    assert (result.status, result.observed_state) == ("observed", "unknown")
    assert result.observation["status"] == "hold" and result.observation["reason"] == "no_durable_effect_for_claim"
    assert result.observation["status"] not in {"applied", "partial"}


@needs_cp
def test_control_atomic_unconsumed_claim_executes_once_after_the_crash(tmp_path):
    from engine.atomic_local_control_plane_executor import AtomicLocalControlPlaneExecutor

    flow, env, claim, fresh = _atomic(tmp_path, "after_effect_insert_before_commit")
    ex = AtomicLocalControlPlaneExecutor(workflow=None, destination=fresh, policy=flow.policy(env.operation))
    first = ex.execute(envelope=env, claim_id=claim.claim_id, attempt_id="attempt-2")
    assert first.status == "executed" and rows(fresh.path, "effects") == 1
    second = ex.execute(envelope=env, claim_id=claim.claim_id, attempt_id="attempt-3")
    assert second.status != "executed" and rows(fresh.path, "effects") == 1
