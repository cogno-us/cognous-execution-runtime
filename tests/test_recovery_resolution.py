from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from threading import Barrier

import pytest

from engine.recovery_resolution import SyntheticObservation, SyntheticRecoveryLedger
from engine.revocation_closeout import OrderingEvidence, RevocationCloseoutStore

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


def original(store, effect="effect", attempt="attempt"):
    store.record_item(effect_id=effect, attempt_id=attempt, decision_id="decision",
                      grant_id=effect, grant_revision="r1", state="executing",
                      ordering=OrderingEvidence(1, "synthetic:queue"))
    store.record_revocation(grant_id=effect, grant_revision="r1",
                            revocation=OrderingEvidence(2, "synthetic:revocation"),
                            owner="synthetic:owner", deadline=NOW-timedelta(hours=1),
                            reconciliation_ref="synthetic:reconciliation")


@pytest.fixture
def pair(tmp_path):
    old = RevocationCloseoutStore(tmp_path, clock=lambda: NOW)
    original(old)
    new = SyntheticRecoveryLedger(old.path, synthetic=True, clock=lambda: NOW)
    return old, new


def observation(**changes):
    event = SyntheticObservation(
        event_id="event-1", effect_id="effect", attempt_id="attempt",
        adapter_id="synthetic:adapter", client_reference="synthetic:client",
        state="unknown", observed_at=NOW, ordering_domain="synthetic:domain", order=3,
        observation_ref="synthetic:observation", observation_digest="0" * 64,
        observer_ref="synthetic:observer", watermark_ref="synthetic:watermark",
        provider_acceptance_ref="synthetic:unavailable", evidence_location_ref="synthetic:location",
    )
    return replace(event, **changes)


def append(ledger, event):
    current = ledger.current(event.attempt_id)
    return ledger.append(event, expected_revision=current["revision"], predecessor_digest=current["head_digest"])


def complete(**changes):
    return observation(**{"state": "applied", "complete_coverage": True, "final": True, **changes})


def test_unknown_to_applied_survives_restart_with_provenance_and_v1_untouched(pair):
    old, new = pair
    before = old.dispositions()
    append(new, observation())
    append(new, complete(event_id="event-2", order=4))
    restarted = SyntheticRecoveryLedger(old.path, synthetic=True, clock=lambda: NOW)
    assert restarted.current("attempt")["work_state"] == "resolved"
    history = restarted.history("attempt")
    assert len(history) == 2
    for row in history:
        body = json.loads(row["body"])
        assert body["observation"]["observer_ref"] == "synthetic:observer"
        assert body["observation"]["watermark_ref"] == "synthetic:watermark"
        assert json.loads(row["receipt"])["assurance"] == "synthetic_admission"
    assert old.dispositions() == before
    assert old.closeout("attempt") is None
    assert restarted.current("attempt")["retry_eligible"] is False
    assert restarted.current("attempt")["settlement_verified"] is False


def test_metrics_include_unobserved_then_remove_resolved_from_overdue(pair):
    old, new = pair
    report = new.metrics()
    assert report["current_unresolved"] == report["current_overdue"] == 1
    assert report["work_states"]["awaiting_observation"] == 1
    append(new, complete())
    report = new.metrics()
    assert report["historical_in_doubt"] == 1
    assert report["current_unresolved"] == report["current_overdue"] == 0
    assert old.overdue_report(now=NOW)["count"] == 1


@pytest.mark.parametrize("state,coverage,final,work", [
    ("unknown", False, False, "awaiting_finality"),
    ("absent", True, True, "awaiting_finality"),
    ("partial", True, True, "partial_followup"),
    ("applied", False, True, "awaiting_finality"),
    ("applied", True, False, "awaiting_finality"),
    ("applied", True, True, "resolved"),
])
def test_outcome_and_finality_are_separate(pair, state, coverage, final, work):
    result = append(pair[1], observation(state=state, complete_coverage=coverage, final=final))
    assert result["current"]["work_state"] == work
    assert result["current"]["execution_authority"] == "none"
    assert result["current"]["retry_eligible"] is False


def test_partial_followed_by_full_coverage(pair):
    new = pair[1]
    append(new, observation(state="partial"))
    assert append(new, complete(event_id="event-2", order=4))["current"]["work_state"] == "resolved"


def test_duplicate_returns_original_admission_and_later_conflict(pair):
    new = pair[1]
    first = new.append(complete(), expected_revision=0, predecessor_digest=None)
    append(new, observation(event_id="event-2", state="absent", order=4))
    duplicate = new.append(complete(), expected_revision=0, predecessor_digest=None)
    assert duplicate["admission"] == first["admission"]
    assert duplicate["current"]["work_state"] == "conflict_review"
    assert duplicate["current"]["observation"] == "applied"
    assert len(new.history("attempt")) == 2
    append(new, complete(event_id="event-3", order=5))
    assert new.current("attempt")["work_state"] == "conflict_review"


def test_same_id_changed_body_records_minimized_disagreement(pair):
    new = pair[1]
    append(new, complete())
    result = append(new, observation(state="absent", order=4))
    assert result["current"]["work_state"] == "conflict_review"
    row = new.history("attempt")[-1]
    assert row["kind"] == "disagreement"
    assert set(json.loads(row["body"])) == {"conflicting_digest", "event_id"}


@pytest.mark.parametrize("changes", [
    {"ordering_domain": "synthetic:other"}, {"order": 2},
    {"adapter_id": "synthetic:other"}, {"client_reference": "synthetic:other"},
    {"final": False}, {"complete_coverage": False},
])
def test_incomparable_and_regressive_evidence_holds(pair, changes):
    new = pair[1]
    append(new, complete())
    params = {"event_id": "event-2", "order": 4, **changes}
    result = append(new, complete(**params))
    assert result["current"]["work_state"] == "conflict_review"
    assert result["current"]["finality"] == "unestablished"


@pytest.mark.parametrize("changes", [
    {"effect_id": "other"}, {"attempt_id": "missing"},
    {"observed_at": NOW+timedelta(seconds=1)},
    {"observed_at": NOW-timedelta(days=2)},
    {"observed_at": NOW.replace(tzinfo=None)},
    {"observation_digest": "invalid"}, {"observer_ref": ""},
    {"order": True}, {"final": 1}, {"state": "settled"},
])
def test_invalid_inputs_do_not_promote_or_append(pair, changes):
    new = pair[1]
    with pytest.raises(ValueError):
        append(new, observation(**changes))
    assert new.current("attempt")["revision"] == 0
    assert new.history("attempt") == []


def test_competing_writers_have_one_winner_and_stale_loser(pair):
    old, new = pair
    barrier = Barrier(2)
    def submit(event_id):
        ledger = SyntheticRecoveryLedger(old.path, synthetic=True, clock=lambda: NOW)
        barrier.wait()
        try:
            ledger.append(observation(event_id=event_id), expected_revision=0, predecessor_digest=None)
            return "admitted"
        except ValueError as error:
            assert "stale stream revision" in str(error)
            return "stale"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, ["first", "second"])) == ["admitted", "stale"]
    assert len(new.history("attempt")) == new.current("attempt")["revision"] == 1


def test_projection_failure_rolls_back_event_and_restart(pair):
    old, new = pair
    with sqlite3.connect(old.path) as conn:
        conn.execute("CREATE TRIGGER test_projection_failure BEFORE INSERT ON recovery_synthetic_projection BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        append(new, complete())
    reopened = SyntheticRecoveryLedger(old.path, synthetic=True, clock=lambda: NOW)
    assert reopened.history("attempt") == []
    assert reopened.current("attempt")["revision"] == 0
    with sqlite3.connect(old.path) as conn:
        conn.execute("DROP TRIGGER test_projection_failure")
    assert append(reopened, complete())["current"]["revision"] == 1


def test_disabled_writer_and_append_only_controls(pair):
    old, new = pair
    disabled = SyntheticRecoveryLedger(old.path, synthetic=True, writer_enabled=False)
    with pytest.raises(RuntimeError, match="writer disabled"):
        append(disabled, observation())
    append(new, observation())
    with sqlite3.connect(old.path) as conn:
        for sql in ["DELETE FROM recovery_synthetic_events", "UPDATE recovery_synthetic_events SET body='{}'"]:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(sql)


def test_ambiguous_and_missing_attempts_are_separate_work(pair):
    old, new = pair
    original(old, effect="second", attempt="attempt")
    original(old, effect="missing", attempt=None)
    with pytest.raises(ValueError, match="ambiguous"):
        append(new, observation())
    report = new.metrics()
    assert report["historical_in_doubt"] == report["unattributable_effects"] == 3
    assert report["current_unresolved"] == 0


def test_missing_deadline_and_legacy_closeout_do_not_fabricate_resolution(pair):
    old, new = pair
    old.close_original_attempt(attempt_id="attempt", observation_state="partial",
                               observation=OrderingEvidence(3, "synthetic:legacy"),
                               observation_ref="synthetic:legacy")
    with sqlite3.connect(old.path) as conn:
        conn.execute("UPDATE revocation_dispositions_v1 SET deadline=NULL")
    report = new.metrics()
    assert report["invalid_or_missing_deadlines"] == 1
    assert report["current_unresolved"] == 1
    assert report["work_states"]["awaiting_observation"] == 1
    assert old.closeout("attempt").closed is True


def test_synthetic_opt_in_and_existing_database_required(tmp_path):
    with pytest.raises(ValueError, match="production admission unsupported"):
        SyntheticRecoveryLedger(tmp_path / "missing.db")
    with pytest.raises(sqlite3.OperationalError):
        SyntheticRecoveryLedger(tmp_path / "missing.db", synthetic=True)
    assert not (tmp_path / "missing.db").exists()


def test_canonical_digest_normalizes_timezone_and_duplicate_is_idempotent(pair):
    new = pair[1]
    first = new.append(observation(), expected_revision=0, predecessor_digest=None)
    equivalent = observation(observed_at=NOW.astimezone(timezone(timedelta(hours=2))))
    second = new.append(equivalent, expected_revision=0, predecessor_digest=None)
    assert first == second
    assert len(new.history("attempt")) == 1
    import hashlib
    row = new.history("attempt")[0]
    assert hashlib.sha256(row["body"].encode("utf-8")).hexdigest() == row["digest"]


def test_predecessor_and_cross_attempt_event_reuse_fail_closed(pair):
    old, new = pair
    original(old, effect="other-effect", attempt="other-attempt")
    append(new, complete())
    with pytest.raises(ValueError, match="stale stream revision"):
        new.append(observation(event_id="event-2"), expected_revision=1, predecessor_digest="wrong")
    with pytest.raises(ValueError, match="another attempt"):
        append(new, observation(effect_id="other-effect", attempt_id="other-attempt"))
    assert new.current("other-attempt")["revision"] == 0
    assert len(new.history("attempt")) == 1


@pytest.mark.parametrize("change", ["ambiguous", "decision_changed", "disposition_missing"])
def test_current_revalidates_original_identity_instead_of_returning_stale_resolution(pair, change):
    old, new = pair
    append(new, complete())
    retained = new.history("attempt")
    if change == "ambiguous":
        original(old, effect="second", attempt="attempt")
    else:
        with sqlite3.connect(old.path) as conn:
            if change == "decision_changed":
                conn.execute("UPDATE runtime_items_v1 SET decision_id='changed'")
            else:
                conn.execute("DELETE FROM revocation_dispositions_v1")
    with pytest.raises(ValueError, match="HOLD"):
        new.current("attempt")
    assert new.history("attempt") == retained
    assert new.metrics()["work_states"]["resolved"] == 0


@pytest.mark.parametrize("boundary,committed", [
    ("event", False), ("projection", False), ("commit", True),
])
def test_abrupt_process_exit_preserves_transaction_boundary(pair, boundary, committed):
    import subprocess
    import sys
    from pathlib import Path

    old, new = pair
    # Exit from the actual append transaction without exception handlers or cleanup.
    script = '''
import os, runpy, sqlite3, sys
from engine.recovery_resolution import SyntheticRecoveryLedger
fixtures = runpy.run_path("tests/test_recovery_resolution.py")
ledger = SyntheticRecoveryLedger(sys.argv[1], synthetic=True, clock=lambda: fixtures["NOW"])
connect = sqlite3.connect
boundary = sys.argv[2]
class ExitConnection(sqlite3.Connection):
    def execute(self, sql, *args):
        result = super().execute(sql, *args)
        target = {"event": "INSERT INTO recovery_synthetic_events",
                  "projection": "INSERT INTO recovery_synthetic_projection",
                  "commit": "COMMIT"}[boundary]
        if sql.startswith(target):
            os._exit(73)
        return result
sqlite3.connect = lambda *args, **kwargs: connect(*args, factory=ExitConnection, **kwargs)
ledger.append(fixtures["complete"](), expected_revision=0, predecessor_digest=None)
raise SystemExit("boundary was not reached")
'''
    result = subprocess.run([sys.executable, "-c", script, str(old.path), boundary],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 73, result.stderr
    reopened = SyntheticRecoveryLedger(old.path, synthetic=True, clock=lambda: NOW)
    assert len(reopened.history("attempt")) == int(committed)
    assert reopened.current("attempt")["revision"] == int(committed)
    assert reopened.metrics()["current_unresolved"] == int(not committed)
    replay = reopened.append(complete(), expected_revision=0, predecessor_digest=None)
    assert replay["current"]["revision"] == 1
    assert len(reopened.history("attempt")) == 1
