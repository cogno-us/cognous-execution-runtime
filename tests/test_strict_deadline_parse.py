"""Stored deadlines are read with the same strict instant grammar the writers produce.

A stored deadline that is not an RFC 3339 instant with an explicit offset is reported as invalid, never interpreted
leniently. Valid stored deadlines (the form the writers emit) keep working.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from engine.recovery_resolution import SyntheticRecoveryLedger
from engine.revocation_closeout import OrderingEvidence, RevocationCloseoutStore

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)

# Accepted by datetime.fromisoformat on current Python versions, outside the strict grammar.
LENIENT_ONLY = [
    "2026-10-11 10:00:00+00:00",          # space separator
    "2026-10-11T10:00:00.1234567+00:00",  # seven fraction digits
    "20261011T100000+0000",               # basic format
]


def _pair(tmp_path):
    old = RevocationCloseoutStore(tmp_path, clock=lambda: NOW)
    old.record_item(effect_id="effect", attempt_id="attempt", decision_id="decision", grant_id="effect",
                    grant_revision="r1", state="executing", ordering=OrderingEvidence(1, "synthetic:queue"))
    old.record_revocation(grant_id="effect", grant_revision="r1", revocation=OrderingEvidence(2, "synthetic:revocation"),
                          owner="synthetic:owner", deadline=NOW - timedelta(hours=1),
                          reconciliation_ref="synthetic:reconciliation")
    new = SyntheticRecoveryLedger(old.path, synthetic=True, clock=lambda: NOW)
    return old, new


def _set_deadline(old, value):
    with sqlite3.connect(old.path) as conn:
        conn.execute("UPDATE revocation_dispositions_v1 SET deadline=?", (value,))


def test_valid_stored_deadline_still_counts_overdue(tmp_path):
    old, new = _pair(tmp_path)
    assert old.overdue_report(now=NOW)["count"] == 1
    report = new.metrics()
    assert (report["current_overdue"], report["invalid_or_missing_deadlines"]) == (1, 0)


@pytest.mark.parametrize("value", LENIENT_ONLY)
def test_closeout_overdue_report_refuses_a_non_strict_stored_deadline(tmp_path, value):
    old, _ = _pair(tmp_path)
    _set_deadline(old, value)
    with pytest.raises(ValueError):
        old.overdue_report(now=NOW)


@pytest.mark.parametrize("value", LENIENT_ONLY)
def test_recovery_metrics_report_a_non_strict_stored_deadline_as_invalid(tmp_path, value):
    old, new = _pair(tmp_path)
    _set_deadline(old, value)
    report = new.metrics()
    assert report["invalid_or_missing_deadlines"] == 1
    assert report["current_unresolved"] == 1
    assert report["current_overdue"] == 0
