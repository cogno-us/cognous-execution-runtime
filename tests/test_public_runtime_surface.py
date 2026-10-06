from __future__ import annotations

import importlib.abc
import json
import subprocess
import sys
from pathlib import Path

import pytest

from engine.control_plane_adapter import PinnedControlPlaneExecutor
from engine.producer_contract import (
    EXECUTOR_PRODUCER_PROFILE_ID,
    EXECUTOR_PRODUCER_PROFILE_VERSION,
    DurableRefundDestination,
    ExecutionEnvelope,
    ExecutionOperation,
    LocalExecutionPolicy,
    export_execution_artifacts,
)
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION,
    LocalDestinationExecutor,
    commitment,
    snapshot_envelope,
)


def _operation() -> ExecutionOperation:
    payload = {"refund_reason": "public-runtime-surface"}
    return ExecutionOperation(
        actor="urn:cognous:identity:agent-1",
        principal="urn:cognous:principal:service",
        institution_id="urn:cognous:institution:demo",
        authority_domain="customer-refunds",
        manifest_id="refund-integration-v1-1",
        manifest_version="1.1",
        manifest_digest="sha256:" + "a" * 64,
        proposal_commitment="sha256:" + "b" * 64,
        action_id="refund.issue",
        adapter_id="urn:cognous:adapter:synthetic-refund",
        target="urn:cognous:synthetic-account:customer-1",
        payload=payload,
        payload_commitment=commitment(payload),
        requested_permissions=("refund.issue",),
        amount=50.0,
        unit="USD",
        effects=1,
        authority_context_id="urn:cognous:authority-context:refund-demo",
        requirement_id="urn:cognous:requirement:refund-t1",
        grant_id="urn:cognous:grant:refund-1",
        grant_revision="1",
        effective_max_effects=1,
    )


def _policy(op: ExecutionOperation) -> LocalExecutionPolicy:
    return LocalExecutionPolicy(
        allowed_institutions=frozenset({op.institution_id}),
        allowed_authority_domains=frozenset({op.authority_domain}),
        allowed_adapters=frozenset({op.adapter_id}),
        allowed_actions=frozenset({op.action_id}),
        allowed_target_prefixes=("urn:cognous:synthetic-account:",),
        allowed_units=frozenset({"USD"}),
        max_amount=1000.0,
        max_effects=1,
    )


def test_public_runtime_imports_without_test_modules():
    repo_root = Path(__file__).resolve().parents[1]
    code = r"""
import importlib.abc
import sys

class BlockTests(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "tests" or fullname.startswith("tests."):
            raise ImportError("test modules are unavailable")
        return None

sys.meta_path.insert(0, BlockTests())
sys.path.insert(0, REPO_ROOT)
import engine.control_plane_adapter
import engine.producer_contract

assert "tests.test_safe_executor" not in sys.modules
assert all(not name.startswith("tests.") for name in sys.modules)
assert engine.producer_contract.EXECUTOR_PRODUCER_PROFILE_VERSION == "1.0.0"
""".replace("REPO_ROOT", repr(str(repo_root)))
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root.parent,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_executor_requires_explicit_caller_supplied_authority_workflow(tmp_path):
    op = _operation()
    destination = DurableRefundDestination(tmp_path / "state")
    with pytest.raises(ValueError, match="caller-supplied trusted Control Plane workflow"):
        PinnedControlPlaneExecutor(
            workflow=None,
            destination=destination,
            policy=_policy(op),
        )


def test_executor_requires_explicit_policy_and_destination():
    class Resolver:
        pass

    class Workflow:
        resolver = Resolver()

    with pytest.raises(ValueError, match="destination"):
        PinnedControlPlaneExecutor(
            workflow=Workflow(),
            destination=None,
            policy=object(),
        )
    with pytest.raises(ValueError, match="execution policy"):
        PinnedControlPlaneExecutor(
            workflow=Workflow(),
            destination=object(),
            policy=None,
        )


def test_producer_profile_identifies_version_and_preserves_source_provenance(tmp_path):
    op = _operation()
    envelope = ExecutionEnvelope(
        EXECUTION_ENVELOPE_VERSION,
        "decision-public-surface",
        "effect-public-surface",
        op,
    )
    destination = DurableRefundDestination(tmp_path / "state")
    result = LocalDestinationExecutor(
        destination, _policy(op)
    ).execute_snapshot(snapshot_envelope(envelope))
    exported = export_execution_artifacts(
        envelope,
        result,
        destination,
        repository_revision="revision-under-test",
        source_asserted_provenance={"source": "focused-runtime-test"},
    )

    assert exported["producer_profile"]["profile_id"] == EXECUTOR_PRODUCER_PROFILE_ID
    assert (
        exported["producer_profile"]["profile_version"]
        == EXECUTOR_PRODUCER_PROFILE_VERSION
    )
    assert exported["producer_profile"]["execution_envelope_version"] == "0.2.0"
    assert exported["repository"] == {
        "repository": "cogno-us/moltbot-safe",
        "revision": "revision-under-test",
        "revision_status": "source_asserted",
    }
    assert (
        exported["provenance"]["source_asserted"]["repository_revision"]
        == "revision-under-test"
    )
    assert exported["provenance"]["independently_established"] == []
    assert "resolver" not in exported
    assert "authority_context" not in exported
    assert "policy" not in exported


def test_existing_binding_and_recovery_semantics_remain_intact(tmp_path):
    op = _operation()
    state = tmp_path / "state"
    destination = DurableRefundDestination(state)
    envelope = ExecutionEnvelope(
        EXECUTION_ENVELOPE_VERSION,
        "decision-recovery",
        "effect-recovery",
        op,
        "attempt-recovery",
    )
    executor = LocalDestinationExecutor(destination, _policy(op))
    first = executor.execute_snapshot(
        snapshot_envelope(envelope),
        simulate="crash_after_commit",
    )
    assert first.status == "unknown"
    assert first.newly_executed is True
    assert destination.effect_count(op.grant_id) == 1

    restarted = DurableRefundDestination(state)
    observed = LocalDestinationExecutor(
        restarted, _policy(op)
    ).observe_historical(envelope)
    assert observed.status == "observed"
    assert observed.newly_executed is False
    assert observed.effect_id == "effect-recovery"
    assert restarted.effect_count(op.grant_id) == 1

    exported = export_execution_artifacts(
        envelope,
        observed,
        restarted,
        repository_revision="revision-under-test",
    )
    assert len(exported["effects"]) == 1
    assert exported["effects"][0]["effect_id"] == "effect-recovery"
    assert exported["attempts"]
    assert exported["attempt_events"]
    assert exported["observations"][0]["effect_id"] == "effect-recovery"
