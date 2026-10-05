# Proposed Control Plane executor-binding extension

This repository does not modify the Agent Control Plane. This is a Governor-review proposal based on pinned commit `283500652d47a692fb0b99a1172a6d5faffbd9a7`.

## Current integration

Moltbot Safe now executes only through `BoundedAuthorizationWorkflow.execute()`, so the pinned Control Plane performs its current authorization revalidation immediately before the destination adapter is invoked.

For institution/domain, the executor currently derives both values from the trusted Authority Context resolver used by that same workflow and compares them against the frozen Moltbot Safe request.

That keeps caller-supplied labels from creating authority, but the values still are not present in the persisted `AuthorizationBinding`.

## Required additive upstream fields

Add these fields to `AuthorizationBinding` in a backward-compatible schema revision:

```text
institution_id: str
authority_domain: str
```

Populate them from the already validated Authority Context before the decision is persisted. They should participate in equality/revalidation of the binding and be exposed to downstream executors.

## Preferred executor-facing contract

A future version should export a versioned, immutable executor-facing binding that contains, or unambiguously commits to:

- decision ID and stable effect ID;
- actor and principal;
- institution ID and authority domain;
- manifest ID/version/digest;
- complete proposal commitment;
- action and adapter IDs;
- target;
- exact payload commitment;
- requested permissions;
- amount/unit/effect count;
- Authority Context and requirement IDs;
- grant ID/revision;
- effective limits;
- applicable policy versions and other revalidation-critical binding fields already held by the Control Plane.

The executor-facing object must remain a record of what was authorized, not a transport-authentication mechanism. Execution still requires the Control Plane's current revalidation or an equivalent trusted revalidation interface.

## Explicit non-proposal

Moltbot Safe does **not** propose treating a copied decision document, matching digest, caller-supplied institution, or historical `authorized` state as sufficient authority for effect.
