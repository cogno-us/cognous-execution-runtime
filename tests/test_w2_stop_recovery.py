"""W2 qualification for the existing local process stop/restart path."""
from __future__ import annotations

import multiprocessing as mp
import sqlite3
import time

import pytest

from engine.atomic_local_control_plane_executor import AtomicLocalControlPlaneExecutor
from engine.local_authority_effect import AtomicAuthorityEffectDestination
from engine.safe_executor import snapshot_envelope
from test_local_authority_effect import BASE, effect_rows, policy, setup_atomic


def _blocked_worker(root, env, claim_id, stage, reached):
    class BarrierDestination(AtomicAuthorityEffectDestination):
        def _transaction_stage(self, current):
            if current == stage:
                reached.set()
                while True:
                    time.sleep(60)

    destination = BarrierDestination(root, clock=lambda: BASE)
    destination.execute_claim_atomic(
        claim_id,
        snapshot_envelope(env),
        attempt_id=f"stop-{stage}",
    )


def _attempt_rows(destination):
    with sqlite3.connect(destination.path) as conn:
        return conn.execute(
            "SELECT attempt_id,effect_id,decision_id FROM attempts ORDER BY attempt_id"
        ).fetchall()


@pytest.mark.parametrize(
    ("stage", "expected_claim", "expected_effects", "expected_attempts", "expected_status"),
    [
        ("after_begin", "issued", 0, 0, "hold"),
        ("after_effect_insert_before_commit", "issued", 0, 0, "hold"),
        ("after_commit", "consumed", 1, 1, "applied"),
    ],
)
def test_local_terminate_restart_boundaries_are_distinct(
    tmp_path,
    stage,
    expected_claim,
    expected_effects,
    expected_attempts,
    expected_status,
):
    _, env, destination, claim = setup_atomic(tmp_path)
    ctx = mp.get_context("spawn")
    reached = ctx.Event()
    proc = ctx.Process(
        target=_blocked_worker,
        args=(str(tmp_path), env, claim.claim_id, stage, reached),
    )
    proc.start()
    assert reached.wait(10)

    # Stop request: parent asks the OS to terminate this one local worker.
    proc.terminate()

    # Stop acknowledgement and local dispatch closure: the child is observed exited.
    proc.join(10)
    assert proc.exitcode is not None
    assert proc.is_alive() is False

    # Quiescence observation is separate from the stop acknowledgement. We only
    # assert this local destination remains unchanged after the stopped worker exits.
    reopened = AtomicAuthorityEffectDestination(tmp_path, clock=lambda: BASE)
    first_effects = effect_rows(reopened)
    first_attempts = _attempt_rows(reopened)
    time.sleep(0.02)
    assert effect_rows(reopened) == first_effects
    assert _attempt_rows(reopened) == first_attempts
    assert reopened.claim_state(claim.claim_id) == expected_claim
    assert len(first_effects) == expected_effects
    assert len(first_attempts) == expected_attempts

    # Destination reconciliation is a separate observation step. It never
    # represents the stop as rollback and never grants blind retry permission.
    recovery = reopened.reconcile_claim(claim.claim_id, snapshot_envelope(env))
    assert recovery["status"] == expected_status
    assert recovery["retry_eligible"] is False

    if expected_effects:
        assert first_effects[0]["effect_id"] == env.effect_id
        assert first_attempts[0][0] == f"stop-{stage}"
        assert first_attempts[0][1] == env.effect_id
        assert first_attempts[0][2] == env.decision_id


def test_lost_ack_restart_preserves_effect_and_attempt_identity_without_blind_retry(tmp_path):
    _, env, destination, claim = setup_atomic(tmp_path)
    executor = AtomicLocalControlPlaneExecutor(
        workflow=object(),
        destination=destination,
        policy=policy(env.operation),
    )

    first = executor.execute(
        envelope=env,
        claim_id=claim.claim_id,
        simulate="lost_ack",
        attempt_id="attempt-lost-ack",
    )
    assert first.status == "unknown"
    assert first.attempt_id == "attempt-lost-ack"
    assert first.acknowledged is False
    assert first.observed_state == "applied"
    assert first.observation["retry_eligible"] is False

    before_effects = effect_rows(destination)
    before_attempts = _attempt_rows(destination)
    assert len(before_effects) == 1
    assert before_effects[0]["effect_id"] == env.effect_id
    assert before_attempts == [("attempt-lost-ack", env.effect_id, env.decision_id)]

    reopened = AtomicAuthorityEffectDestination(tmp_path, clock=lambda: BASE)
    recovery = reopened.reconcile_claim(claim.claim_id, snapshot_envelope(env))
    assert recovery["status"] == "applied"
    assert recovery["retry_eligible"] is False
    assert effect_rows(reopened) == before_effects
    assert _attempt_rows(reopened) == before_attempts

    # A repeated execution request after restart does not create a second effect
    # or a second durable execution attempt. Historical effect identity survives.
    repeated = AtomicLocalControlPlaneExecutor(
        workflow=object(),
        destination=reopened,
        policy=policy(env.operation),
    ).execute(
        envelope=env,
        claim_id=claim.claim_id,
        attempt_id="attempt-blind-retry",
    )
    assert repeated.status == "denied"
    assert repeated.attempted is False
    assert effect_rows(reopened) == before_effects
    assert _attempt_rows(reopened) == before_attempts


def test_stop_after_commit_does_not_claim_rollback(tmp_path):
    _, env, destination, claim = setup_atomic(tmp_path)
    ctx = mp.get_context("spawn")
    reached = ctx.Event()
    proc = ctx.Process(
        target=_blocked_worker,
        args=(str(tmp_path), env, claim.claim_id, "after_commit", reached),
    )
    proc.start()
    assert reached.wait(10)
    proc.terminate()
    proc.join(10)
    assert proc.exitcode is not None

    reopened = AtomicAuthorityEffectDestination(tmp_path, clock=lambda: BASE)
    assert reopened.claim_state(claim.claim_id) == "consumed"
    rows = effect_rows(reopened)
    assert len(rows) == 1
    assert rows[0]["effect_id"] == env.effect_id
    assert reopened.reconcile_claim(
        claim.claim_id, snapshot_envelope(env)
    )["status"] == "applied"
