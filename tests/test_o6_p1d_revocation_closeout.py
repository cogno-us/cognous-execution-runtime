from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.revocation_closeout import OrderingEvidence, RevocationCloseoutStore

BASE = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)


def store(tmp_path, **kwargs):
    return RevocationCloseoutStore(tmp_path, clock=lambda: BASE, **kwargs)


def add_item(
    ledger,
    *,
    effect,
    grant,
    revision="r1",
    state,
    order,
    attempt=None,
    completion_order=None,
):
    ledger.record_item(
        effect_id=effect,
        attempt_id=attempt,
        decision_id=f"decision-{effect}",
        grant_id=grant,
        grant_revision=revision,
        state=state,
        ordering=OrderingEvidence(order, f"queue:{effect}:{order}"),
        completion=(
            OrderingEvidence(completion_order, f"complete:{effect}:{completion_order}")
            if completion_order is not None
            else None
        ),
    )


def test_sweep_attributes_queued_executing_and_completed_items(tmp_path):
    ledger = store(tmp_path)
    add_item(ledger, effect="queued", grant="g1", state="queued", order=10)
    add_item(
        ledger,
        effect="executing",
        grant="g1",
        state="executing",
        order=20,
        attempt="attempt-executing",
    )
    add_item(
        ledger,
        effect="completed",
        grant="g1",
        state="completed",
        order=5,
        attempt="attempt-completed",
        completion_order=15,
    )
    result = ledger.record_revocation(
        grant_id="g1",
        grant_revision="r1",
        revocation=OrderingEvidence(30, "authority-log:g1:r1:30"),
        owner="ops:oncall",
        deadline=BASE + timedelta(hours=1),
        reconciliation_ref="reconcile:g1:r1",
    )
    assert {item.effect_id: item.status for item in result} == {
        "queued": "refused_before_execution",
        "executing": "in_doubt",
        "completed": "completed_before_revocation",
    }
    doubtful = next(item for item in result if item.effect_id == "executing")
    assert doubtful.owner == "ops:oncall"
    assert doubtful.reconciliation_ref == "reconcile:g1:r1"


def test_simultaneous_distinct_grant_revocations_preserve_item_attribution(tmp_path):
    ledger = store(tmp_path)
    add_item(ledger, effect="effect-a", grant="grant-a", revision="r7", state="queued", order=4)
    add_item(ledger, effect="effect-b", grant="grant-b", revision="r9", state="queued", order=4)
    a = ledger.record_revocation(
        grant_id="grant-a",
        grant_revision="r7",
        revocation=OrderingEvidence(8, "authority-log:simultaneous"),
    )
    b = ledger.record_revocation(
        grant_id="grant-b",
        grant_revision="r9",
        revocation=OrderingEvidence(8, "authority-log:simultaneous"),
    )
    assert [(x.effect_id, x.grant_id, x.grant_revision) for x in a] == [
        ("effect-a", "grant-a", "r7")
    ]
    assert [(x.effect_id, x.grant_id, x.grant_revision) for x in b] == [
        ("effect-b", "grant-b", "r9")
    ]


def test_c0_post_check_pre_commit_race_is_in_doubt_not_atomicity_claim(tmp_path):
    ledger = store(tmp_path)
    add_item(
        ledger,
        effect="race-effect",
        grant="g-race",
        state="executing",
        order=40,
        attempt="attempt-race",
    )
    result = ledger.record_revocation(
        grant_id="g-race",
        grant_revision="r1",
        revocation=OrderingEvidence(41, "authority-log:revoked-after-check"),
        owner="runtime-reconciliation",
        deadline=BASE + timedelta(minutes=30),
        reconciliation_ref="reconcile:race-effect",
    )
    assert result[0].status == "in_doubt"
    assert result[0].revocation_order == 41


def test_crash_restart_preserves_disposition_and_original_attempt_closeout(tmp_path):
    ledger = store(tmp_path)
    add_item(
        ledger,
        effect="lost-ack-effect",
        grant="g1",
        state="executing",
        order=10,
        attempt="attempt-original",
    )
    ledger.record_revocation(
        grant_id="g1",
        grant_revision="r1",
        revocation=OrderingEvidence(20, "authority-log:g1"),
        owner="ops",
        deadline=BASE + timedelta(minutes=15),
        reconciliation_ref="reconcile:lost-ack-effect",
    )
    reopened = store(tmp_path)
    assert reopened.dispositions()[0].status == "in_doubt"
    closed = reopened.close_original_attempt(
        attempt_id="attempt-original",
        observation_state="applied",
        observation=OrderingEvidence(30, "destination-watermark:30"),
        observation_ref="destination-observation:effect=lost-ack-effect",
    )
    assert closed.closed is True
    assert closed.attempt_id == "attempt-original"
    assert closed.effect_id == "lost-ack-effect"
    assert closed.retry_eligible is False

    restarted_again = store(tmp_path)
    retained = restarted_again.closeout("attempt-original")
    assert retained is not None
    assert retained.closed is True
    assert retained.observation_ref == "destination-observation:effect=lost-ack-effect"


@pytest.mark.parametrize("state", ["absent", "unknown"])
def test_missing_or_unknown_observation_never_permits_retry(tmp_path, state):
    ledger = store(tmp_path)
    add_item(
        ledger,
        effect=f"effect-{state}",
        grant="g1",
        state="executing",
        order=1,
        attempt=f"attempt-{state}",
    )
    result = ledger.close_original_attempt(
        attempt_id=f"attempt-{state}",
        observation_state=state,
        observation=OrderingEvidence(2, f"observer:{state}"),
        observation_ref=f"observation:{state}",
    )
    assert result.closed is False
    assert result.retry_eligible is False


def test_in_doubt_requires_owner_deadline_and_reconciliation_reference(tmp_path):
    ledger = store(tmp_path)
    add_item(ledger, effect="effect", grant="g1", state="executing", order=1)
    with pytest.raises(ValueError, match="named owner"):
        ledger.record_revocation(
            grant_id="g1",
            grant_revision="r1",
            revocation=OrderingEvidence(2, "authority-log"),
        )


def test_overdue_report_counts_and_ages_unresolved_items(tmp_path):
    ledger = store(tmp_path)
    add_item(ledger, effect="old", grant="g1", state="executing", order=1)
    add_item(ledger, effect="new", grant="g1", state="executing", order=1)
    ledger.record_revocation(
        grant_id="g1",
        grant_revision="r1",
        revocation=OrderingEvidence(2, "authority-log"),
        owner="ops",
        deadline=BASE - timedelta(minutes=10),
        reconciliation_ref="reconcile:g1",
    )
    report = ledger.overdue_report(now=BASE)
    assert report["count"] == 2
    assert report["oldest_age_seconds"] == 600
    assert report["youngest_age_seconds"] == 600


def test_disabled_disposition_writer_is_detectable_mutation_control(tmp_path):
    ledger = store(tmp_path, disposition_writer_enabled=False)
    add_item(ledger, effect="effect", grant="g1", state="queued", order=1)
    with pytest.raises(RuntimeError, match="writer disabled"):
        ledger.record_revocation(
            grant_id="g1",
            grant_revision="r1",
            revocation=OrderingEvidence(2, "authority-log"),
        )
    assert ledger.dispositions() == []


def test_disabled_closeout_writer_is_detectable_mutation_control(tmp_path):
    ledger = store(tmp_path, closeout_writer_enabled=False)
    add_item(
        ledger,
        effect="effect",
        grant="g1",
        state="executing",
        order=1,
        attempt="attempt-original",
    )
    with pytest.raises(RuntimeError, match="writer disabled"):
        ledger.close_original_attempt(
            attempt_id="attempt-original",
            observation_state="applied",
            observation=OrderingEvidence(2, "destination-watermark:2"),
            observation_ref="observation:effect",
        )
    assert ledger.closeout("attempt-original") is None


def test_item_admitted_concurrently_with_revocation_is_dispositioned_or_serialized(tmp_path, monkeypatch):
    import sqlite3

    ledger = store(tmp_path)
    add_item(ledger, effect="first", grant="g1", state="queued", order=1)
    original = RevocationCloseoutStore._disposition_for
    attempts = {"count": 0, "outcome": None}

    def racing_disposition(self, row, **kwargs):
        if attempts["count"] == 0:
            attempts["count"] += 1
            # A second writer admits a new item for the same grant while the
            # revocation sweep is between its read and its write.
            try:
                second = sqlite3.connect(ledger.path, timeout=0.05)
                with second:
                    second.execute(
                        """INSERT INTO runtime_items_v1(
                            effect_id,attempt_id,decision_id,grant_id,grant_revision,state,
                            state_order,ordering_ref,completed_order,completion_ref,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        ("late", None, "decision-late", "g1", "r1", "executing", 2,
                         "queue:late:2", None, None, BASE.isoformat()),
                    )
                second.close()
                attempts["outcome"] = "inserted"
            except sqlite3.OperationalError:
                attempts["outcome"] = "blocked"
        return original(self, row, **kwargs)

    monkeypatch.setattr(RevocationCloseoutStore, "_disposition_for", racing_disposition)
    result = ledger.record_revocation(
        grant_id="g1",
        grant_revision="r1",
        revocation=OrderingEvidence(3, "authority-log:g1:r1:3"),
        owner="ops:oncall",
        deadline=BASE + timedelta(hours=1),
        reconciliation_ref="reconcile:g1:r1",
    )
    dispositioned = {item.effect_id for item in result}
    if attempts["outcome"] == "inserted":
        # The concurrent writer committed: the sweep must have covered it.
        assert "late" in dispositioned
    else:
        # The concurrent writer was serialized behind the sweep's write lock.
        assert attempts["outcome"] == "blocked"
        assert dispositioned == {"first"}
    assert {item.effect_id for item in ledger.dispositions()} == dispositioned
