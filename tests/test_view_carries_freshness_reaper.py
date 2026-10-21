"""PinnedControlPlaneExecutor: the per-call workflow view carries the host freshness reaper (scan 6, finding CP-6).

The reaper is handed out by the host's workflow (`workflow.reaper()`), not passed to its constructor. A per-call view built
without it never checks it, so a reaper that died no longer failed closed on this path and effects dispatched. The stub
follows the real workflow: a view starts with `_reaper = None` and `execute` checks it before any dispatch.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from engine.control_plane_adapter import PinnedControlPlaneExecutor
from engine.safe_executor import EXECUTION_ENVELOPE_VERSION, ExecutionEnvelope, DurableRefundDestination, snapshot_envelope
from test_failed_result_observed_state import control_plane_stub  # noqa: F401  (fixture)
from test_producer_contract import operation, policy
from test_route_a_tenant_binding import _Proposal, _Resolver


class ReaperFailed(RuntimeError):
    pass


class _Reaper:
    def __init__(self, failed=False):
        self.failed, self.checks = failed, 0

    def check(self):
        self.checks += 1
        if self.failed:
            raise ReaperFailed("freshness reaper stopped: thread ended")


class _Workflow:
    resolver = _Resolver()
    manifest = {}
    observation_policy = None
    status_max_age_seconds = identity_max_age_seconds = mandate_max_age_seconds = approval_max_age_seconds = 300
    clock_tolerance_seconds = 5
    runtime_authority_profile = None
    rule_revision_profile = None
    allow_test_time = False
    dispatched = 0

    def __init__(self, *, destination=None, records=None, **_):
        self.destination, self.records = destination, records
        self._reaper = None

    def execute(self, **_):
        if self._reaper is not None:
            self._reaper.check()
        type(self).dispatched += 1  # the effect would be dispatched here
        raise AssertionError("dispatch reached")


def _executor(tmp_path, reaper):
    op = operation()
    env = ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)
    frozen = snapshot_envelope(env)
    workflow = _Workflow()
    workflow.records = SimpleNamespace(reconciliations=[], refusals=[])
    workflow._reaper = reaper
    _Workflow.dispatched = 0
    executor = PinnedControlPlaneExecutor(workflow=workflow, destination=DurableRefundDestination(tmp_path / "s"),
                                          policy=policy(op), observation_clock=lambda: None)
    decision = SimpleNamespace(decision_id=frozen.decision_id, effect_id=frozen.effect_id,
                               binding=SimpleNamespace(grant_id=op.grant_id, grant_revision=op.grant_revision,
                                                       effective_max_effects=op.effective_max_effects))
    return executor, env, decision, _Proposal(op)


def test_dead_reaper_fails_closed_on_the_pinned_executor_path_before_dispatch(tmp_path, control_plane_stub):
    reaper = _Reaper(failed=True)
    executor, env, decision, proposal = _executor(tmp_path, reaper)
    with pytest.raises(ReaperFailed):
        executor.execute(envelope=env, proposal=proposal, decision=decision)
    assert reaper.checks == 1 and _Workflow.dispatched == 0


def test_live_reaper_is_checked_and_does_not_block(tmp_path, control_plane_stub):
    reaper = _Reaper(failed=False)
    executor, env, decision, proposal = _executor(tmp_path, reaper)
    with pytest.raises(AssertionError, match="dispatch reached"):
        executor.execute(envelope=env, proposal=proposal, decision=decision)
    assert reaper.checks == 1 and _Workflow.dispatched == 1


def test_no_reaper_configured_leaves_the_view_unchanged(tmp_path, control_plane_stub):
    executor, env, decision, proposal = _executor(tmp_path, None)
    with pytest.raises(AssertionError, match="dispatch reached"):
        executor.execute(envelope=env, proposal=proposal, decision=decision)
    assert _Workflow.dispatched == 1
