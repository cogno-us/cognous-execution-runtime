"""Route A keeps the local-policy refusal reason that the Control Plane would otherwise collapse (issue #97 / F-09)."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from engine.control_plane_adapter import ControlPlaneRefundDestinationAdapter
from engine.safe_executor import EXECUTION_ENVELOPE_VERSION, DurableRefundDestination, ExecutionEnvelope, snapshot_envelope
from test_producer_contract import operation, policy


def _adapter(tmp_path, local_policy):
    op = operation()
    snap = snapshot_envelope(ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op))
    return ControlPlaneRefundDestinationAdapter(
        snapshot=snap, destination=DurableRefundDestination(tmp_path / "state"), policy=local_policy,
        trusted_institution_id=op.institution_id, trusted_authority_domain=op.authority_domain,
        observation_clock=lambda: datetime.now(timezone.utc))


@pytest.mark.parametrize("change,reason", [
    (dict(allowed_units=frozenset({"EUR"})), "local unit restriction"),
    (dict(max_amount=1), "local amount restriction"),
    (dict(allowed_actions=frozenset({"other-action"})), "local action restriction"),
])
def test_observe_refusal_for_stricter_local_policy_records_its_cause(tmp_path, change, reason):
    adapter = _adapter(tmp_path, replace(policy(operation()), **change))
    with pytest.raises(PermissionError, match=reason):
        adapter.observe("effect-1")
    assert adapter.local_refusal == reason


def test_no_local_refusal_recorded_before_any_refusal(tmp_path):
    assert _adapter(tmp_path, policy(operation())).local_refusal is None
