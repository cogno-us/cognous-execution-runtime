"""IntentRefundDestination.observe_bound is coupled to the original-claim validation.

An observation through the per-request intent destination is only served for the registered intent and, once the intent
is reserved, only for the original effect and operation digest. Another effect (or other content) for the same intent is
refused instead of being told "absent". The owner's own observation, before and after its commit, is unchanged.
"""
from __future__ import annotations

import dataclasses

import pytest

from engine.refund_intent import IntentRefundDestination, RefundIntent, RefundIntentRegistry
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION, DurableRefundDestination, ExecutionEnvelope, commitment, snapshot_envelope)
from test_producer_contract import operation


def _snapshot(effect_id="effect-1", *, request_amount=50.0):
    payload = {"customer_id": "customer-1", "refund_reason": "observation-test"}
    op = dataclasses.replace(operation(), payload=payload, payload_commitment=commitment(payload), amount=request_amount)
    return snapshot_envelope(ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", effect_id, op))


def _intent():
    return RefundIntent("urn:cognous:institution:demo", "customer-refunds", "customer-1", "request-1")


@pytest.fixture
def parts(tmp_path):
    base = DurableRefundDestination(tmp_path / "state")
    registry = RefundIntentRegistry(base)
    owner = _snapshot()
    registry.provision(_intent(), owner)
    return IntentRefundDestination(base, _intent()), registry, owner


def test_owner_observation_is_unchanged_before_and_after_commit(parts):
    dest, registry, owner = parts
    assert dest.observe_bound(owner)["state"] == "absent"  # registered, not yet reserved
    registry.reserve(_intent(), owner)
    assert dest.observe_bound(owner)["state"] == "absent"  # reserved by this owner
    dest.commit(owner)
    assert dest.observe_bound(owner)["state"] == "applied"


def test_observation_for_another_effect_on_a_reserved_intent_is_refused(parts):
    dest, registry, owner = parts
    registry.reserve(_intent(), owner)
    with pytest.raises(PermissionError, match="original effect"):
        dest.observe_bound(_snapshot("effect-2"))


def test_observation_with_other_content_on_a_reserved_intent_is_refused(parts):
    dest, registry, owner = parts
    registry.reserve(_intent(), owner)
    with pytest.raises(PermissionError):
        dest.observe_bound(_snapshot(request_amount=9.0))


def test_observation_for_an_unregistered_intent_is_refused(tmp_path):
    base = DurableRefundDestination(tmp_path / "state")
    RefundIntentRegistry(base)
    dest = IntentRefundDestination(base, _intent())
    with pytest.raises(PermissionError, match="unregistered intent"):
        dest.observe_bound(_snapshot())
