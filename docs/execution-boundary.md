# Supported constrained execution boundary

Status: synthetic local pilot. Not a production payment, identity, authority or distributed execution system.

## Runtime selection

The supported Moltbot Safe boundary is the Python path under `engine/`.

The retained TypeScript Moltbot application is upstream code outside this reviewed boundary. The legacy Python `AgentEngine` is compatibility-only and performs no side effect.

## Supported authorization-to-effect path

Execution is supported only through the pinned Control Plane's `BoundedAuthorizationWorkflow.execute()` at commit `283500652d47a692fb0b99a1172a6d5faffbd9a7`.

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

When an existing pilot SQLite file contains the earlier `attempts(status,error)` schema, Moltbot Safe migrates those rows on open. Each historical row is preserved with operation digest `legacy:unknown` and its prior outcome is retained as an attempt event. The migration does not infer missing authorization or operation content from those legacy rows.

No production-state migration is claimed because PR #4 has not been merged or deployed as a production interface.
