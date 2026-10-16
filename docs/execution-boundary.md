# Supported constrained execution boundary

Status: synthetic local pilot. Not a production payment, identity, authority or distributed execution system.

## Runtime selection

The supported Execution Runtime boundary is the Python path under `engine/`.

The upstream TypeScript Moltbot application is outside this reviewed boundary; it is quarantined at tag `upstream-moltbot-quarantine-20261016` and is not on `main` (see [quarantine record](quarantine-upstream-moltbot-20261016.md)). The legacy Python `AgentEngine` is compatibility-only and performs no side effect.

## Supported authorization-to-effect path

Execution is supported only through the pinned Control Plane's `BoundedAuthorizationWorkflow.execute()` at commit `2ea9528eeb87e14ff10f05de06473122b9df540f`.

A historical persisted `RuntimeDecision(result="authorized")` is insufficient by itself. Immediately before effect, the pinned workflow re-resolves and rechecks its authorization-critical inputs. Execution Runtime is invoked only as the bounded destination adapter after those checks pass.

The resulting path is:

1. deep-snapshot the complete Execution Runtime execution envelope;
2. bind the snapshot to the actual pinned `RuntimeProposal` and persisted decision identity;
3. derive institution/domain from the trusted resolver's Authority Context;
4. invoke `BoundedAuthorizationWorkflow.execute()`;
5. allow that workflow to revalidate current grant, identity/delegation, mandate, approvals, policy, conflict and required evidence state;
6. only then invoke the Execution Runtime destination adapter;
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

For this pilot, both are derived from the Authority Context returned by the same trusted resolver used by the revalidating workflow. Execution Runtime compares those trusted values exactly to the frozen execution operation. Caller labels cannot satisfy the boundary on their own.

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

Lost acknowledgement after durable commit remains an `unknown` attempt outcome until observation establishes destination state. Partial delivery remains partial and is not blindly retried.

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

When an existing pilot SQLite file contains the earlier `attempts(status,error)` schema, Execution Runtime migrates those rows on open. Each historical row is preserved with operation digest `legacy:unknown` and its prior outcome is retained as an attempt event. The migration does not infer missing authorization or operation content from those legacy rows.

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

This is same-database synthetic enforcement. It does not make remote authority,
purpose or policy stores atomic. Host/database access must separate authority
writers from untrusted agents; possession of the SQLite file can bypass that
separation. No new OS isolation or production key custody is claimed. This
profile is independent of the existing atomic claim and refund-intent profiles;
unsupported combinations refuse. Historical selected pins and release permissions
are unchanged. Source provenance: supplied Moltbook 101526_A K3, 15 October 2026;
tests and code are original synthetic implementations under existing licenses.
