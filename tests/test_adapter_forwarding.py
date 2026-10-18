"""F1 (read-only code review R1, findings 1 and test gaps): the control plane adapter must forward what the opt-in profiles need.

Synthetic, component-local; no control plane package is required (a stub of its rejection type is injected where needed).
"""
import sys
import types
from types import SimpleNamespace

import pytest

from engine.control_plane_adapter import ControlPlaneRefundDestinationAdapter, workflow_view_profile_kwargs
from engine.operation_identity_destination import OperationIdentityDestination
from engine.safe_executor import LocalExecutionPolicy
from test_n1_operation_identity import OP, snap


def _adapter(snapshot, dest):
    op = snapshot.operation
    policy = LocalExecutionPolicy(
        allowed_institutions=frozenset({op.institution_id}), allowed_authority_domains=frozenset({op.authority_domain}),
        allowed_adapters=frozenset({op.adapter_id}), allowed_actions=frozenset({op.action_id}),
        allowed_target_prefixes=(op.target,), allowed_units=frozenset({op.unit}), max_amount=10_000, max_effects=5)
    return ControlPlaneRefundDestinationAdapter(
        snapshot=snapshot, destination=dest, policy=policy, trusted_institution_id=op.institution_id,
        trusted_authority_domain=op.authority_domain, observation_clock=lambda: None)


def _apply(adapter, **extra):
    op = adapter.snapshot.operation
    return adapter.apply(effect_id=adapter.snapshot.effect_id, grant_id=op.grant_id, max_effects=op.effective_max_effects,
                         target=op.target, amount=op.amount, unit=op.unit, payload=op.payload(), **extra)


def test_adapter_forwards_operation_id_so_a_second_effect_for_one_operation_is_refused(tmp_path, monkeypatch):
    """Deleting the operation_id forwarding in ControlPlaneRefundDestinationAdapter.apply makes the second apply succeed."""
    stub = types.ModuleType("agent_control_plane.bounded")

    class DefinitiveDestinationRejection(Exception):
        pass
    stub.DefinitiveDestinationRejection = DefinitiveDestinationRejection
    monkeypatch.setitem(sys.modules, "agent_control_plane", types.ModuleType("agent_control_plane"))
    monkeypatch.setitem(sys.modules, "agent_control_plane.bounded", stub)
    dest = OperationIdentityDestination(tmp_path / "state")
    _, a = snap("effect-a")
    _, b = snap("effect-b")
    first = _apply(_adapter(a, dest), operation_id=OP)
    assert first["moltbot_safe_status"] == "executed"
    with pytest.raises(DefinitiveDestinationRejection, match="operation_already_committed"):
        _apply(_adapter(b, dest), operation_id=OP)
    assert dest.observe(b.effect_id)["state"] == "absent"


def test_adapter_without_an_operation_id_is_refused_by_the_grant_cap_not_by_operation_identity(tmp_path):
    dest = OperationIdentityDestination(tmp_path / "state")
    _, a = snap("effect-a")
    _, b = snap("effect-b")
    assert _apply(_adapter(a, dest))["moltbot_safe_status"] == "executed"
    with pytest.raises(RuntimeError, match="max_effects exhausted"):  # no identity requested: the legacy cap decides
        _apply(_adapter(b, dest))


def test_workflow_view_forwards_the_rule_revision_profile_whenever_set():
    """R1 finding 1: it used to be forwarded only with freshness/0.1, so digest pinning was off at effect time otherwise."""
    rule_profile = object()
    for ra in (None, SimpleNamespace(freshness=None), SimpleNamespace(freshness=object())):
        wf = SimpleNamespace(runtime_authority_profile=ra, rule_revision_profile=rule_profile, allow_test_time=False)
        assert workflow_view_profile_kwargs(wf)["rule_revision_profile"] is rule_profile, ra


def test_workflow_view_default_path_is_unchanged_when_nothing_is_configured():
    wf = SimpleNamespace(runtime_authority_profile=None, rule_revision_profile=None, allow_test_time=False)
    assert workflow_view_profile_kwargs(wf) == {}
    ra = SimpleNamespace(freshness=None)
    wf = SimpleNamespace(runtime_authority_profile=ra, rule_revision_profile=None, allow_test_time=True)
    assert workflow_view_profile_kwargs(wf) == {"runtime_authority_profile": ra, "allow_test_time": True}
