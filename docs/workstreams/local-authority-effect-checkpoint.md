# Worker 21 - atomic local authority/effect implementation checkpoint

Starting main: `31cd5dc5bc5cc4bf8d3c62e69737ec7a74e1f28d`.

Control Plane dependency for this proposal: Worker 21 PR #12 exact head
`b670dd471eb7793ff796fd627d3c60628ed679f6`.

Moltbot Safe PR #14 was inspected at
`d21f2cd9416e48398ba2ea683e930698e0076f29` and is not consumed.

## Implementation choice

One opted-in local SQLite database is authoritative for mutable authorization
projection, claims, budgets and protected effects. All invalidating writer APIs
use the same SQLite write-transaction ordering. The legacy executor remains
available only on non-profile databases.

## Focused qualification

The focused suite covers grant/approval/policy/evidence invalidation, grant and
evidence expiry, unchanged authority, operation substitution, unavailable state,
legacy-path rejection, lost acknowledgement/restart, same-claim concurrent
processes, shared-budget competing processes, process termination before/during/
after transaction, and expiry after waiting for the transaction lock.

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
