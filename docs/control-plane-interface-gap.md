# Proposed Control Plane interface extension

This repository does not modify the Agent Control Plane. The following is a Governor-review proposal derived from the pinned integration at `283500652d47a692fb0b99a1172a6d5faffbd9a7`.

## Gap

The Control Plane checks institution and authority domain while resolving the Alvorada Authority Context, but its persisted `AuthorizationBinding` does not carry `institution_id` or `authority_domain`.

A downstream executor can therefore verify actor, principal, manifest/proposal commitments, action, adapter, target, payload commitment, grant, requirement and limits against the issued decision, but cannot independently prove from that decision object which institution/domain was bound upstream.

## Proposed additive fields

Add to `AuthorizationBinding` in a backward-compatible schema revision:

```text
institution_id: str
authority_domain: str
```

Populate both from the already validated Authority Context and include them in the persisted decision binding. Downstream executors should compare them exactly before effect.

No new authority semantics are proposed. These fields expose an already-checked binding to downstream enforcement.

## Optional executor-facing export

A future executor-facing record could also package the authoritative `RuntimeDecision` plus the frozen `RuntimeProposal` or an equivalent canonical operation record, allowing the executor to recompute the proposal commitment from one canonical operation representation.

If introduced, it should be versioned and should not treat copied JSON as authenticated transport.
