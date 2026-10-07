# Worker 21 - atomic local authority/effect implementation checkpoint

Starting main: `31cd5dc5bc5cc4bf8d3c62e69737ec7a74e1f28d`.

Control Plane dependency for this hardened proposal: Worker 21 PR #12 exact head
`6c7b49138134eeb0d6e37e1b99a36a49cc42218e`.

Moltbot Safe PR #14 was inspected at
`d21f2cd9416e48398ba2ea683e930698e0076f29` and is not consumed.

## Implementation choice

The Control Plane now performs an explicit trusted handoff: the authority source
must exclude its invalidating writers while final resolution, coherent snapshot
validation and destination provisioning occur. Once provisioning commits, one
opted-in local SQLite database is authoritative for mutable authorization
projection, claims, budgets and protected effects.

Provisioning preserves source statuses and rejects non-active grant, approval or
policy projections plus non-current decision-critical evidence. It never
normalizes those states to active.

All later invalidating writer APIs use the same SQLite write-transaction
ordering. Reconciliation requires exact claim/effect identity and the
transaction-retained destination operation digest before reporting applied or
partial. The legacy executor remains available only on non-profile databases.

## Focused qualification

The focused suite covers grant/approval/policy/evidence invalidation, issuance
interleaving after successful final resolution, rejection of non-active projected
statuses, exact reconciliation binding, grant and evidence expiry, unchanged
authority, operation substitution, unavailable state, legacy-path rejection,
lost acknowledgement/restart, same-claim concurrent processes, shared-budget
competing processes, process termination before/during/after transaction, and a
deterministic expiry-while-waiting case that signals initialization and the
pre-BEGIN lock attempt before advancing the trusted clock.

Command:

```bash
PYTHONPATH=".:pinned/control-plane/src" pytest -q tests/test_local_authority_effect.py
```

Final PR-head CI is authoritative. No passing result is asserted until observed.

## Integration debt

PR #14's refund-intent ownership and this authority/effect profile overlap only
at destination commit gating. They must be composed after independent acceptance;
held intent claims must not be released, replaced or migrated by this work.

The hub must qualify exact accepted upstream revisions before any pin advance.


## Worker 20 coordination

Worker 20 PR #11 is merged at
`29337fe900d3b2da5656c77d56d70f18feb190b8`. Worker 21 does not
silently consume it as runtime authority. Its record remains non-authorizing and
is referenced only through optional opaque linkage fields pending a separately
reviewed integration.
