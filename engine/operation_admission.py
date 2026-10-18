"""Opt-in operation admission and adapter conformance profile.

This module is deliberately sidecar-only. It does not replace the default executor,
select the refund-intent profile, issue authority, or infer operation identity from a
planner. Stable operation discriminators are supplied by the responsible principal
inside an authority domain and attempts remain subordinate to that identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PROFILE_ID = "cognous-operation-admission/1"
ObservationState = Literal["applied", "partial", "absent", "unknown"]
ProviderAcceptance = Literal["accepted", "rejected", "unknown"]
DedupeMode = Literal["none", "client_reference", "provider_operation"]
DeadlineMode = Literal["none", "client_deadline", "provider_deadline"]
IdReuseMode = Literal["reject", "idempotent_same_operation"]


@dataclass(frozen=True, order=True)
class OperationDiscriminator:
    """Principal/domain-issued stable identity for one business operation."""

    principal_id: str
    authority_domain: str
    operation_id: str

    def __post_init__(self) -> None:
        for name, value in (
            ("principal_id", self.principal_id),
            ("authority_domain", self.authority_domain),
            ("operation_id", self.operation_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")

    @property
    def key(self) -> tuple[str, str, str]:
        return self.principal_id, self.authority_domain, self.operation_id


@dataclass(frozen=True)
class OperationAdmission:
    discriminator: OperationDiscriminator
    operation_commitment: str
    supersedes: OperationDiscriminator | None = None
    # The grant issuer that may write renewals of this operation (operation-identity/0.1).
    issuer: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation_commitment, str) or not self.operation_commitment.strip():
            raise ValueError("operation_commitment must be a non-empty string")
        if self.issuer is not None and (not isinstance(self.issuer, str) or not self.issuer.strip()):
            raise ValueError("issuer must be a non-empty string when declared")
        if self.supersedes is not None:
            if self.supersedes == self.discriminator:
                raise ValueError("an operation cannot supersede itself")
            if (
                self.supersedes.principal_id != self.discriminator.principal_id
                or self.supersedes.authority_domain != self.discriminator.authority_domain
            ):
                raise PermissionError("supersession must remain within principal and authority domain")


@dataclass(frozen=True)
class OperationAttempt:
    discriminator: OperationDiscriminator
    attempt_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, str) or not self.attempt_id.strip():
            raise ValueError("attempt_id must be a non-empty string")


@dataclass(frozen=True)
class OperationRenewal:
    """A new grant revision that continues an admitted operation (never a new operation)."""

    discriminator: OperationDiscriminator
    to_revision: str
    written_by: str


@dataclass(frozen=True)
class RenewalRefusal:
    """Recorded when a renewal is refused, so a worker's self-extension leaves a row."""

    discriminator: OperationDiscriminator
    to_revision: str
    written_by: str
    reason: str


@dataclass(frozen=True)
class AttemptJoin:
    """One join record per attempt: operation, attempt and licensing journal check by id."""

    discriminator: OperationDiscriminator
    attempt_id: str
    check_id: str
    resolver_id: str
    resolver_version: str
    class_max_age: int
    expected_effective_grant_digest: str | None = None
    # Field only. Names the surviving operation (never an attempt id); merge logic is a later stream.
    merged_into: OperationDiscriminator | None = None

    def __post_init__(self) -> None:
        for name in ("attempt_id", "check_id", "resolver_id", "resolver_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if type(self.class_max_age) is not int or self.class_max_age < 0:
            raise ValueError("class_max_age must be a non-negative integer")
        if self.merged_into is not None and not isinstance(self.merged_into, OperationDiscriminator):
            raise TypeError("merged_into must be an OperationDiscriminator, never an attempt id")
        if self.merged_into is not None and self.merged_into == self.discriminator:
            raise ValueError("an operation cannot be merged into itself")


@dataclass(frozen=True)
class AdapterConformance:
    adapter_id: str
    id_reuse: IdReuseMode
    client_reference_searchable: bool
    dedupe: DedupeMode
    deadlines: DeadlineMode
    observation_coverage: frozenset[ObservationState]
    watermark: str | None
    provider_acceptance_closure: bool

    def __post_init__(self) -> None:
        if not isinstance(self.adapter_id, str) or not self.adapter_id.strip():
            raise ValueError("adapter_id must be a non-empty string")
        allowed = {"applied", "partial", "absent", "unknown"}
        if not self.observation_coverage.issubset(allowed):
            raise ValueError("unsupported observation state declaration")
        if self.watermark is not None and (not isinstance(self.watermark, str) or not self.watermark.strip()):
            raise ValueError("watermark must be a non-empty string when declared")

    @property
    def qualification_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if not self.client_reference_searchable:
            reasons.append("client-reference searchability is required")
        if self.dedupe == "none":
            reasons.append("dedupe declaration is required")
        if self.deadlines == "none":
            reasons.append("deadline semantics are required")
        required_observations = {"applied", "partial", "absent", "unknown"}
        missing = sorted(required_observations - set(self.observation_coverage))
        if missing:
            reasons.append("observation coverage missing: " + ",".join(missing))
        if self.watermark is None:
            reasons.append("provider observation watermark is required")
        if not self.provider_acceptance_closure:
            reasons.append("provider-acceptance closure is required")
        return tuple(reasons)

    @property
    def qualified(self) -> bool:
        return not self.qualification_reasons


@dataclass(frozen=True)
class RetryDecision:
    permitted: bool
    reason: str


@dataclass
class OperationAdmissionRegistry:
    """In-memory conformance registry for the opt-in profile.

    Durability and distributed coordination are intentionally out of scope. The
    registry enforces identity and lineage semantics used by focused qualification.
    It grants no institutional authority and performs no destination effect.
    """

    admissions: dict[tuple[str, str, str], OperationAdmission] = field(default_factory=dict)
    attempts: dict[str, OperationAttempt] = field(default_factory=dict)
    superseded_by: dict[tuple[str, str, str], tuple[str, str, str]] = field(default_factory=dict)
    renewals: list[OperationRenewal] = field(default_factory=list)
    renewal_refusals: list[RenewalRefusal] = field(default_factory=list)
    joins: dict[str, AttemptJoin] = field(default_factory=dict)

    def admit(self, admission: OperationAdmission) -> None:
        key = admission.discriminator.key
        existing = self.admissions.get(key)
        if existing is not None:
            if existing != admission:
                raise PermissionError("operation discriminator collision with different content")
            return

        if admission.supersedes is not None:
            prior_key = admission.supersedes.key
            prior = self.admissions.get(prior_key)
            if prior is None:
                raise PermissionError("superseded operation must already be admitted")
            if prior_key in self.superseded_by:
                raise PermissionError("superseded operation already has a successor")
            # A replan must be a newly domain-issued operation identity, never a
            # planner mutation under the old stable discriminator.
            self.superseded_by[prior_key] = key

        self.admissions[key] = admission

    def register_attempt(self, attempt: OperationAttempt) -> None:
        key = attempt.discriminator.key
        if key not in self.admissions:
            raise PermissionError("attempt references an operation that was not domain-admitted")
        if key in self.superseded_by:
            raise PermissionError("superseded operation cannot receive a new attempt")
        existing = self.attempts.get(attempt.attempt_id)
        if existing is not None:
            if existing != attempt:
                raise PermissionError("attempt identity collision across operations")
            raise PermissionError("attempt identity reuse is not a new attempt")
        self.attempts[attempt.attempt_id] = attempt

    def record_renewal(self, renewal: OperationRenewal) -> None:
        """Accept a renewal only from the issuer recorded at admission; refuse and record otherwise."""
        key = renewal.discriminator.key
        admission = self.admissions.get(key)
        reason = None
        if admission is None:
            reason = "operation_not_admitted"
        elif key in self.superseded_by:
            reason = "operation_superseded"
        elif admission.issuer is None or renewal.written_by != admission.issuer:
            reason = "renewal_not_written_by_grant_issuer"
        if reason is not None:
            self.renewal_refusals.append(RenewalRefusal(
                renewal.discriminator, renewal.to_revision, renewal.written_by, reason))
            raise PermissionError(reason)
        self.renewals.append(renewal)

    def join_attempt(self, join: AttemptJoin) -> None:
        """Record the one join row for a registered attempt, by id."""
        attempt = self.attempts.get(join.attempt_id)
        if attempt is None or attempt.discriminator != join.discriminator:
            raise PermissionError("join must reference an attempt registered under the same operation")
        if join.attempt_id in self.joins:
            raise PermissionError("attempt already joined")
        if join.merged_into is not None and join.merged_into.key not in self.admissions:
            raise PermissionError("merge must name an admitted operation id")
        self.joins[join.attempt_id] = join

    def evaluate_retry(
        self,
        *,
        attempt: OperationAttempt,
        observation: ObservationState,
        provider_acceptance: ProviderAcceptance,
        adapter: AdapterConformance,
    ) -> RetryDecision:
        """Return a conservative retry decision for a previously dispatched attempt.

        Point-in-time absence is never positive retry evidence. This profile does
        not implement automatic retry at all: accepted/partial/unknown outcomes
        hold, while explicit provider rejection closes the prior attempt but still
        requires the principal/domain to issue a new attempt through its own policy.
        """
        retained = self.attempts.get(attempt.attempt_id)
        if retained != attempt:
            return RetryDecision(False, "attempt is not retained under the admitted operation")
        if not adapter.qualified:
            return RetryDecision(False, "adapter is not qualified for operation admission")
        if observation == "absent":
            return RetryDecision(False, "absent observation alone never permits retry")
        if provider_acceptance == "accepted":
            return RetryDecision(False, "provider accepted the prior attempt; reconcile outcome")
        if provider_acceptance == "unknown":
            return RetryDecision(False, "provider acceptance is unresolved; hold and reconcile")
        return RetryDecision(False, "provider rejection closes the attempt but does not auto-authorize retry")


def qualify_adapter(adapter: AdapterConformance) -> tuple[bool, tuple[str, ...]]:
    """Return bounded conformance only; qualification is not execution authority."""
    return adapter.qualified, adapter.qualification_reasons


__all__ = [
    "PROFILE_ID",
    "AdapterConformance",
    "AttemptJoin",
    "OperationAdmission",
    "OperationAdmissionRegistry",
    "OperationAttempt",
    "OperationDiscriminator",
    "OperationRenewal",
    "RenewalRefusal",
    "RetryDecision",
    "qualify_adapter",
]
