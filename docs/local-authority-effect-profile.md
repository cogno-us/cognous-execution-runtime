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
