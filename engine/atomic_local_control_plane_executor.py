from __future__ import annotations

import copy
import uuid
from datetime import datetime
from typing import Any

from engine.safe_executor import ExecutionEnvelope, ExecutionResult, FrozenEnvelope, LocalExecutionPolicy, snapshot_envelope

PROFILE = "urn:cognous:profiles:local-authority-effect:0.1.0-proposed"


class AtomicLocalControlPlaneExecutor:
    def __init__(self, *, workflow: Any, destination: Any, policy: LocalExecutionPolicy):
        self.workflow = workflow
        self.destination = destination
        self.policy = policy

    def provision_claim(self, *, proposal: Any, decision: Any, now: datetime, claim_id: str | None = None, budget_id: str | None = None, expires_at: str | None = None, decision_input_commitment: str | None = None, decision_input_profile_version: str | None = None):
        from agent_control_plane.local_authority_effect import provision_local_execution_claim
        return provision_local_execution_claim(self.workflow, proposal, decision, provision=self.destination.provision_claim, now=now, claim_id=claim_id, budget_id=budget_id, expires_at=expires_at, decision_input_commitment=decision_input_commitment, decision_input_profile_version=decision_input_profile_version)

    def execute(self, *, envelope: ExecutionEnvelope, claim_id: str, simulate: str | None = None, attempt_id: str | None = None) -> ExecutionResult:
        snapshot = snapshot_envelope(envelope)
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
        attempt_id = attempt_id or str(uuid.uuid4())
        try:
            value = self.destination.execute_claim_atomic(claim_id, snapshot, attempt_id=attempt_id, simulate=simulate)
        except TimeoutError as exc:
            recovery = self.destination.reconcile_claim(claim_id, snapshot)
            failure_id = self.destination.record_failure(
                snapshot,
                failure_class="dispatch_error",
                reason_code="acknowledgement_unavailable",
                stage="post_dispatch",
                attempt_id=attempt_id,
                detail=str(exc),
            )
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
                error=str(exc),
                control_plane_evidence={
                    "failure_record_id": failure_id,
                    "failure_class": "dispatch_error",
                },
            )
        except PermissionError as exc:
            return self._denied(
                snapshot,
                str(exc),
                failure_class="authority_hold",
                reason_code="current_authority_rejected",
            )
        except Exception as exc:
            return self._denied(
                snapshot,
                str(exc),
                failure_class="dispatch_error",
                reason_code="dispatch_exception",
            )
        observation = value["observation"]
        return ExecutionResult(status="partial" if observation["state"] == "partial" else "executed", decision_id=snapshot.decision_id, effect_id=snapshot.effect_id, attempt_id=attempt_id, attempted=True, acknowledged=True, observed_state=observation["state"], newly_executed=True, observation=copy.deepcopy(observation), control_plane_evidence={"local_authority_effect_profile":PROFILE,"claim_id":claim_id,"linearized_at":value["linearized_at"]})

    def reconcile(self, *, claim_id: str, envelope: ExecutionEnvelope) -> ExecutionResult:
        snapshot = snapshot_envelope(envelope)
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
            control_plane_evidence={
                "failure_record_id": failure_id,
                "failure_class": failure_class,
            },
        )
