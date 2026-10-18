"""Profile exclusivity, amount exactness and closeout uniqueness (security scan remediation)."""
from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from engine.effective_grant import EffectiveGrantDestination
from engine.local_authority_effect import AtomicAuthorityEffectDestination
from engine.revocation_closeout import OrderingEvidence, RevocationCloseoutStore
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION,
    ExecutionEnvelope,
    LocalDestinationExecutor,
    MAX_EXACT_INTEGER_AMOUNT,
    exact_amount_equal,
    snapshot_envelope,
)
from tests.test_producer_contract import operation, policy

NOW = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)


def envelope(**changes):
    op = replace(operation(), **changes) if changes else operation()
    return ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)


# --- atomic route and effective-grant profile are never combined (both orders) -------------------------------------

def test_atomic_profile_refuses_a_database_with_an_effective_grant_profile(tmp_path):
    EffectiveGrantDestination(tmp_path)
    with pytest.raises(PermissionError, match="effective-grant database"):
        AtomicAuthorityEffectDestination(tmp_path, clock=lambda: NOW)


def test_effective_grant_profile_refuses_a_database_with_the_atomic_profile(tmp_path):
    AtomicAuthorityEffectDestination(tmp_path, clock=lambda: NOW)
    with pytest.raises(PermissionError, match="authority-effect database"):
        EffectiveGrantDestination(tmp_path)


def test_refused_combination_leaves_no_partial_state(tmp_path):
    EffectiveGrantDestination(tmp_path)
    with pytest.raises(PermissionError):
        AtomicAuthorityEffectDestination(tmp_path, clock=lambda: NOW)
    with sqlite3.connect(tmp_path / "refunds.sqlite3") as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "authority_effect_profile_v1" not in names


def test_late_effective_grant_table_is_refused_at_atomic_execution(tmp_path):
    pytest.importorskip("agent_control_plane.local_authority_effect")
    destination = AtomicAuthorityEffectDestination(tmp_path, clock=lambda: NOW)
    with sqlite3.connect(destination.path) as conn:
        conn.execute("CREATE TABLE effective_grants_v1 (grant_id TEXT PRIMARY KEY)")
    snapshot = snapshot_envelope(envelope())
    with pytest.raises(PermissionError, match="effective-grant database"):
        destination.execute_claim_atomic("claim-1", snapshot, attempt_id="attempt-1")


def test_positive_controls_each_profile_alone_still_works(tmp_path):
    AtomicAuthorityEffectDestination(tmp_path / "atomic", clock=lambda: NOW)
    grants = EffectiveGrantDestination(tmp_path / "grant")
    op = operation()
    digest = grants.set_effective_grant(principal=op.principal, grant_id=op.grant_id, revision=op.grant_revision,
                                        permissions=list(op.requested_permissions))
    result = LocalDestinationExecutor(grants, policy(op)).execute_snapshot(
        snapshot_envelope(envelope()), expected_effective_grant_digest=digest,
        effective_grant_derivation_version="effective-grant/0.1")
    assert result.status == "executed"


def test_effective_grant_id_cannot_be_rebound_to_another_principal(tmp_path):
    grants = EffectiveGrantDestination(tmp_path)
    grants.set_effective_grant(principal="p1", grant_id="g1", revision="1", permissions=["a"])
    grants.set_effective_grant(principal="p1", grant_id="g1", revision="2", permissions=["a", "b"])
    with pytest.raises(PermissionError, match="another principal"):
        grants.set_effective_grant(principal="p2", grant_id="g1", revision="3", permissions=["a"])


# --- amounts: exact, bounded integers ------------------------------------------------------------------------------

@pytest.mark.parametrize("amount", [MAX_EXACT_INTEGER_AMOUNT, 50, 50.0, 0, 1000.0000000000001])
def test_exactly_representable_amounts_are_admitted(amount):
    snapshot_envelope(envelope(amount=amount))


@pytest.mark.parametrize("amount", [
    MAX_EXACT_INTEGER_AMOUNT + 1, -(MAX_EXACT_INTEGER_AMOUNT + 1), 10 ** 30, True, False,
    Decimal("50"), "50", None, float("nan"), float("inf"), -1,
])
def test_inexact_or_non_numeric_amounts_are_refused(amount):
    with pytest.raises(ValueError):
        snapshot_envelope(envelope(amount=amount))


def test_integer_subclass_amount_is_refused():
    class Amount(int):
        pass

    with pytest.raises(ValueError):
        snapshot_envelope(envelope(amount=Amount(5)))


def test_amount_comparison_is_exact_not_binary64():
    assert exact_amount_equal(50, 50.0)
    assert exact_amount_equal(1000.0, 1000.0)
    assert not exact_amount_equal(2 ** 53 + 1, float(2 ** 53))
    assert not exact_amount_equal(1000.0000000000001, 1000.0)
    assert not exact_amount_equal(True, 1)
    assert not exact_amount_equal("50", 50)
    assert not exact_amount_equal(Decimal("50"), 50)


# --- revocation closeout: an attempt id names one retained item ----------------------------------------------------

def _item(ledger, effect, attempt):
    ledger.record_item(
        effect_id=effect, attempt_id=attempt, decision_id=f"d-{effect}", grant_id="g1", grant_revision="r1",
        state="executing", ordering=OrderingEvidence(1, f"q:{effect}"),
    )


def test_duplicate_attempt_id_is_refused_when_recording_an_item(tmp_path):
    ledger = RevocationCloseoutStore(tmp_path, clock=lambda: NOW)
    _item(ledger, "e1", "attempt-1")
    with pytest.raises(ValueError, match="already retained"):
        _item(ledger, "e2", "attempt-1")
    _item(ledger, "e3", None)
    _item(ledger, "e4", None)  # items without an attempt id are not constrained


def test_legacy_database_with_duplicate_attempt_ids_fails_closed_on_closeout(tmp_path):
    path = tmp_path / "revocation-closeout.sqlite3"
    RevocationCloseoutStore(tmp_path, clock=lambda: NOW)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP INDEX runtime_items_v1_attempt_id_unique")
        for effect in ("e1", "e2"):
            conn.execute(
                "INSERT INTO runtime_items_v1(effect_id,attempt_id,decision_id,grant_id,grant_revision,state,"
                "state_order,ordering_ref,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (effect, "attempt-dup", f"d-{effect}", "g1", "r1", "executing", 1, "q", "2026-10-09T18:00:00+00:00"),
            )
    reopened = RevocationCloseoutStore(tmp_path, clock=lambda: NOW)  # index cannot be created; must not raise
    with pytest.raises(PermissionError, match="ambiguous"):
        reopened.close_original_attempt(
            attempt_id="attempt-dup", observation_state="applied",
            observation=OrderingEvidence(5, "obs"), observation_ref="ref",
        )


def test_unique_attempt_id_closeout_still_works(tmp_path):
    ledger = RevocationCloseoutStore(tmp_path, clock=lambda: NOW)
    _item(ledger, "e1", "attempt-1")
    result = ledger.close_original_attempt(
        attempt_id="attempt-1", observation_state="applied",
        observation=OrderingEvidence(5, "obs"), observation_ref="ref",
    )
    assert result.closed is True and result.effect_id == "e1"
