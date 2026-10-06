from __future__ import annotations

import copy

import pytest

from engine.producer_profile import (
    EXECUTOR_PRODUCER_PROFILE_VERSION,
    ProducerContractError,
    export_execution_producer_record,
    validate_execution_producer_record,
)
from engine.safe_executor import DurableRefundDestination, LocalDestinationExecutor

from test_safe_executor import envelope, make_operation, policy


def test_exported_producer_record_is_versioned_and_bound(tmp_path):
    op = make_operation()
    request = envelope(op)
    destination = DurableRefundDestination(tmp_path / "state")
    result = LocalDestinationExecutor(destination, policy(op)).execute(request)
    record = export_execution_producer_record(
        envelope=request,
        result=result,
        destination=destination,
        repository_revision="proposed-test-revision",
    )
    assert record["producer_profile_version"] == EXECUTOR_PRODUCER_PROFILE_VERSION
    assert record["repository_revision"] == "proposed-test-revision"
    assert record["provenance"]["source_asserted"] is True
    assert record["provenance"]["independently_established"] is False
    assert len(record["effects"]) == 1
    assert record["execution_result"]["effect_id"] == request.effect_id
    validate_execution_producer_record(record)


@pytest.mark.parametrize(
    "field,value",
    [
        ("producer_profile_version", "9.9.9"),
        ("repository_revision", ""),
    ],
)
def test_unsupported_profile_or_missing_revision_rejected(tmp_path, field, value):
    op = make_operation()
    request = envelope(op)
    destination = DurableRefundDestination(tmp_path / "state")
    result = LocalDestinationExecutor(destination, policy(op)).execute(request)
    record = export_execution_producer_record(
        envelope=request,
        result=result,
        destination=destination,
        repository_revision="proposed-test-revision",
    )
    changed = copy.deepcopy(record)
    changed[field] = value
    with pytest.raises(ProducerContractError):
        validate_execution_producer_record(changed)


def test_contradictory_attempt_binding_rejected(tmp_path):
    op = make_operation()
    request = envelope(op)
    destination = DurableRefundDestination(tmp_path / "state")
    result = LocalDestinationExecutor(destination, policy(op)).execute(request)
    record = export_execution_producer_record(
        envelope=request,
        result=result,
        destination=destination,
        repository_revision="proposed-test-revision",
    )
    changed = copy.deepcopy(record)
    changed["attempts"][0]["decision_id"] = "different"
    with pytest.raises(ProducerContractError):
        validate_execution_producer_record(changed)
