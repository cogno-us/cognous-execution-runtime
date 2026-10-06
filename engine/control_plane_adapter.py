from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from .safe_executor import (
    DurableRefundDestination,
    ExecutionEnvelope,
    ExecutionResult,
    FrozenEnvelope,
    LocalDestinationExecutor,
    LocalExecutionPolicy,
    snapshot_envelope,
)


class _ReconciliationCapture:
    """Per-call capture while forwarding every write to the durable store."""

    def __init__(self, records):
        self.records = records
        self.reconciliations = []

    def __getattr__(self, name):
        return getattr(self.records, name)

    def append_reconciliation(self, value):
        self.records.append_reconciliation(value)
        self.reconciliations.append(value)


@dataclass
class _AdapterOutcome:
    result: ExecutionResult | None = None


class ControlPlaneRefundDestinationAdapter:
    """Destination interface consumed by the pinned BoundedAuthorizationWorkflow.

    The adapter is bound to one pre-snapshotted Moltbot Safe request. The Control
    Plane revalidates current authority before invoking apply().
    """

    def __init__(
        self,
        *,
        snapshot: FrozenEnvelope,
        destination: DurableRefundDestination,
        policy: LocalExecutionPolicy,
        trusted_institution_id: str,
        trusted_authority_domain: str,
        observation_clock: Callable[[], datetime],
    ):
        self.observation_clock = observation_clock
        self.snapshot = snapshot
        self.destination = destination
        self.executor = LocalDestinationExecutor(destination, policy)
        self.trusted_institution_id = trusted_institution_id
        self.trusted_authority_domain = trusted_authority_domain
        self.outcome = _AdapterOutcome()

    def _check_trusted_context(self) -> None:
        op = self.snapshot.operation
        if op.institution_id != self.trusted_institution_id:
            raise PermissionError("institution binding mismatch at trusted integration boundary")
        if op.authority_domain != self.trusted_authority_domain:
            raise PermissionError("authority-domain binding mismatch at trusted integration boundary")

    def observe(self, effect_id: str):
        # BoundedAuthorizationWorkflow calls observe before deciding whether to
        # submit. Validate the stable effect identity and operation digest here.
        if effect_id != self.snapshot.effect_id:
            raise PermissionError("effect identifier mismatch")
        self._check_trusted_context()
        self.executor.policy.check(self.snapshot.operation)
        observed = self.destination.observe_bound(self.snapshot)

        # Construct the pinned Control Plane EffectObservation lazily so this
        # module can still be imported when that package is not installed.
        from agent_control_plane.bounded import EffectObservation, _iso

        state = observed["state"]
        return EffectObservation(
            effect_id=effect_id,
            observed_at=_iso(self.observation_clock()),
            state=state,
            destination_state=copy.deepcopy(observed["destination_state"]),
        )

    def apply(
        self,
        *,
        effect_id: str,
        grant_id: str,
        max_effects: int,
        target: str,
        amount: float,
        unit: str,
        payload: dict,
        lose_ack: bool = False,
        partial: bool = False,
    ) -> dict:
        self._check_trusted_context()
        op = self.snapshot.operation
        exact = {
            "effect_id": effect_id == self.snapshot.effect_id,
            "grant_id": grant_id == op.grant_id,
            "max_effects": max_effects == op.effective_max_effects,
            "target": target == op.target,
            "amount": amount == op.amount,
            "unit": unit == op.unit,
            "payload": payload == op.payload(),
        }
        mismatches = [name for name, ok in exact.items() if not ok]
        if mismatches:
            raise PermissionError(
                "control-plane destination binding mismatch: " + ",".join(mismatches)
            )

        simulate = "partial" if partial else ("lost_ack" if lose_ack else None)
        result = self.executor.execute_snapshot(self.snapshot, simulate=simulate)
        self.outcome.result = result
        if result.status == "denied":
            raise PermissionError(result.error or "local destination denied")
        if result.status == "failed":
            raise RuntimeError(result.error or "local destination failed")
        if result.status == "unknown":
            # Preserve Control Plane lost-ack semantics.
            raise TimeoutError(result.error or "acknowledgement unavailable")
        return {
            "moltbot_safe_status": result.status,
            "attempt_id": result.attempt_id,
            "newly_executed": result.newly_executed,
            "observation": copy.deepcopy(result.observation),
        }


class PinnedControlPlaneExecutor:
    """Supported integration: current Control Plane revalidation then local effect."""

    def __init__(
        self,
        *,
        workflow: Any,
        destination: DurableRefundDestination,
        policy: LocalExecutionPolicy,
        observation_clock: Callable[[], datetime] | None = None,
    ):
        if workflow is None or getattr(workflow, "resolver", None) is None:
            raise ValueError(
                "caller-supplied trusted Control Plane workflow with resolver is required"
            )
        if destination is None:
            raise ValueError("caller-supplied destination is required")
        if policy is None:
            raise ValueError("caller-supplied execution policy is required")
        self.observation_clock = observation_clock or (lambda: datetime.now(timezone.utc))
        self.workflow = workflow
        self.destination = destination
        self.policy = policy

    def execute(
        self,
        *,
        envelope: ExecutionEnvelope,
        proposal: Any,
        decision: Any,
        now: Any = None,
        simulate: str | None = None,
    ) -> ExecutionResult:
        snapshot = snapshot_envelope(envelope)

        # Derive institution/domain only from the resolver context used by the
        # trusted Control Plane workflow. Caller labels alone cannot satisfy it.
        context = self.workflow.resolver.authority_context(
            proposal.authority_context_ref or ""
        )
        if not isinstance(context, dict):
            return self._denied(snapshot, "trusted authority context unavailable")
        institution = context.get("institution") or {}
        trusted_institution = institution.get("institution_id")
        trusted_domain = institution.get("authority_domain")
        if not isinstance(trusted_institution, str) or not trusted_institution:
            return self._denied(snapshot, "trusted institution unavailable")
        if not isinstance(trusted_domain, str) or not trusted_domain:
            return self._denied(snapshot, "trusted authority domain unavailable")

        # Bind the request snapshot to the actual RuntimeProposal supplied to the
        # Control Plane, before execution-time revalidation occurs.
        proposal_dump = proposal.model_dump(mode="json", exclude_none=False)
        checks = {
            "actor": proposal.actor == snapshot.operation.actor,
            "principal": proposal.principal == snapshot.operation.principal,
            "manifest_id": proposal.manifest_id == snapshot.operation.manifest_id,
            "manifest_version": proposal.manifest_version == snapshot.operation.manifest_version,
            "manifest_digest": proposal.manifest_digest == snapshot.operation.manifest_digest,
            "action_id": proposal.action_id == snapshot.operation.action_id,
            "adapter_id": proposal.adapter_id == snapshot.operation.adapter_id,
            "target": proposal.target == snapshot.operation.target,
            "payload": proposal.payload == snapshot.operation.payload(),
            "payload_commitment": proposal.payload_commitment == snapshot.operation.payload_commitment,
            "requested_permissions": tuple(proposal.requested_permissions) == snapshot.operation.requested_permissions,
            "amount": proposal.amount == snapshot.operation.amount,
            "unit": proposal.unit == snapshot.operation.unit,
            "effects": proposal.effects == snapshot.operation.effects,
            "authority_context_id": proposal.authority_context_ref == snapshot.operation.authority_context_id,
            "requirement_id": proposal.requirement_id == snapshot.operation.requirement_id,
        }
        mismatches = [name for name, ok in checks.items() if not ok]
        if mismatches:
            return self._denied(
                snapshot, "runtime proposal mismatch: " + ",".join(mismatches)
            )

        from agent_control_plane.bounded import commitment as cp_commitment

        if cp_commitment(proposal_dump) != snapshot.operation.proposal_commitment:
            return self._denied(snapshot, "proposal commitment mismatch")

        if decision.decision_id != snapshot.decision_id:
            return self._denied(snapshot, "decision identifier mismatch")
        if decision.effect_id != snapshot.effect_id:
            return self._denied(snapshot, "effect identifier mismatch")
        if decision.binding is None:
            return self._denied(snapshot, "decision has no authorization binding")
        if decision.binding.grant_id != snapshot.operation.grant_id:
            return self._denied(snapshot, "grant binding mismatch")
        if decision.binding.grant_revision != snapshot.operation.grant_revision:
            return self._denied(snapshot, "grant revision mismatch")
        if decision.binding.effective_max_effects != snapshot.operation.effective_max_effects:
            return self._denied(snapshot, "effect limit mismatch")

        adapter = ControlPlaneRefundDestinationAdapter(
            snapshot=snapshot,
            destination=self.destination,
            policy=self.policy,
            trusted_institution_id=trusted_institution,
            trusted_authority_domain=trusted_domain,
            observation_clock=self.observation_clock,
        )
        # Build a fresh workflow view with the same trusted resolver, manifest,
        # durable Control Plane record store and revalidation settings. Do not
        # mutate the caller's workflow destination, which would create a
        # cross-request race.
        records = _ReconciliationCapture(self.workflow.records)
        workflow = self.workflow.__class__(
            manifest=self.workflow.manifest,
            resolver=self.workflow.resolver,
            destination=adapter,
            records=records,
            observation_policy=self.workflow.observation_policy,
            status_max_age_seconds=self.workflow.status_max_age_seconds,
            identity_max_age_seconds=self.workflow.identity_max_age_seconds,
            mandate_max_age_seconds=self.workflow.mandate_max_age_seconds,
            approval_max_age_seconds=self.workflow.approval_max_age_seconds,
            clock_tolerance_seconds=self.workflow.clock_tolerance_seconds,
        )
        kwargs = {
            "proposal": proposal,
            "decision": decision,
            "adapter_id": snapshot.operation.adapter_id,
        }
        if now is not None:
            kwargs["now"] = now
        if simulate == "lost_ack":
            kwargs["lose_ack"] = True
        elif simulate == "partial":
            kwargs["partial"] = True
        try:
            cp_attempt, cp_observation = workflow.execute(**kwargs)
        except PermissionError as exc:
            result = self._denied(snapshot, str(exc))
            if records.reconciliations:
                result.control_plane_evidence = {"reconciliation":
                    records.reconciliations[-1].model_dump(mode="json", exclude_none=False)}
            return result

        local = adapter.outcome.result
        # Retain the upstream attempt and validation independently of the local
        # acknowledgement. A committed effect is not a validated observation.
        reconciliation = records.reconciliations[-1]
        evidence = {
            "attempt": cp_attempt.model_dump(mode="json", exclude_none=False),
            "reconciliation": reconciliation.model_dump(mode="json", exclude_none=False),
        }
        state = cp_observation.state if cp_observation is not None else "unknown"
        status = (local.status if local is not None else
                  ("reconciled" if state == "applied" else
                   "partial" if state == "partial" else "unknown"))
        if cp_observation is None:
            status = "unknown"
        observation = None
        if cp_observation is not None:
            observation = cp_observation.model_dump(mode="json", exclude_none=False)
        return ExecutionResult(
            status=status,
            decision_id=snapshot.decision_id,
            effect_id=snapshot.effect_id,
            attempt_id=local.attempt_id if local is not None else cp_attempt.attempt_id,
            attempted=local.attempted if local is not None else True,
            acknowledged=local.acknowledged if local is not None else cp_attempt.status == "acknowledged",
            observed_state=state,
            newly_executed=local.newly_executed if local is not None else False,
            observation=observation,
            control_plane_evidence=evidence,
            error=cp_attempt.error or (";".join(reconciliation.reasons) or None),
        )

    def reconcile(self, envelope: ExecutionEnvelope, *, now: datetime):
        """Effect-free reconciliation; absence never grants dispatch permission."""
        snapshot = snapshot_envelope(envelope)
        adapter = ControlPlaneRefundDestinationAdapter(
            snapshot=snapshot, destination=self.destination, policy=self.policy,
            trusted_institution_id=snapshot.operation.institution_id,
            trusted_authority_domain=snapshot.operation.authority_domain,
            observation_clock=self.observation_clock,
        )
        workflow = copy.copy(self.workflow)
        workflow.destination = adapter
        return workflow.reconcile(snapshot.effect_id, now=now)

    def observe_historical(self, envelope: ExecutionEnvelope) -> ExecutionResult:
        """Observe bound durable state without renewing execution authority."""
        return LocalDestinationExecutor(
            self.destination, self.policy
        ).observe_historical(envelope)

    @staticmethod
    def _denied(snapshot: FrozenEnvelope, error: str) -> ExecutionResult:
        return ExecutionResult(
            status="denied",
            decision_id=snapshot.decision_id,
            effect_id=snapshot.effect_id,
            attempt_id=None,
            attempted=False,
            acknowledged=False,
            observed_state="unknown",
            newly_executed=False,
            error=error,
        )
