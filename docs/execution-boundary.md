# Supported constrained execution boundary

Status: synthetic local pilot. Not a production payment, identity, authority or distributed execution system.

## Runtime selection

The supported Moltbot Safe boundary is the Python path under `engine/`.

The upstream TypeScript Moltbot application is outside this reviewed boundary; it is quarantined at tag `upstream-moltbot-quarantine-20261016` and is not on `main` (see [quarantine record](quarantine-upstream-moltbot-20261016.md)). The legacy Python `AgentEngine` is compatibility-only and performs no side effect.

## Supported authorization-to-effect path

Execution is supported only through the pinned Control Plane's `BoundedAuthorizationWorkflow.execute()` at commit `2ea9528eeb87e14ff10f05de06473122b9df540f`.

A historical persisted `RuntimeDecision(result="authorized")` is insufficient by itself. Immediately before effect, the pinned workflow re-resolves and rechecks its authorization-critical inputs. Moltbot Safe is invoked only as the bounded destination adapter after those checks pass.

The resulting path is:

1. deep-snapshot the complete Moltbot Safe execution envelope;
2. bind the snapshot to the actual pinned `RuntimeProposal` and persisted decision identity;
3. derive institution/domain from the trusted resolver's Authority Context;
4. invoke `BoundedAuthorizationWorkflow.execute()`;
5. allow that workflow to revalidate current grant, identity/delegation, mandate, approvals, policy, conflict and required evidence state;
6. only then invoke the Moltbot Safe destination adapter;
7. apply stricter local restrictions and commit the exact frozen operation to SQLite;
8. observe destination state separately from the submission acknowledgement.

This is still an in-process integration. It does not provide authenticated network transport.

## Execution envelope 0.2.0

The request carries:

- decision ID and stable effect ID;
- optional requested attempt ID;
- actor and principal;
- institution ID and authority domain;
- manifest ID/version/digest and proposal commitment;
- action and adapter IDs;
- target;
- exact payload plus payload commitment;
- requested permissions;
- amount and unit;
- requested effect count and effective maximum effect count;
- Authority Context reference and requirement ID;
- grant ID and revision.

The complete request is deep-copied and serialized at API entry. Later mutation of the caller's nested payload cannot alter what is validated or committed.

For the single-refund adapter, `effects` must be exactly integer `1`. Boolean, zero, negative, fractional and larger values are rejected. Amount must be a finite non-negative integer or float and cannot be boolean.

## Institution/domain binding

The pinned Control Plane `AuthorizationBinding` does not expose `institution_id` or `authority_domain`.

For this pilot, both are derived from the Authority Context returned by the same trusted resolver used by the revalidating workflow. Moltbot Safe compares those trusted values exactly to the frozen execution operation. Caller labels cannot satisfy the boundary on their own.

This is a bounded integration workaround, not a silent upstream schema fork. See [control-plane-interface-gap.md](control-plane-interface-gap.md).

## Attempts, effect identity and observation

The SQLite destination separates:

- immutable attempt identity;
- append-only attempt status events;
- durable effect rows;
- authoritative local destination observation.

Attempt IDs are never updated with `INSERT OR REPLACE`. A reused ID is rejected; if the request must be recorded, it receives a fresh denied attempt identity so the prior history remains intact.

Effect IDs are bound to a canonical digest of the frozen operation. Reusing the effect ID for different content fails. Repeated delivery of the same effect/content is not re-applied and reports `newly_executed=false`.

Historical observation checks the expected operation digest. It may report what is already present but does not renew or create permission to execute.

Lost acknowledgement after durable commit remains an `unknown` attempt outcome until observation establishes destination state. When the follow-up validated observation establishes the state, the result is `reconciled` (applied) or `partial` and the attempt's terminal event says the same; the result is not newly executed and not acknowledged. When the observation fails, the result stays `unknown` with observation null and `observed_state` `unknown`. When it is a validated `absent` or `unknown` observation, the result stays `unknown` and carries that observation, with `observed_state` equal to the observation's state (never `failed`, never retry permission; `absent` right after an ambiguous dispatch does not prove the effect will not appear). The same holds for a validated `unknown` observation found before dispatch and for the Control Plane adapter. Whenever `observation` is null, `observed_state` is `unknown`, for every status (denied and failed included); `observation` is never `{}`. Partial delivery remains partial and is not blindly retried.

## Route names (A, B, C)

Cross-component qualification and the execution-attempt lifecycle contract refer to the runtime's execution paths as routes A, B and C. The letters are qualification vocabulary; the runtime itself defines only the class and module names below.

| Route | Runtime name | Module | What it is |
|---|---|---|---|
| A | `PinnedControlPlaneExecutor` | `engine/control_plane_adapter.py` | Supported path: the pinned Control Plane workflow revalidates current authority, then invokes the local destination. The low-level `LocalDestinationExecutor` and `DurableRefundDestination` it drives live in `engine/safe_executor.py`. |
| B | `AtomicLocalControlPlaneExecutor` | `engine/atomic_local_control_plane_executor.py` | Atomic claim route: a provisioned local claim is consumed inside the destination's own transaction. |
| C | the Control Plane adapter (`ControlPlaneRefundDestinationAdapter` and the `control_plane_attempts` / resolution-journal records written by the Control Plane) | `engine/control_plane_adapter.py` | Qualification's term for the Control Plane side of the adapter path. The runtime does not define a route C class and does not write `control_plane_attempts` rows. |

"Route A", "route B" and "route C" in qualification reports, the failure register and the contract fixtures (whose ids keep the `route-b` wording for stability) map to this table. Code and API documentation should use the runtime names.

## Consistency boundary

SQLite `BEGIN IMMEDIATE` serializes the local effect-content check, duplicate check, cumulative grant count and insert for processes sharing the same database file.

The regression suite exercises separate OS processes sharing that file.

This is not a distributed transaction, shared remote budget service or exactly-once guarantee for an external destination.

## Filesystem, process, network and credentials

Implemented:

- no subprocess execution in the supported destination path;
- no destination network calls;
- no production credentials;
- dedicated operator-selected SQLite state root;
- lexical path containment;
- existing symlink-component checks before path resolution;
- rejection of symlinked database paths;
- strict local institution/domain/adapter/action/target/unit/amount/effect restrictions.

These controls do not constitute host confinement. There remains a filesystem check/use race without OS facilities such as directory file descriptors/openat-style confinement or an external sandbox. A different host process with sufficient permissions can bypass this Python module.

No claim is made for whole-upstream Moltbot bypass resistance, container isolation, seccomp/AppArmor, network namespace isolation or production credential confinement.


## Migration from PR #4 envelope 0.1.0

The revised branch upgrades the envelope to 0.2.0 and replaces the first pilot attempt table with immutable attempt identities plus append-only attempt events.

When an existing pilot SQLite file contains the earlier `attempts(status,error)` schema, Moltbot Safe migrates those rows on open. Each historical row is preserved with operation digest `legacy:unknown` and its prior outcome is retained as an attempt event. The migration does not infer missing authorization or operation content from those legacy rows.

No production-state migration is claimed because PR #4 has not been merged or deployed as a production interface.

## Optional local effective grant precondition

`engine.effective_grant.EffectiveGrantDestination` activates
`sqlite-effective-grant/0.1` in a synthetic destination database. A trusted
`set_effective_grant` writer serializes principal, grant revision, semantic
permissions and status with effect commit. The Control Plane's opt-in
runtime-authority/0.1 profile supplies an approval-bound expected digest and
`effective-grant/0.1` derivation. The destination compares them under the actual
`BEGIN IMMEDIATE` effect transaction. Widening, narrowing, revocation, wrong
principal, missing state and unknown derivation refuse before inserting an effect.
Reopening the database through the base destination does not bypass the table.

The stored row also retains its sorted permission set (`permissions_json`). Under
the same transaction the request's `requested_permissions` must be a subset of
it, so a caller that presents the stored row's own digest cannot commit beyond a
narrowed grant (`effective_grant_permissions_insufficient`). A row written
before this check existed has no retained set and refuses with
`effective_grant_permissions_unavailable` until a trusted writer calls
`set_effective_grant` for that grant again; the column itself is added to
existing databases on open.

This is same-database synthetic enforcement. It does not make remote authority,
purpose or policy stores atomic. Host/database access must separate authority
writers from untrusted agents; possession of the SQLite file can bypass that
separation. No new OS isolation or production key custody is claimed. This
profile is independent of the existing atomic claim and refund-intent profiles;
unsupported combinations refuse (see the composition limits below). Historical selected pins and release permissions
are unchanged. Source provenance: supplied Moltbook 101526_A K3, 15 October 2026;
tests and code are original synthetic implementations under existing licenses.

### Known composition limits of the effective-grant destination

Recorded from V1 validation (issue #99). Neither is a change to supported behaviour.

- **Refund-intent profile.** `PinnedControlPlaneExecutor.execute(refund_intent=...)`
  wraps the destination in `IntentRefundDestination`, which does not carry the
  effective-grant capability or commit precondition. Against an
  `EffectiveGrantDestination` the request is refused with no effect
  (`atomic_authority_capability_unavailable`), so the opt-in intent profile
  cannot be combined with route A on this destination. This fails closed and is
  deliberately left as is: intent ownership is consumed before the grant check
  runs, so a supported combination needs its own design (what a grant refusal
  after dispatch ownership means) and requalification.
- **Atomic claim route.** The atomic authority/effect profile and the effective-grant
  profile are mutually exclusive on one database. The atomic route does not read
  effective grants, so combining them would let a revoked effective grant execute.
  Opening either profile on a database that already carries the other is refused
  (`PermissionError`), at activation in both orders, and again inside the claim
  transaction if the other table appears later. Use separate databases.
- **Generation fence (closed).** The governed-exchange component now forwards
  `expected_effective_grant_digest` and `effective_grant_derivation_version` through
  its fence wrapper (exchange 06f58c3), so `run_exchange(generation_fence=...)` works
  on the effective-grant destination. No limit remains here.
- **Tenant scope.** Effective grants and refund intent are keyed without a tenant.
  A grant id must be globally unique within one database and belong to one scope and
  one principal: `set_effective_grant` refuses to bind an existing grant id to a
  different principal, and the refund-intent profile refuses tenant-scoped operations
  (`op.tenant_id` set). Tenant-scoped keys are deferred to the first contract freeze.
- **Evidence rows are keyed by obligation id alone.** On the atomic authority/effect
  destination, `authority_evidence_v1` has no tenant column and the obligation id is
  supplied by the caller (a static string in the requirement). Two tenants that share
  one engine database and one obligation id share one evidence row: an identical
  evidence tuple from a second tenant is reused, a differing tuple is refused at
  provisioning, and a refresh, stale mark or removal of the row applies to every tenant
  that names the obligation id. The claim does not carry a tenant for evidence items.
  Deployment rule: use one engine database per tenant, or tenant-unique obligation ids.
  Keying evidence by tenant is deferred to the first contract freeze;
  `tests/test_evidence_tenant_known_limit.py` pins the current behaviour so a change is
  deliberate.
- **Amounts.** Amounts are exact: integers must be plain `int` with absolute value at
  most 2**53, floats must be finite and non-negative, and comparisons between claim
  and operation amounts are exact, not binary64 conversions.
- **Producer export.** `export_execution_artifacts` refuses results whose status,
  `attempted`, `acknowledged`, `newly_executed` and `observed_state` contradict each
  other (for example `executed` with `attempted=false`). The export stays
  source-asserted; it does not independently establish what the destination did.
- **Legacy audit.** `AuditLog.agent_id` is caller-asserted, not authenticated. An
  entry that cannot be serialised is written as a minimal record instead of being
  skipped.
