from __future__ import annotations

import copy
import uuid
from datetime import datetime
from typing import Any

from engine.safe_executor import ExecutionEnvelope, ExecutionResult, FrozenEnvelope, LocalExecutionPolicy, destination_error_text

PROFILE = "urn:cognous:profiles:local-authority-effect:0.1.0-proposed"


class AtomicLocalControlPlaneExecutor:
    def __init__(self, *, workflow: Any, destination: Any, policy: LocalExecutionPolicy, operation_identity: bool = False):
        """``operation_identity`` (opt-in, default off): enforce one commit per operation across renewals.

        On, every ``execute`` derives the operation id from the persisted Control Plane decision (never from the caller) and
        passes it to the destination, which must declare ``operation-identity/0.1`` (for example
        ``OperationIdentityAtomicAuthorityEffectDestination``). Off, nothing here changes.
        """
        self.workflow = workflow
        self.destination = destination
        self.policy = policy
        self.operation_identity = operation_identity

    def _derive_operation_id(self, snapshot: FrozenEnvelope) -> str:
        """The operation id of the issuer-approved decision behind this envelope, validated against the operation ledger.

        Same source as the adapter route (the validated binding's ``runtime_authority.operation_id``, set by the grant issuer
        inside the approval commitment). Anything that cannot be derived and validated refuses; nothing is guessed.
        """
        from .operation_identity_destination import CAPABILITY
        if getattr(self.destination, "operation_identity_capability", None) != CAPABILITY:
            raise PermissionError("operation_identity_capability_unavailable")
        profile = getattr(self.workflow, "runtime_authority_profile", None)
        if profile is None or getattr(profile, "operation_identity", None) is None:
            raise PermissionError("operation_identity_profile_unavailable")
        from agent_control_plane.operation_identity import valid_operation_id
        persisted = self.workflow.records.decision(snapshot.decision_id)
        if (persisted is None or persisted.result != "authorized" or persisted.binding is None
                or persisted.effect_id != snapshot.effect_id):
            raise PermissionError("operation_identity_decision_unavailable")
        binding = persisted.binding
        extension = binding.runtime_authority or {}
        operation_id, issuer = extension.get("operation_id"), extension.get("operation_issuer")
        if not valid_operation_id(operation_id) or not isinstance(issuer, str) or not issuer:
            raise PermissionError("operation_id_invalid")
        op = snapshot.operation
        domain = self.workflow._authority_domain(binding)
        if (binding.principal, domain, binding.grant_id, binding.grant_revision) != (
                op.principal, op.authority_domain, op.grant_id, op.grant_revision):
            raise PermissionError("operation_identity_binding_mismatch")
        status = self.workflow.records.operation_status(
            operation_id=operation_id, principal_id=binding.principal, authority_domain=domain, issuer=issuer,
            grant_id=binding.grant_id, grant_revision=binding.grant_revision)
        if status != "ok":
            raise PermissionError({"not_admitted": "operation_not_admitted", "collision": "operation_grant_mismatch",
                                   "renewal_unrecorded": "operation_renewal_unrecorded"}[status])
        return operation_id

    def provision_claim(self, *, proposal: Any, decision: Any, now: datetime, claim_id: str | None = None, budget_id: str | None = None, expires_at: str | None = None, decision_input_commitment: str | None = None, decision_input_profile_version: str | None = None):
        from agent_control_plane.local_authority_effect import provision_local_execution_claim
        return provision_local_execution_claim(self.workflow, proposal, decision, provision=self.destination.provision_claim, now=now, claim_id=claim_id, budget_id=budget_id, expires_at=expires_at, decision_input_commitment=decision_input_commitment, decision_input_profile_version=decision_input_profile_version)

    def execute(self, *, envelope: ExecutionEnvelope, claim_id: str, simulate: str | None = None, attempt_id: str | None = None) -> ExecutionResult:
        from .safe_executor import snapshot_or_record_rejection
        snapshot = snapshot_or_record_rejection(envelope, self.destination)
        try:
            self.policy.check(snapshot.operation)
        except PermissionError as exc:
            return self._denied(
                snapshot,
                str(exc),
                failure_class="policy_denial",
                reason_code="local_policy_rejected",
            )
        except Exception as exc:
            return self._denied(
                snapshot,
                str(exc),
                failure_class="evaluation_error",
                reason_code="local_policy_evaluation_error",
            )
        operation_kwargs: dict[str, str] = {}
        if self.operation_identity:
            try:
                operation_kwargs["operation_id"] = self._derive_operation_id(snapshot)
            except PermissionError as exc:
                return self._denied(
                    snapshot,
                    str(exc),
                    failure_class="authority_hold",
                    reason_code="current_authority_rejected",
                )
        attempt_id = attempt_id or str(uuid.uuid4())
        if getattr(self.destination, "_selection_store", None) is not None:
            # The policy this executor will evaluate must be the effective policy the claim binds (compared under the destination lock).
            from engine.registry_selection_authority import policy_digest_of
            operation_kwargs["policy_effective_digest"] = policy_digest_of(self.policy)
        try:
            value = self.destination.execute_claim_atomic(claim_id, snapshot, attempt_id=attempt_id, simulate=simulate, **operation_kwargs)
        except TimeoutError as exc:
            from engine.local_authority_effect import PostCommitObservationUnavailable
            return self._unresolved(
                snapshot, claim_id, attempt_id, exc,
                reason_code=(
                    "post_commit_observation_unavailable"
                    if isinstance(exc, PostCommitObservationUnavailable)
                    else "acknowledgement_unavailable"
                ),
            )
        except PermissionError as exc:
            return self._denied(
                snapshot,
                str(exc),
                failure_class="authority_hold",
                reason_code="current_authority_rejected",
                typed=exc,
            )
        except Exception as exc:
            # An error here may come after the durable commit (the destination cannot always say). Only a
            # reconciliation that establishes no durable effect and an unconsumed claim keeps it a pre-dispatch refusal.
            try:
                recovery = self.destination.reconcile_claim(claim_id, snapshot)
            except Exception:
                recovery = None
            if (isinstance(recovery, dict) and recovery.get("status") == "hold"
                    and recovery.get("reason") == "no_durable_effect_for_claim"
                    and recovery.get("claim_state") == "issued"):
                return self._denied(
                    snapshot,
                    destination_error_text(exc),
                    failure_class="dispatch_error",
                    reason_code="dispatch_exception",
                )
            return self._unresolved(snapshot, claim_id, attempt_id, exc, reason_code="dispatch_outcome_unresolved",
                                    recovery=recovery if isinstance(recovery, dict) else None, detail=destination_error_text(exc))
        observation = value["observation"]
        return ExecutionResult(status="partial" if observation["state"] == "partial" else "executed", decision_id=snapshot.decision_id, effect_id=snapshot.effect_id, attempt_id=attempt_id, attempted=True, acknowledged=True, observed_state=observation["state"], newly_executed=True, observation=copy.deepcopy(observation), control_plane_evidence={"local_authority_effect_profile":PROFILE,"claim_id":claim_id,"linearized_at":value["linearized_at"],"authority_receipt":copy.deepcopy(value["authority_receipt"])})

    def _unresolved(self, snapshot: FrozenEnvelope, claim_id: str, attempt_id: str, exc: Exception, *, reason_code: str,
                    recovery: dict | None = None, detail: str | None = None) -> ExecutionResult:
        """Report a possibly committed effect as ``unknown``; never as a refusal and never as retryable."""
        if recovery is None:
            try:
                recovery = self.destination.reconcile_claim(claim_id, snapshot)
            except Exception:
                # Effect state stays unresolved; never downgrade a possible effect to a refusal.
                recovery = {"status": "unknown", "retry_eligible": False, "reason": "reconciliation_unavailable"}
        if recovery.get("retry_eligible"):
            recovery = {**recovery, "retry_eligible": False}
        evidence = {"failure_record_id": None, "failure_class": "dispatch_error"}
        try:
            evidence["failure_record_id"] = self.destination.record_failure(
                snapshot,
                failure_class="dispatch_error",
                reason_code=reason_code,
                stage="post_dispatch",
                attempt_id=attempt_id,
                detail=detail if detail is not None else str(exc),
            )
        except Exception:
            evidence["failure_record_unavailable"] = True
        return ExecutionResult(
            status="unknown",
            decision_id=snapshot.decision_id,
            effect_id=snapshot.effect_id,
            attempt_id=attempt_id,
            attempted=True,
            acknowledged=False,
            observed_state=recovery["status"] if recovery["status"] in {"applied","partial"} else "unknown",
            newly_executed=False,
            observation=recovery,
            error=str(exc) if detail is None else detail,
            control_plane_evidence=evidence,
        )

    def reconcile(self, *, claim_id: str, envelope: ExecutionEnvelope) -> ExecutionResult:
        from .safe_executor import snapshot_or_record_rejection
        snapshot = snapshot_or_record_rejection(envelope, self.destination)
        value = self.destination.reconcile_claim(claim_id, snapshot)
        state = value["status"] if value["status"] in {"applied","partial"} else "unknown"
        return ExecutionResult(status="observed", decision_id=snapshot.decision_id, effect_id=snapshot.effect_id, attempt_id=None, attempted=False, acknowledged=False, observed_state=state, newly_executed=False, observation=value)

    def _denied(
        self,
        snapshot: FrozenEnvelope,
        error: str,
        *,
        failure_class: str,
        reason_code: str,
        typed: Exception | None = None,
    ) -> ExecutionResult:
        failure_id = self.destination.record_failure(
            snapshot,
            failure_class=failure_class,
            reason_code=reason_code,
            stage="pre_dispatch",
            detail=error,
        )
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
            control_plane_evidence=self._denied_evidence(failure_id, failure_class, typed),
        )

    @staticmethod
    def _denied_evidence(failure_id: Any, failure_class: str, typed: Exception | None) -> dict[str, Any]:
        evidence: dict[str, Any] = {"failure_record_id": failure_id, "failure_class": failure_class}
        from engine.registry_selection_authority import SelectionRefusal
        if isinstance(typed, SelectionRefusal):
            # Typed fields travel as data; no consumer parses message text (ADR 7.4).
            evidence["selection_refusal"] = {"code": typed.code, "refusal_class": typed.refusal_class, "cause": typed.cause}
        return evidence
