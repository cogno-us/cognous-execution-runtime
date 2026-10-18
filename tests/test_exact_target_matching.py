"""Exact target matching for the local execution policy (component-local, synthetic; no network, no real provider).

Provenance: every test named test_default_* was RUN on main 7170da59 before this change (they pass there and here), so they
pin the valid-input behaviour of the default path. Tests naming target_match, and the character/segment refusals, describe new
behaviour and have no expected-today value.
"""
import dataclasses
import inspect
from types import SimpleNamespace

import pytest

from engine.atomic_local_control_plane_executor import AtomicLocalControlPlaneExecutor
from engine.dedupe_destination import DedupeTtlOperationDestination
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION, DurableRefundDestination, ExecutionEnvelope, LocalDestinationExecutor, LocalExecutionPolicy,
    snapshot_envelope)
from test_producer_contract import operation, policy

BASE = "urn:cognous:synthetic-account:"
VALID = [BASE + "customer-1", BASE + "customer-1/sub", BASE + "a.b-c_d"]


def _snap(op, effect="effect-1"):
    return snapshot_envelope(ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-" + effect, effect, op))


def _run(tmp_path, pol, target, **kw):
    op = dataclasses.replace(operation(), target=target)
    try:
        return LocalDestinationExecutor(DurableRefundDestination(tmp_path / "s"), pol).execute_snapshot(_snap(op), **kw)
    except PermissionError as exc:
        return SimpleNamespace(status="refused", attempted=False, error=str(exc))


def _with(pol, **changes):
    return dataclasses.replace(pol, **changes)


@pytest.mark.parametrize("target", VALID)
def test_default_valid_targets_execute_with_the_same_result_shape(tmp_path, target):
    result = _run(tmp_path, policy(operation()), target)
    assert result.status == "executed" and result.attempted is True
    assert result.observation["state"] == "applied"


def test_default_policy_fields_and_default_mode_are_unchanged():
    names = [f.name for f in dataclasses.fields(LocalExecutionPolicy)]
    assert names[:8] == ["allowed_institutions", "allowed_authority_domains", "allowed_adapters", "allowed_actions",
                         "allowed_target_prefixes", "allowed_units", "max_amount", "max_effects"]


def test_default_mode_is_the_historical_prefix_mode():
    assert policy(operation()).target_match == "prefix"


def test_default_unlisted_target_is_still_refused(tmp_path):
    result = _run(tmp_path, policy(operation()), "urn:other:prefix:customer-1")
    assert result.status == "refused" and result.error == "local target restriction"


def test_default_operation_key_path_unchanged_for_valid_targets(tmp_path):
    op = dataclasses.replace(operation(), effective_max_effects=2)
    dest = DedupeTtlOperationDestination(tmp_path / "d", dedupe_ttl_seconds=600, clock=lambda: 1000.0)
    executor = LocalDestinationExecutor(dest, dataclasses.replace(policy(op), max_effects=2))
    first = executor.execute_snapshot(_snap(op, "effect-a"), operation_id="op-1/refund-1")
    again = executor.execute_snapshot(_snap(op, "effect-a"), operation_id="op-1/refund-1")
    assert first.status == "executed" and dest.effect_count(op.grant_id) == 1
    assert again.status == "reconciled" and dest.effect_count(op.grant_id) == 1


def test_default_atomic_route_policy_check_unchanged_for_valid_targets(tmp_path):
    class Dest(DurableRefundDestination):
        calls = 0

        def execute_claim_atomic(self, claim_id, snapshot, *, attempt_id, simulate=None):
            Dest.calls += 1
            return {"observation": {"state": "applied"}, "linearized_at": "t", "authority_receipt": {}}

    executor = AtomicLocalControlPlaneExecutor(workflow=None, destination=Dest(tmp_path / "s"), policy=policy(operation()))
    env = ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", operation())
    assert executor.execute(envelope=env, claim_id="claim-1").status == "executed" and Dest.calls == 1
    assert inspect.signature(AtomicLocalControlPlaneExecutor.__init__).parameters["operation_identity"].default is False


BAD = [
    BASE + "customer-1\x00x", BASE + "customer-1\x00", BASE + "customer-1\n", BASE + "customer-1\x1b[0m", BASE + "customer-1\x7f",
    BASE + "customer-1​", BASE + "‍customer-1", BASE + "customer-1﻿", BASE + "customer-1 ",
    BASE + "customer-1/../customer-2", BASE + "customer-1/./x", BASE + "..", BASE + "customer-1/..", BASE + "a\\..\\b",
]


@pytest.mark.parametrize("mode", ["prefix", "segment", "exact"])
@pytest.mark.parametrize("target", BAD)
def test_malformed_targets_are_refused_in_every_mode(tmp_path, mode, target):
    pol = _with(policy(operation()), target_match=mode)
    if mode == "exact":
        pol = _with(pol, allowed_target_prefixes=(target,))  # even listed verbatim, it is refused
    result = _run(tmp_path, pol, target)
    assert result.status == "refused" and result.error == "local target restriction"


@pytest.mark.parametrize("target,ok", [
    (BASE + "customer-1", True), (BASE + "customer-1/sub", True), (BASE + "customer-1:sub", True),
    (BASE + "customer-10", False), (BASE + "customer-1x", False), (BASE + "customer-2", False)])
def test_segment_mode_requires_a_boundary(tmp_path, target, ok):
    pol = _with(policy(operation()), allowed_target_prefixes=(BASE + "customer-1",), target_match="segment")
    assert (_run(tmp_path, pol, target).status == "executed") is ok


def test_segment_mode_accepts_a_prefix_that_already_ends_at_a_separator(tmp_path):
    pol = _with(policy(operation()), target_match="segment")
    assert _run(tmp_path, pol, BASE + "customer-10").status == "executed"


@pytest.mark.parametrize("target,ok", [
    (BASE + "customer-1", True), (BASE + "customer-10", False), (BASE + "customer-1/sub", False), (BASE, False)])
def test_exact_mode_matches_only_listed_targets(tmp_path, target, ok):
    pol = _with(policy(operation()), allowed_target_prefixes=(BASE + "customer-1",), target_match="exact")
    assert (_run(tmp_path, pol, target).status == "executed") is ok


def test_legacy_prefix_mode_still_matches_by_prefix(tmp_path):
    pol = _with(policy(operation()), allowed_target_prefixes=(BASE + "customer-1",))
    assert _run(tmp_path, pol, BASE + "customer-10").status == "executed"


@pytest.mark.parametrize("value", ["", "Exact", None, ["exact"], "startswith"])
def test_unknown_target_match_is_rejected_at_construction(value):
    with pytest.raises(ValueError, match="target_match"):
        _with(policy(operation()), target_match=value)


def test_exact_mode_keeps_the_operation_key_path(tmp_path):
    op = dataclasses.replace(operation(), effective_max_effects=2)
    dest = DedupeTtlOperationDestination(tmp_path / "d", dedupe_ttl_seconds=600, clock=lambda: 1000.0)
    pol = dataclasses.replace(policy(op), max_effects=2, allowed_target_prefixes=(op.target,), target_match="exact")
    executor = LocalDestinationExecutor(dest, pol)
    assert executor.execute_snapshot(_snap(op, "effect-a"), operation_id="op-1/refund-1").status == "executed"
    assert dest.effect_count(op.grant_id) == 1
