"""Focused worker/adapter contract checks for producer profile 2.0.0."""
from dataclasses import asdict
import copy

import pytest

from engine.openshell_environment import OpenShellRefundDestination
from engine.openshell_worker import handle
from engine.safe_executor import snapshot_envelope
from test_safe_executor import envelope, make_operation


def _request(snapshot, mode):
    return {"version": "0.1.0", "mode": mode, "snapshot": asdict(snapshot)}


def test_real_worker_response_matches_repaired_observation_contract(tmp_path):
    snapshot = snapshot_envelope(envelope())
    ack = handle(_request(snapshot, "commit"), tmp_path)
    assert ack["duplicate"] is False
    observation = ack["observation"]

    # Worker bytes must carry both outer and embedded state; the accepted
    # OpenShell adapter validates every exact operation binding.
    assert observation["state"] == "applied"
    assert observation["destination_state"]["state"] == "applied"
    OpenShellRefundDestination._validate_observation(snapshot, observation)

    observed = handle(_request(snapshot, "observe"), tmp_path)
    OpenShellRefundDestination._validate_observation(snapshot, observed)
    assert observed == observation


def test_real_worker_duplicate_and_conflicting_effect_identity(tmp_path):
    snapshot = snapshot_envelope(envelope())
    assert handle(_request(snapshot, "commit"), tmp_path)["duplicate"] is False
    assert handle(_request(snapshot, "commit"), tmp_path)["duplicate"] is True

    conflicting = snapshot_envelope(
        envelope(make_operation(target="urn:cognous:synthetic-account:conflict"))
    )
    with pytest.raises(PermissionError, match="different operation content"):
        handle(_request(conflicting, "commit"), tmp_path)

    observed = handle(_request(snapshot, "observe"), tmp_path)
    assert observed["destination_state"]["target"] == snapshot.operation.target
    OpenShellRefundDestination._validate_observation(snapshot, observed)


def test_real_worker_partial_observation_is_not_promoted(tmp_path):
    snapshot = snapshot_envelope(envelope())
    from engine.safe_executor import DurableRefundDestination

    DurableRefundDestination(tmp_path).commit(snapshot, simulate="partial")
    observed = handle(_request(snapshot, "observe"), tmp_path)
    assert observed["state"] == "partial"
    assert observed["destination_state"]["state"] == "partial"
    OpenShellRefundDestination._validate_observation(snapshot, observed)


@pytest.mark.parametrize("mutation", ["missing_state", "contradictory_target", "wrong_amount_type"])
def test_actual_worker_bytes_fail_closed_after_observation_mutation(tmp_path, mutation):
    snapshot = snapshot_envelope(envelope())
    actual = handle(_request(snapshot, "commit"), tmp_path)["observation"]
    altered = copy.deepcopy(actual)

    if mutation == "missing_state":
        del altered["destination_state"]["state"]
    elif mutation == "contradictory_target":
        altered["destination_state"]["target"] = "urn:cognous:synthetic-account:attacker"
        assert altered["destination_state"]["operation_digest"] == actual["destination_state"]["operation_digest"]
    else:
        altered["destination_state"]["amount"] = True

    with pytest.raises(PermissionError):
        OpenShellRefundDestination._validate_observation(snapshot, altered)
