"""Opt-in same-host atomic authority/effect profile for synthetic refunds.

The profile deliberately uses ONE SQLite database as the authoritative store for:
- mutable authorization-critical state,
- exact execution claims,
- shared effect budgets, and
- the protected synthetic effect.

It does not change the legacy executor path.
"""
from __future__ import annotations

import copy
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from .safe_executor import (
    exact_amount_equal,
    DurableRefundDestination,
    FrozenEnvelope,
)

PROFILE = "urn:cognous:profiles:local-authority-effect:0.1.0-proposed"


class PostCommitObservationUnavailable(TimeoutError):
    """The atomic effect is committed and its claim consumed, but the post-commit read failed.

    A TimeoutError subclass so every caller that already treats an unavailable acknowledgement
    as an unresolved (``unknown``) outcome keeps doing so; it is never a pre-dispatch refusal.
    """
MARKER_TABLE = "authority_effect_profile_v1"
# Retained (grant_id, revision) pairs that were revoked on this destination; created on first revocation.
REVOCATION_TABLE = "authority_grant_revocations_v1"
APPROVAL_REVOCATION_TABLE = "authority_approval_revocations_v1"


# ODES time semantics section 1 (open-decision-evidence-standard docs/engineering/ODES-TIME-SEMANTICS.md): an RFC 3339
# instant with an explicit offset, ASCII digits, one to six fraction digits (a seventh is refused, never truncated),
# no leap second, offset hour 00-23 and minute 00-59. Calendar validity is left to datetime.fromisoformat.
_INSTANT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,6})?"
    r"(Z|[+-]([01][0-9]|2[0-3]):[0-5][0-9])",
    re.ASCII,
)


def _parse_time(value: str) -> datetime:
    if not isinstance(value, str) or _INSTANT.fullmatch(value) is None:
        raise ValueError("timestamp is not an RFC 3339 instant with an explicit offset")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


class AtomicAuthorityEffectDestination(DurableRefundDestination):
    """Participating local SQLite authority/effect boundary.

    Once activated, all profile-relevant invalidating writes MUST go through this
    database. External resolver/cache changes are outside the guarantee.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], datetime],
        database_name: str = "refunds.sqlite3",
    ):
        self.clock = clock
        super().__init__(root, database_name=database_name)
        self._activate_profile()

    def _trusted_now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("trusted profile clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _activate_profile(self) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Profiles have separate ownership contracts; do not implicitly combine them.
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name IN ('refund_intent_registry_v1', 'refund_intent_claims_v1')"
            ).fetchone():
                conn.execute("ROLLBACK")
                raise PermissionError("authority-effect profile cannot share an intent-profile database")
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='effective_grants_v1'"
            ).fetchone():
                # The effective-grant precondition is not evaluated on the atomic route, so the two
                # profiles are never combined on one database (refused in both directions).
                conn.execute("ROLLBACK")
                raise PermissionError("authority-effect profile cannot share an effective-grant database")
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (MARKER_TABLE,),
            ).fetchone()
            if exists is None:
                effect_count = int(conn.execute("SELECT COUNT(*) FROM effects").fetchone()[0])
                attempt_count = int(conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0])
                if effect_count or attempt_count:
                    conn.execute("ROLLBACK")
                    raise PermissionError(
                        "atomic authority/effect profile cannot activate over legacy effects or attempts"
                    )
                statements = [
                    f"""CREATE TABLE {MARKER_TABLE} (
                        profile TEXT PRIMARY KEY,
                        activated_at TEXT NOT NULL
                    )""",
                    """CREATE TABLE authority_grants_v1 (
                        grant_id TEXT PRIMARY KEY,
                        revision TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('active','suspended','revoked','unknown')),
                        institution_id TEXT NOT NULL,
                        authority_domain TEXT NOT NULL,
                        tenant_id TEXT,
                        not_before TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    )""",
                    """CREATE TABLE authority_approvals_v1 (
                        approval_ref TEXT PRIMARY KEY,
                        role_id TEXT NOT NULL,
                        approver TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('active','revoked','unknown')),
                        grant_id TEXT NOT NULL,
                        grant_revision TEXT NOT NULL,
                        proposal_commitment TEXT NOT NULL,
                        policy_versions_json TEXT NOT NULL,
                        tenant_id TEXT
                    )""",
                    """CREATE TABLE authority_policies_v1 (
                        ref TEXT PRIMARY KEY,
                        version TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('active','superseded','unknown')),
                        tenant_id TEXT
                    )""",
                    """CREATE TABLE authority_evidence_v1 (
                        obligation_id TEXT PRIMARY KEY,
                        source_ref TEXT NOT NULL,
                        required INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        max_age_seconds INTEGER NOT NULL,
                        unknown_behavior TEXT NOT NULL,
                        state TEXT NOT NULL CHECK(state IN ('current','stale','unknown')),
                        observed_at TEXT NOT NULL
                    )""",
                    """CREATE TABLE execution_claims_v1 (
                        claim_id TEXT PRIMARY KEY,
                        claim_commitment TEXT NOT NULL,
                        claim_json TEXT NOT NULL,
                        effect_id TEXT NOT NULL,
                        decision_id TEXT NOT NULL,
                        grant_id TEXT NOT NULL,
                        grant_revision TEXT NOT NULL,
                        operation_commitment TEXT NOT NULL,
                        effect_operation_digest TEXT,
                        budget_id TEXT NOT NULL,
                        max_effects INTEGER NOT NULL,
                        not_before TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        tenant_id TEXT,
                        state TEXT NOT NULL CHECK(state IN ('issued','consumed'))
                    )""",
                    """CREATE TABLE effect_budgets_v1 (
                        budget_id TEXT PRIMARY KEY,
                        max_effects INTEGER NOT NULL,
                        used_effects INTEGER NOT NULL
                    )""",
                ]
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    f"INSERT INTO {MARKER_TABLE}(profile,activated_at) VALUES(?,?)",
                    (PROFILE, self._trusted_now().isoformat()),
                )
            else:
                row = conn.execute(f"SELECT profile FROM {MARKER_TABLE}").fetchone()
                if row is None or row["profile"] != PROFILE:
                    conn.execute("ROLLBACK")
                    raise PermissionError("unsupported authority/effect profile marker")
                claim_columns = {
                    item["name"]
                    for item in conn.execute("PRAGMA table_info(execution_claims_v1)").fetchall()
                }
                if "effect_operation_digest" not in claim_columns:
                    conn.execute(
                        "ALTER TABLE execution_claims_v1 ADD COLUMN effect_operation_digest TEXT"
                    )
                for table in (
                    "authority_grants_v1",
                    "authority_approvals_v1",
                    "authority_policies_v1",
                    "execution_claims_v1",
                ):
                    columns = {
                        item["name"]
                        for item in conn.execute(f"PRAGMA table_info({table})").fetchall()
                    }
                    if "tenant_id" not in columns:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT")
            # Additive migration: historical effects get no fabricated receipts.
            conn.execute("""CREATE TABLE IF NOT EXISTS authority_effect_receipts_v1 (
                attempt_id TEXT PRIMARY KEY,
                effect_id TEXT NOT NULL,
                claim_id TEXT NOT NULL,
                receipt_json TEXT NOT NULL
            )""")
            conn.execute("COMMIT")

    def _profile_present(self, conn: sqlite3.Connection) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (MARKER_TABLE,),
        ).fetchone() is not None

    def _claim_model(self, claim: Any):
        from agent_control_plane.local_authority_effect import (
            LocalExecutionClaim,
            TenantLocalExecutionClaim,
            verify_local_execution_claim,
        )

        if isinstance(claim, LocalExecutionClaim):
            model = claim
        elif isinstance(claim, dict) and (
            "tenant_id" in claim or "authorization_generation" in claim
        ):
            model = TenantLocalExecutionClaim.model_validate(claim)
        else:
            model = LocalExecutionClaim.model_validate(claim)
        if not verify_local_execution_claim(model):
            raise PermissionError("execution claim commitment is invalid")
        if model.profile != PROFILE or model.authorizing_by_possession is not False:
            raise PermissionError("unsupported execution claim profile")
        if model.grant_status != "active":
            raise PermissionError("execution claim grant projection is not active")
        if any(item.status != "active" for item in model.approval_state):
            raise PermissionError("execution claim approval projection is not active")
        if any(item.status != "active" for item in model.policy_state):
            raise PermissionError("execution claim policy projection is not active")
        tenant_id = getattr(model, "tenant_id", None)
        if tenant_id is not None:
            if any(getattr(item, "tenant_id", None) != tenant_id for item in model.approval_state):
                raise PermissionError("execution claim approval tenant projection mismatch")
            if any(getattr(item, "tenant_id", None) != tenant_id for item in model.policy_state):
                raise PermissionError("execution claim policy tenant projection mismatch")
        for item in model.evidence_state:
            decision_critical = item.required or item.unknown_behavior == "hold_effect"
            if decision_critical and item.state != "current":
                raise PermissionError("execution claim evidence projection is not current")
        return model

    def provision_claim(self, claim: Any) -> None:
        """Trusted-host provisioning into the authoritative local profile store.

        Existing mutable authority rows are never overwritten by a stale claim.
        Re-provisioning cannot revive revoked/superseded/non-current state.
        """
        model = self._claim_model(claim)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not self._profile_present(conn):
                conn.execute("ROLLBACK")
                raise PermissionError("atomic authority/effect profile is not active")

            if conn.execute(
                "SELECT 1 FROM execution_claims_v1 WHERE claim_id=?", (model.claim_id,)
            ).fetchone():
                conn.execute("ROLLBACK")
                raise PermissionError("execution claim already provisioned")

            grant = conn.execute(
                "SELECT * FROM authority_grants_v1 WHERE grant_id=?", (model.grant_id,)
            ).fetchone()
            if self._revision_revoked(conn, model.grant_id, model.grant_revision):
                conn.execute("ROLLBACK")
                raise PermissionError("existing authoritative grant state rejects claim provisioning")
            if grant is None:
                conn.execute(
                    """INSERT INTO authority_grants_v1
                    (grant_id,revision,status,institution_id,authority_domain,tenant_id,not_before,expires_at)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        model.grant_id,
                        model.grant_revision,
                        model.grant_status,
                        model.institution_id,
                        model.authority_domain,
                        getattr(model, "tenant_id", None),
                        model.not_before,
                        model.expires_at,
                    ),
                )
            else:
                expected = (
                    model.grant_revision,
                    model.grant_status,
                    model.institution_id,
                    model.authority_domain,
                    getattr(model, "tenant_id", None),
                )
                actual = (
                    grant["revision"],
                    grant["status"],
                    grant["institution_id"],
                    grant["authority_domain"],
                    grant["tenant_id"],
                )
                if actual != expected:
                    conn.execute("ROLLBACK")
                    raise PermissionError("existing authoritative grant state rejects claim provisioning")

            for approval in model.approval_state:
                if self._approval_revoked(conn, approval.approval_ref):
                    conn.execute("ROLLBACK")
                    raise PermissionError("existing authoritative approval state rejects claim provisioning")
                prior = conn.execute(
                    "SELECT * FROM authority_approvals_v1 WHERE approval_ref=?",
                    (approval.approval_ref,),
                ).fetchone()
                expected = (
                    approval.role_id,
                    approval.approver,
                    approval.status,
                    approval.grant_id,
                    approval.grant_revision,
                    approval.proposal_commitment,
                    json.dumps(approval.policy_versions, sort_keys=True, separators=(",", ":")),
                    getattr(approval, "tenant_id", None),
                )
                if prior is None:
                    conn.execute(
                        """INSERT INTO authority_approvals_v1
                        (approval_ref,role_id,approver,status,grant_id,grant_revision,
                         proposal_commitment,policy_versions_json,tenant_id)
                        VALUES(?,?,?,?,?,?,?,?,?)""",
                        (approval.approval_ref, *expected),
                    )
                else:
                    actual = (
                        prior["role_id"], prior["approver"], prior["status"],
                        prior["grant_id"], prior["grant_revision"],
                        prior["proposal_commitment"], prior["policy_versions_json"],
                        prior["tenant_id"],
                    )
                    if actual != expected:
                        conn.execute("ROLLBACK")
                        raise PermissionError("existing authoritative approval state rejects claim provisioning")

            for policy in model.policy_state:
                prior = conn.execute(
                    "SELECT * FROM authority_policies_v1 WHERE ref=?", (policy.ref,)
                ).fetchone()
                expected = (policy.version, policy.status, getattr(policy, "tenant_id", None))
                if prior is None:
                    conn.execute(
                        "INSERT INTO authority_policies_v1(ref,version,status,tenant_id) VALUES(?,?,?,?)",
                        (policy.ref, *expected),
                    )
                elif (prior["version"], prior["status"], prior["tenant_id"]) != expected:
                    conn.execute("ROLLBACK")
                    raise PermissionError("existing authoritative policy state rejects claim provisioning")

            for evidence in model.evidence_state:
                prior = conn.execute(
                    "SELECT * FROM authority_evidence_v1 WHERE obligation_id=?",
                    (evidence.obligation_id,),
                ).fetchone()
                expected = (
                    evidence.source_ref,
                    1 if evidence.required else 0,
                    evidence.kind,
                    evidence.max_age_seconds,
                    evidence.unknown_behavior,
                    evidence.state,
                    evidence.observed_at,
                )
                if prior is None:
                    conn.execute(
                        """INSERT INTO authority_evidence_v1
                        (obligation_id,source_ref,required,kind,max_age_seconds,
                         unknown_behavior,state,observed_at)
                        VALUES(?,?,?,?,?,?,?,?)""",
                        (evidence.obligation_id, *expected),
                    )
                else:
                    actual = (
                        prior["source_ref"], prior["required"], prior["kind"],
                        prior["max_age_seconds"], prior["unknown_behavior"],
                        prior["state"], prior["observed_at"],
                    )
                    if actual != expected:
                        conn.execute("ROLLBACK")
                        raise PermissionError("existing authoritative evidence state rejects claim provisioning")

            budget = conn.execute(
                "SELECT * FROM effect_budgets_v1 WHERE budget_id=?", (model.budget_id,)
            ).fetchone()
            if budget is None:
                conn.execute(
                    "INSERT INTO effect_budgets_v1(budget_id,max_effects,used_effects) VALUES(?,?,0)",
                    (model.budget_id, model.max_effects),
                )
            elif int(budget["max_effects"]) != int(model.max_effects):
                conn.execute("ROLLBACK")
                raise PermissionError("shared budget limit mismatch")

            raw = model.model_dump(mode="json", exclude_none=False)
            conn.execute(
                """INSERT INTO execution_claims_v1
                (claim_id,claim_commitment,claim_json,effect_id,decision_id,grant_id,
                 grant_revision,operation_commitment,effect_operation_digest,budget_id,max_effects,not_before,
                 expires_at,tenant_id,state)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    model.claim_id,
                    model.claim_commitment,
                    json.dumps(raw, sort_keys=True, separators=(",", ":")),
                    model.effect_id,
                    model.decision_id,
                    model.grant_id,
                    model.grant_revision,
                    model.operation_commitment,
                    None,
                    model.budget_id,
                    model.max_effects,
                    model.not_before,
                    model.expires_at,
                    getattr(model, "tenant_id", None),
                    "issued",
                ),
            )
            conn.execute("COMMIT")

    @staticmethod
    def _revision_revoked(conn: sqlite3.Connection, grant_id: str, revision: str) -> bool:
        """True when (grant_id, revision) was revoked on this destination; revoked is terminal per revision."""
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (REVOCATION_TABLE,)
        ).fetchone() is None:
            return False
        return conn.execute(
            f"SELECT 1 FROM {REVOCATION_TABLE} WHERE grant_id=? AND revision=?", (grant_id, revision)
        ).fetchone() is not None

    @staticmethod
    def _record_revoked_revision(conn: sqlite3.Connection, grant_id: str, revision: str) -> None:
        # Created on the first revocation only, so a database that never revokes keeps its schema.
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {REVOCATION_TABLE} (
            grant_id TEXT NOT NULL,
            revision TEXT NOT NULL,
            PRIMARY KEY(grant_id, revision)
        )""")
        conn.execute(
            f"INSERT OR IGNORE INTO {REVOCATION_TABLE}(grant_id,revision) VALUES(?,?)", (grant_id, revision)
        )

    def set_grant_status(self, grant_id: str, *, status: str, revision: str | None = None) -> None:
        """Trusted authority writer for the grant row.

        ``revoked`` is terminal for a (grant_id, revision) on this destination: once written, no later update
        may set that revision to any other status (a successor revision is the way forward). ``suspended`` and
        ``unknown`` stay reversible. The grant object here carries no status version, so none is compared.
        """
        if status not in {"active", "suspended", "revoked", "unknown"}:
            raise ValueError("unsupported grant status")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT revision,status FROM authority_grants_v1 WHERE grant_id=?", (grant_id,)
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise KeyError(grant_id)
            new_revision = revision or row["revision"]
            if status != "revoked" and (
                (row["status"] == "revoked" and new_revision == row["revision"])
                or self._revision_revoked(conn, grant_id, new_revision)
            ):
                conn.execute("ROLLBACK")
                raise PermissionError("grant revision is revoked; a successor revision is required")
            if row["status"] == "revoked":
                # Also covers a row revoked before revocations were retained.
                self._record_revoked_revision(conn, grant_id, row["revision"])
            if status == "revoked":
                self._record_revoked_revision(conn, grant_id, new_revision)
            conn.execute(
                "UPDATE authority_grants_v1 SET status=?,revision=? WHERE grant_id=?",
                (status, new_revision, grant_id),
            )
            conn.execute("COMMIT")

    @staticmethod
    def _approval_revoked(conn: sqlite3.Connection, approval_ref: str) -> bool:
        """True when this approval_ref was revoked on this destination; revoked is terminal per approval_ref."""
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (APPROVAL_REVOCATION_TABLE,)
        ).fetchone() is None:
            return False
        return conn.execute(
            f"SELECT 1 FROM {APPROVAL_REVOCATION_TABLE} WHERE approval_ref=?", (approval_ref,)
        ).fetchone() is not None

    @staticmethod
    def _record_revoked_approval(conn: sqlite3.Connection, approval_ref: str) -> None:
        # Created on the first revocation only, so a database that never revokes an approval keeps its schema.
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {APPROVAL_REVOCATION_TABLE} (
            approval_ref TEXT PRIMARY KEY
        )""")
        conn.execute(
            f"INSERT OR IGNORE INTO {APPROVAL_REVOCATION_TABLE}(approval_ref) VALUES(?)", (approval_ref,)
        )

    def set_approval_status(self, approval_ref: str, *, status: str) -> None:
        """Trusted authority writer for the approval row.

        ``revoked`` is terminal for an approval_ref on this destination: once written, no later update may set it
        to any other status (a new approval with a new approval_ref is the way forward). ``unknown`` stays
        reversible. Parity with ``set_grant_status``.
        """
        if status not in {"active", "revoked", "unknown"}:
            raise ValueError("unsupported approval status")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM authority_approvals_v1 WHERE approval_ref=?", (approval_ref,)
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise KeyError(approval_ref)
            if status != "revoked" and (
                row["status"] == "revoked" or self._approval_revoked(conn, approval_ref)
            ):
                conn.execute("ROLLBACK")
                raise PermissionError("approval is revoked; a new approval is required")
            if status == "revoked" or row["status"] == "revoked":
                # The second case covers a row revoked before revocations were retained.
                self._record_revoked_approval(conn, approval_ref)
            conn.execute(
                "UPDATE authority_approvals_v1 SET status=? WHERE approval_ref=?", (status, approval_ref)
            )
            conn.execute("COMMIT")

    def set_policy_state(self, ref: str, *, version: str | None = None, status: str | None = None) -> None:
        if status is not None and status not in {"active", "superseded", "unknown"}:
            raise ValueError("unsupported policy status")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT version,status,tenant_id FROM authority_policies_v1 WHERE ref=?", (ref,)
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise KeyError(ref)
            conn.execute(
                "UPDATE authority_policies_v1 SET version=?,status=? WHERE ref=?",
                (version or row["version"], status or row["status"], ref),
            )
            conn.execute("COMMIT")

    def set_evidence_state(
        self,
        obligation_id: str,
        *,
        state: str,
        observed_at: str | None = None,
    ) -> None:
        if state not in {"current", "stale", "unknown"}:
            raise ValueError("unsupported evidence state")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT observed_at FROM authority_evidence_v1 WHERE obligation_id=?",
                (obligation_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise KeyError(obligation_id)
            conn.execute(
                "UPDATE authority_evidence_v1 SET state=?,observed_at=? WHERE obligation_id=?",
                (state, observed_at or row["observed_at"], obligation_id),
            )
            conn.execute("COMMIT")

    def remove_authoritative_state(
        self,
        kind: Literal["grant", "approval", "policy", "evidence"],
        key: str,
    ) -> None:
        table, column = {
            "grant": ("authority_grants_v1", "grant_id"),
            "approval": ("authority_approvals_v1", "approval_ref"),
            "policy": ("authority_policies_v1", "ref"),
            "evidence": ("authority_evidence_v1", "obligation_id"),
        }[kind]
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(f"DELETE FROM {table} WHERE {column}=?", (key,))
            conn.execute("COMMIT")

    def _update_required_row(self, table: str, key_col: str, key: str, field: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"SELECT 1 FROM {table} WHERE {key_col}=?", (key,)
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise KeyError(key)
            conn.execute(
                f"UPDATE {table} SET {field}=? WHERE {key_col}=?",
                (value, key),
            )
            conn.execute("COMMIT")

    @staticmethod
    def _operation_commitment(snapshot: FrozenEnvelope) -> str:
        from agent_control_plane.bounded import commitment

        op = snapshot.operation
        value = {
            "actor": op.actor,
            "principal": op.principal,
            "institution_id": op.institution_id,
            "authority_domain": op.authority_domain,
            "manifest_id": op.manifest_id,
            "manifest_version": op.manifest_version,
            "manifest_digest": op.manifest_digest,
            "action_id": op.action_id,
            "adapter_id": op.adapter_id,
            "target": op.target,
            "payload": op.payload(),
            "payload_commitment": op.payload_commitment,
            "requested_permissions": list(op.requested_permissions),
            "amount": op.amount,
            "unit": op.unit,
            "effects": op.effects,
            "authority_context_id": op.authority_context_id,
            "requirement_id": op.requirement_id,
            "grant_id": op.grant_id,
            "grant_revision": op.grant_revision,
        }
        if op.tenant_id is not None:
            value["tenant_id"] = op.tenant_id
        return commitment(value)

    @staticmethod
    def _proposal_bound(claim: Any, snapshot: FrozenEnvelope) -> bool:
        """The envelope's proposal_commitment is the one the claim was issued for.

        Every approval projection in the claim carries the binding's proposal commitment. A claim with no approval
        projection is bound through the control plane's effect id derivation,
        effect_id = commitment({proposal, grant_id, grant_revision}), which the claim's effect_id already commits to.
        """
        op = snapshot.operation
        if claim.approval_state:
            return all(item.proposal_commitment == op.proposal_commitment for item in claim.approval_state)
        from agent_control_plane.bounded import commitment

        return claim.effect_id == commitment({
            "proposal": op.proposal_commitment,
            "grant_id": op.grant_id,
            "grant_revision": op.grant_revision,
        })

    def _validate_claim_state(self, conn: sqlite3.Connection, claim: Any, snapshot: FrozenEnvelope, now: datetime) -> None:
        op = snapshot.operation
        exact = {
            "effect_id": claim.effect_id == snapshot.effect_id,
            "decision_id": claim.decision_id == snapshot.decision_id,
            "institution_id": claim.institution_id == op.institution_id,
            "authority_domain": claim.authority_domain == op.authority_domain,
            "actor": claim.actor == op.actor,
            "principal": claim.principal == op.principal,
            "grant_id": claim.grant_id == op.grant_id,
            "grant_revision": claim.grant_revision == op.grant_revision,
            "tenant_id": getattr(claim, "tenant_id", None) == op.tenant_id,
            "manifest_id": claim.manifest_id == op.manifest_id,
            "manifest_version": claim.manifest_version == op.manifest_version,
            "manifest_digest": claim.manifest_digest == op.manifest_digest,
            "action_id": claim.action_id == op.action_id,
            "adapter_id": claim.adapter_id == op.adapter_id,
            "target": claim.target == op.target,
            "payload_commitment": claim.payload_commitment == op.payload_commitment,
            "requested_permissions": tuple(claim.requested_permissions) == op.requested_permissions,
            "amount": exact_amount_equal(claim.amount, op.amount),
            "unit": claim.unit == op.unit,
            "effects": int(claim.effects) == int(op.effects),
            "operation_commitment": claim.operation_commitment == self._operation_commitment(snapshot),
            # Envelope members outside operation_commitment, bound to what the claim already commits to.
            "proposal_commitment": self._proposal_bound(claim, snapshot),
            "effective_max_effects": int(claim.max_effects) == int(op.effective_max_effects),
        }
        mismatches = [name for name, ok in exact.items() if not ok]
        if mismatches:
            raise PermissionError("execution claim operation mismatch: " + ",".join(mismatches))

        if not (_parse_time(claim.not_before) <= now < _parse_time(claim.expires_at)):
            raise PermissionError("execution claim outside validity interval")

        grant = conn.execute(
            "SELECT * FROM authority_grants_v1 WHERE grant_id=?", (claim.grant_id,)
        ).fetchone()
        if grant is None:
            raise PermissionError("authoritative grant state unavailable")
        if (
            grant["revision"] != claim.grant_revision
            or grant["status"] != "active"
            or grant["institution_id"] != claim.institution_id
            or grant["authority_domain"] != claim.authority_domain
            or grant["tenant_id"] != getattr(claim, "tenant_id", None)
            or self._revision_revoked(conn, claim.grant_id, claim.grant_revision)
        ):
            raise PermissionError("authoritative grant state invalid")
        if not (_parse_time(grant["not_before"]) <= now < _parse_time(grant["expires_at"])):
            raise PermissionError("authoritative grant outside validity interval")

        for expected in claim.approval_state:
            row = conn.execute(
                "SELECT * FROM authority_approvals_v1 WHERE approval_ref=?",
                (expected.approval_ref,),
            ).fetchone()
            if row is None:
                raise PermissionError("authoritative approval state unavailable")
            if (
                row["status"] != "active"
                or self._approval_revoked(conn, expected.approval_ref)
                or row["grant_id"] != claim.grant_id
                or row["grant_revision"] != claim.grant_revision
                or row["proposal_commitment"] != expected.proposal_commitment
                or row["policy_versions_json"]
                != json.dumps(expected.policy_versions, sort_keys=True, separators=(",", ":"))
                or row["tenant_id"] != getattr(expected, "tenant_id", None)
            ):
                raise PermissionError("authoritative approval state invalid")

        for expected in claim.policy_state:
            row = conn.execute(
                "SELECT version,status,tenant_id FROM authority_policies_v1 WHERE ref=?",
                (expected.ref,),
            ).fetchone()
            if row is None:
                raise PermissionError("authoritative policy state unavailable")
            if (
                row["version"] != expected.version
                or row["status"] != "active"
                or row["tenant_id"] != getattr(expected, "tenant_id", None)
            ):
                raise PermissionError("authoritative policy state invalid")

        for expected in claim.evidence_state:
            row = conn.execute(
                "SELECT * FROM authority_evidence_v1 WHERE obligation_id=?",
                (expected.obligation_id,),
            ).fetchone()
            if row is None:
                raise PermissionError("authoritative evidence state unavailable")
            if row["source_ref"] != expected.source_ref:
                raise PermissionError("authoritative evidence source changed")
            positive_required = bool(row["required"]) or row["unknown_behavior"] == "hold_effect"
            if positive_required and row["state"] != "current":
                raise PermissionError("authoritative evidence state invalid")
            observed = _parse_time(row["observed_at"])
            if observed > now:
                raise PermissionError("authoritative evidence observation is in the future")
            if positive_required and (now - observed).total_seconds() > int(row["max_age_seconds"]):
                raise PermissionError("authoritative evidence expired")

    def _transaction_stage(self, stage: str) -> None:
        """Internal no-op hook used by deterministic qualification subclasses."""
        return None

    def execute_claim_atomic(
        self,
        claim_id: str,
        snapshot: FrozenEnvelope,
        *,
        attempt_id: str,
        simulate: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        from agent_control_plane.local_authority_effect import (
            LocalExecutionClaim,
            TenantLocalExecutionClaim,
        )

        with self._connect() as conn:
            self._transaction_stage("before_begin")
            conn.execute("BEGIN IMMEDIATE")
            self._transaction_stage("after_begin")
            now = self._trusted_now()  # trusted time is evaluated after lock acquisition
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='effective_grants_v1'"
            ).fetchone():
                # Defence in depth: an effective-grant table created after activation is not honoured here.
                conn.execute("ROLLBACK")
                raise PermissionError("authority-effect profile cannot share an effective-grant database")

            row = conn.execute(
                "SELECT * FROM execution_claims_v1 WHERE claim_id=?", (claim_id,)
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise PermissionError("execution claim unavailable")
            if row["state"] != "issued":
                conn.execute("ROLLBACK")
                raise PermissionError("execution claim is not usable")

            claim_raw = json.loads(row["claim_json"])
            claim_type = (
                TenantLocalExecutionClaim
                if row["tenant_id"] is not None
                else LocalExecutionClaim
            )
            claim = claim_type.model_validate(claim_raw)
            self._validate_claim_state(conn, claim, snapshot, now)
            # Same write transaction: no authority writer can interleave here.
            grant_row = dict(conn.execute(
                "SELECT * FROM authority_grants_v1 WHERE grant_id=?", (claim.grant_id,)
            ).fetchone())
            from agent_control_plane.bounded import commitment
            receipt = {
                "attempt_id": attempt_id,
                "effect_id": snapshot.effect_id,
                "decision_id": snapshot.decision_id,
                "claim_id": claim_id,
                "grant_id": claim.grant_id,
                "grant_revision": grant_row["revision"],
                "grant_status": grant_row["status"],
                "grant_state_commitment": commitment(grant_row),
                "status_version_source": "local_authority_row_commitment",
                "checked_at": now.isoformat(),
                "profile": PROFILE,
            }

            budget = conn.execute(
                "SELECT * FROM effect_budgets_v1 WHERE budget_id=?", (claim.budget_id,)
            ).fetchone()
            if budget is None:
                conn.execute("ROLLBACK")
                raise PermissionError("authoritative effect budget unavailable")
            if (
                int(budget["max_effects"]) != int(claim.max_effects)
                or int(budget["used_effects"]) >= int(budget["max_effects"])
            ):
                conn.execute("ROLLBACK")
                raise PermissionError("authoritative effect budget exhausted")

            if operation_id is not None:
                # Opt-in (operation-identity/0.1). The base destination refuses an operation id it cannot store.
                self._operation_guard(conn, snapshot, operation_id)

            if conn.execute(
                "SELECT 1 FROM attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone():
                conn.execute("ROLLBACK")
                raise PermissionError("attempt identity already exists")

            conn.execute(
                "INSERT INTO attempts(attempt_id,effect_id,decision_id,operation_digest,created_at) VALUES(?,?,?,?,?)",
                (
                    attempt_id,
                    snapshot.effect_id,
                    snapshot.decision_id,
                    snapshot.operation.digest,
                    now.timestamp(),
                ),
            )
            conn.execute(
                "INSERT INTO attempt_events(attempt_id,status,error,created_at) VALUES(?,?,?,?)",
                (attempt_id, "attempted", None, now.timestamp()),
            )

            state = "partial" if simulate == "partial" else "applied"
            conn.execute(
                """UPDATE execution_claims_v1
                SET state='consumed', effect_operation_digest=?
                WHERE claim_id=?""",
                (snapshot.operation.digest, claim_id),
            )
            conn.execute(
                "UPDATE effect_budgets_v1 SET used_effects=used_effects+1 WHERE budget_id=?",
                (claim.budget_id,),
            )
            conn.execute(
                """INSERT INTO effects
                (effect_id,operation_digest,grant_id,target,amount,unit,payload_json,state)
                VALUES(?,?,?,?,?,?,?,?)""",
                (
                    snapshot.effect_id,
                    snapshot.operation.digest,
                    snapshot.operation.grant_id,
                    snapshot.operation.target,
                    float(snapshot.operation.amount),
                    snapshot.operation.unit,
                    snapshot.operation.payload_json,
                    state,
                ),
            )
            conn.execute(
                "INSERT INTO attempt_events(attempt_id,status,error,created_at) VALUES(?,?,?,?)",
                (attempt_id, "partial" if state == "partial" else "executed", None, now.timestamp()),
            )
            conn.execute(
                "INSERT INTO authority_effect_receipts_v1 VALUES(?,?,?,?)",
                (attempt_id, snapshot.effect_id, claim_id,
                 json.dumps(receipt, sort_keys=True, separators=(",", ":"))),
            )
            if operation_id is not None:
                self._operation_record(conn, snapshot, operation_id)
            self._transaction_stage("after_effect_insert_before_commit")
            conn.execute("COMMIT")
            try:
                self._transaction_stage("after_commit")
            except Exception as exc:
                # The commit is durable; a failing hook must not read as a pre-dispatch refusal.
                raise PostCommitObservationUnavailable(
                    f"post-commit stage failed after atomic commit: {type(exc).__name__}"
                ) from exc

        try:
            observation = self.observe_bound(snapshot)
        except Exception as exc:
            # The effect is durably committed and the claim consumed. The caller must
            # not see this as a pre-dispatch refusal (issue #96 / F-08).
            raise PostCommitObservationUnavailable(
                f"post-commit observation unavailable after atomic commit: {type(exc).__name__}"
            ) from exc
        if simulate == "lost_ack":
            raise TimeoutError("synthetic acknowledgement unavailable after atomic commit")
        return {
            "claim_id": claim_id,
            "attempt_id": attempt_id,
            "newly_executed": True,
            "observation": observation,
            "linearized_at": now.isoformat(),
            "authority_receipt": copy.deepcopy(receipt),
        }

    def authority_receipt(self, attempt_id: str) -> dict[str, Any] | None:
        """Read a retained local commit receipt; absence does not prove non-effect."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT receipt_json FROM authority_effect_receipts_v1 WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
        return json.loads(row["receipt_json"]) if row else None

    def reconcile_claim(self, claim_id: str, snapshot: FrozenEnvelope) -> dict[str, Any]:
        from engine.local_authority_effect_recovery import reconcile_claim_exact
        return reconcile_claim_exact(self, claim_id, snapshot)


    def claim_state(self, claim_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state FROM execution_claims_v1 WHERE claim_id=?", (claim_id,)
            ).fetchone()
        return row["state"] if row else None

    def budget_state(self, budget_id: str) -> dict[str, int] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT max_effects,used_effects FROM effect_budgets_v1 WHERE budget_id=?",
                (budget_id,),
            ).fetchone()
        return dict(row) if row else None


from engine.atomic_local_control_plane_executor import AtomicLocalControlPlaneExecutor
