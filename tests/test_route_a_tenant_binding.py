"""Route A (PinnedControlPlaneExecutor) must bind the request tenant to the proposal tenant (issue #95 / F-04).

Component-local, synthetic: a stub workflow is used because the refusal happens before any Control Plane call.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from engine.control_plane_adapter import PinnedControlPlaneExecutor
from engine.safe_executor import (
    EXECUTION_ENVELOPE_VERSION,
    DurableRefundDestination,
    ExecutionEnvelope,
)
from test_producer_contract import operation, policy


class _Resolver:
    def authority_context(self, ref):
        op = operation()
        return {"institution": {"institution_id": op.institution_id, "authority_domain": op.authority_domain}}


class _Workflow:
    resolver = _Resolver()


class _Proposal:
    """Minimal stand-in for RuntimeProposal / TenantRuntimeProposal (tenant_id only when tenant-scoped)."""

    def __init__(self, op, **extra):
        self.actor, self.principal = op.actor, op.principal
        self.manifest_id, self.manifest_version, self.manifest_digest = op.manifest_id, op.manifest_version, op.manifest_digest
        self.action_id, self.adapter_id, self.target = op.action_id, op.adapter_id, op.target
        self.payload, self.payload_commitment = op.payload, op.payload_commitment
        self.requested_permissions = list(op.requested_permissions)
        self.amount, self.unit, self.effects = op.amount, op.unit, op.effects
        self.authority_context_ref, self.requirement_id = op.authority_context_id, op.requirement_id
        self.__dict__.update(extra)

    def model_dump(self, **_):
        return dict(self.__dict__)


def _run(tmp_path, *, envelope_tenant, proposal_tenant, **proposal_overrides):
    op = replace(operation(), tenant_id=envelope_tenant)
    dest = DurableRefundDestination(tmp_path / "state")
    executor = PinnedControlPlaneExecutor(workflow=_Workflow(), destination=dest, policy=policy(op))
    extra = dict(proposal_overrides)
    if proposal_tenant is not None:
        extra["tenant_id"] = proposal_tenant
    proposal = _Proposal(op, **extra)
    envelope = ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)
    decision = SimpleNamespace(decision_id="decision-1", effect_id="effect-1", binding=None)
    result = executor.execute(envelope=envelope, proposal=proposal, decision=decision)
    return op, dest, result


@pytest.mark.parametrize(
    "envelope_tenant,proposal_tenant",
    [("tenant-beta", "tenant-alpha"), (None, "tenant-alpha"), ("tenant-beta", None)],
    ids=["substituted", "removed", "added"],
)
def test_tenant_substituted_removed_or_added_request_is_refused_before_dispatch(tmp_path, envelope_tenant, proposal_tenant):
    op, dest, result = _run(tmp_path, envelope_tenant=envelope_tenant, proposal_tenant=proposal_tenant)
    assert result.status == "denied" and result.attempted is False
    assert "tenant_id" in result.error
    assert dest.effect_count(op.grant_id) == 0


@pytest.mark.parametrize("tenant", ["tenant-alpha", None], ids=["tenant-scoped", "tenant-less"])
def test_matching_tenant_is_not_a_mismatch(tmp_path, tenant):
    # An unrelated mismatch (amount) is used to stop before the Control Plane; tenant must not be named.
    _, _, result = _run(tmp_path, envelope_tenant=tenant, proposal_tenant=tenant, amount=1.0)
    assert result.status == "denied"
    assert "amount" in result.error and "tenant_id" not in result.error
