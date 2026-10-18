"""Characterisation of attempt-event transitions (known limit, no behaviour change).

The runtime has no transition table: ``DurableRefundDestination.transition_attempt`` inserts any status string for any
retained attempt in any order, and ``engine/producer_contract.py`` checks only that an event belongs to a retained
attempt. The executors themselves only emit the sequences listed in ``ODES_EXECUTOR_TRANSITIONS``, which is a copy of the
ODES execution-attempt-lifecycle executor table at ODES 1ed3bb4 (rule 14: executed -> unknown and partial -> unknown are
allowed). The first group of tests pins what the destination accepts today so a later constraint is deliberate; the
second group is the valid-producer control: every sequence a real executor run produces is inside the ODES table.
"""
from __future__ import annotations

import itertools
import sqlite3

import pytest

from engine.producer_contract import export_execution_artifacts
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION, DurableRefundDestination, ExecutionEnvelope, LocalDestinationExecutor,
    snapshot_envelope)
from test_producer_contract import operation, policy

ODES_EXECUTOR_TRANSITIONS = {
    "attempted": {"executed", "partial", "failed", "unknown", "denied", "reconciled"},
    "executed": {"unknown", "reconciled"},
    "partial": {"unknown", "reconciled"},
    "unknown": {"reconciled", "executed", "partial", "failed"},
    "reconciled": set(), "failed": set(), "denied": set(),
}


def _snapshot(effect_id="effect-1", attempt=None):
    return snapshot_envelope(ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", effect_id, operation(), attempt))


def _begin(tmp_path):
    dest = DurableRefundDestination(tmp_path / "d")
    return dest, dest.begin_attempt("attempt-1", _snapshot(attempt="attempt-1"))


# --- what the destination accepts today (known limit) -----------------------------------------------------------------

@pytest.mark.parametrize("status", ["banana", "", "EXECUTED", "acknowledged", "observed"])
def test_any_status_string_is_accepted_known_limit(tmp_path, status):
    dest, attempt = _begin(tmp_path)
    dest.transition_attempt(attempt, status)
    assert [e["status"] for e in dest.attempt_history(attempt)] == ["attempted", status]


def test_every_ordered_pair_of_statuses_is_accepted_known_limit(tmp_path):
    statuses = list(ODES_EXECUTOR_TRANSITIONS)
    disallowed = [(a, b) for a, b in itertools.product(statuses, repeat=2) if b not in ODES_EXECUTOR_TRANSITIONS[a]]
    assert disallowed  # for example anything after a terminal status, or a second 'attempted'
    for i, (before, after) in enumerate(disallowed):
        dest = DurableRefundDestination(tmp_path / f"d{i}")
        attempt = dest.begin_attempt("attempt-1", _snapshot(attempt="attempt-1"))
        if before != "attempted":
            dest.transition_attempt(attempt, before)
        dest.transition_attempt(attempt, after)  # accepted: no exception
        assert [e["status"] for e in dest.attempt_history(attempt)][-2:] == [before, after]


def test_success_event_can_follow_a_failed_event_known_limit(tmp_path):
    dest, attempt = _begin(tmp_path)
    dest.transition_attempt(attempt, "failed", "ProviderError")
    dest.transition_attempt(attempt, "executed")
    assert [e["status"] for e in dest.attempt_history(attempt)] == ["attempted", "failed", "executed"]


def test_transition_needs_a_retained_attempt_and_a_status(tmp_path):
    dest, attempt = _begin(tmp_path)
    with pytest.raises(KeyError):
        dest.transition_attempt("no-such-attempt", "executed")
    with pytest.raises(sqlite3.IntegrityError):
        dest.transition_attempt(attempt, None)  # NOT NULL is the only constraint on the column
    assert [e["status"] for e in dest.attempt_history(attempt)] == ["attempted"]


def test_export_checks_event_binding_only_not_event_order_known_limit(tmp_path):
    op = operation()
    env = ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)
    dest = DurableRefundDestination(tmp_path / "d")
    result = LocalDestinationExecutor(dest, policy(op)).execute_snapshot(snapshot_envelope(env))
    assert result.status == "executed"
    dest.transition_attempt(result.attempt_id, "attempted")  # second start event, after the terminal one
    exported = export_execution_artifacts(env, result, dest, repository_revision="test")
    assert [e["status"] for e in exported["attempt_events"]] == ["attempted", "executed", "attempted"]


# --- valid-producer control: what the executors really emit is inside the ODES table ----------------------------------

class Lost(DurableRefundDestination):
    def commit(self, snapshot, **kwargs):
        raise TimeoutError("acknowledgement unavailable before any write")


class Boom(DurableRefundDestination):
    def commit(self, snapshot, **kwargs):
        raise RuntimeError("x")


class ObserveBroken(DurableRefundDestination):
    def observe_bound(self, snapshot):
        raise ConnectionError("x")


def _sequences(tmp_path):
    op = operation()
    out = {}

    def run(name, dest, **kw):
        res = LocalDestinationExecutor(dest, policy(op)).execute_snapshot(_snapshot(), **kw)
        out[name] = [e["status"] for e in dest.attempt_history(res.attempt_id)]
        return dest

    run("executed", DurableRefundDestination(tmp_path / "a"))
    run("partial", DurableRefundDestination(tmp_path / "b"), simulate="partial")
    run("lost_ack_applied", DurableRefundDestination(tmp_path / "h"), simulate="lost_ack")
    run("lost_ack_unknown", Lost(tmp_path / "c"))
    run("failed", Boom(tmp_path / "e"))
    run("denied", ObserveBroken(tmp_path / "f"))
    dup = run("first", DurableRefundDestination(tmp_path / "g"))
    run("reconciled_duplicate", dup)  # same effect, new attempt: observed applied before dispatch
    return out


def test_real_executor_sequences_are_inside_the_odes_table(tmp_path):
    seqs = _sequences(tmp_path)
    assert seqs["executed"] == ["attempted", "executed"]
    assert seqs["partial"] == ["attempted", "partial"]
    assert seqs["lost_ack_applied"] == ["attempted", "reconciled"]
    assert seqs["lost_ack_unknown"] == ["attempted", "unknown"]
    assert seqs["failed"] == ["attempted", "failed"]
    assert seqs["denied"] == ["attempted", "denied"]
    assert seqs["reconciled_duplicate"] == ["attempted", "reconciled"]
    for name, seq in seqs.items():
        assert seq[0] == "attempted", name
        for before, after in zip(seq, seq[1:]):
            assert after in ODES_EXECUTOR_TRANSITIONS[before], (name, before, after)


def test_odes_table_copy_keeps_rule_14_edges():
    assert "unknown" in ODES_EXECUTOR_TRANSITIONS["executed"]
    assert "unknown" in ODES_EXECUTOR_TRANSITIONS["partial"]
