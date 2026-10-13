# Atomic local authority/effect profile

Status: **implemented proposal on Worker 21 branch; opt-in; not hub-selected**.

Profile: `urn:cognous:profiles:local-authority-effect:0.1.0-proposed`.

## Authoritative store

After the Control Plane trusted handoff completes, the profile uses one local
SQLite database as the authoritative store for:

- grant status/revision and local validity interval;
- approval state and bindings;
- policy version/status;
- decision-relevant evidence state and freshness inputs;
- exact Control Plane execution claims;
- shared cumulative effect budgets;
- protected synthetic effect rows and attempt history.

This is not a cache claim. Claim provisioning occurs while the Control Plane
authority source still holds its mutation-exclusion handoff. The handoff ends
only after the exact claim is committed into this store; from that point,
profile-relevant invalidating writers must use this database. External resolver
changes after handoff are outside the local guarantee rather than silently
overriding this authority.

Provisioning preserves the projected statuses supplied by Control Plane.
Non-active grant/approval/policy projections and non-current
decision-critical evidence are rejected; provisioning never rewrites them to
`active`.

## Transaction ordering

Execution uses `BEGIN IMMEDIATE`. After the write transaction is acquired it:

1. reads the trusted profile clock;
2. loads and verifies the issued claim;
3. checks exact operation, institution/domain, actor/principal and grant binding;
4. validates current authoritative grant, approval, policy and evidence rows;
5. checks claim/grant/evidence expiry;
6. checks the shared effect budget;
7. writes the attempt, consumes the claim, increments the budget and inserts the
   protected effect;
8. commits all of those durable changes together.

If an invalidating writer commits first, execution observes the changed row and
produces no effect. If execution commits first, later revocation changes current
authority but does not rewrite the historical effect.

## Trusted time

The clock is supplied by the trusted host profile, not by an agent request.
Execution evaluates it only after acquiring the SQLite write transaction.
Tests include expiry while execution waits for that boundary.

## Recovery

Claim consumption, budget use and effect insertion share one transaction.

- crash before transaction: no atomic profile state changes;
- crash during transaction: SQLite rollback leaves claim issued and no effect;
- crash after commit: claim remains consumed and the effect remains durable;
- lost acknowledgement or later recovery reports applied/partial only when the
  caller supplies the exact original execution envelope: decision ID and effect
  ID must equal the retained claim, the supplied canonical operation commitment
  must equal the claim's committed operation, and the supplied/destination
  operation digest must equal the digest transactionally retained when the
  claim was consumed. Substituted decision IDs, targets, amounts or payloads
  remain hold/unknown with an explicit binding reason;
- observation of absence alone does not produce retry permission.

## Legacy and intent-registry compatibility

The default legacy destination remains unchanged for non-profile databases.
Profile activation refuses prior legacy effects/attempts. Legacy writes are
blocked after opt-in.

Moltbot Safe PR #14 (`governor/refund-intent-registry`) remains unaccepted and
is not consumed. Its business-intent ownership table is independent. A future
integration must compose both profile guards and transactions deliberately; it
must not release or migrate held intent ownership implicitly.

## Worker 20 coordination

Control Plane Worker 20 PR #11 is merged at
`29337fe900d3b2da5656c77d56d70f18feb190b8`. Its Decision Input
Commitment Record remains non-authorizing. Worker 21 does not silently adopt its
semantics into this execution path; the local claim retains only optional opaque
linkage fields unless a later explicit integration is reviewed.

## Limits

Same-host SQLite only. No production identity/key custody, remote revocation,
distributed transactions, external destination enforcement, OpenShell
confinement, universal mediation, distributed exactly-once, EBL-Core conformance
or production readiness.

## Database profile exclusivity

The authority/effect profile and synthetic refund-intent profile require separate
SQLite databases. Activation rejects the other profile's marker while holding
`BEGIN IMMEDIATE`, in either activation order, including an empty opted-in
store. Rejection preserves existing ownership and execution claims. Reopening
the same profile remains supported. This is explicit separation, not combined
intent ownership plus authority/effect enforcement for one operation.

## Commit-state receipts (issue #84)

New C1 commits retain an `authority_effect_receipts_v1` row in the same SQLite
transaction as the effect, attempt, budget consumption and claim consumption.
The receipt links attempt/effect/decision/claim IDs with the grant revision,
active status, check time and a canonical commitment to the authoritative local
grant row. The executor returns this receipt in `control_plane_evidence`;
`authority_receipt(attempt_id)` retrieves it after restart or lost acknowledgement.

`grant_state_commitment` is a content-addressed local state version.
`status_version_source=local_authority_row_commitment` explicitly distinguishes it
from an upstream issuer-assigned status revision, which this profile does not
retain. The timestamp is the check time after obtaining the transaction lock,
not proof of commit ordering. SQLite transaction serialization supplies the local
order. The receipt does not add a remote sequencer, external signature or witness.

Both effect-first and revocation-first schedules are tested. A revocation waiting
behind an already locked effect transaction cannot interleave between authority
validation and effect commit. If revocation commits first, there is no effect or
commit receipt. If the effect commits first, its retained receipt names the
active revision and local state commitment even after revocation. A failure
before commit rolls back the effect and receipt together. No-claim dispatch is
refused. Existing historical effects receive no fabricated receipt on migration.

All invalidating authority writes MUST use this same database/transaction lock.
A remote authority mirrored into it has a separate ordering boundary. Default C0
is unchanged; no C2/C3, external refusal-custody or real-settlement claim follows.
