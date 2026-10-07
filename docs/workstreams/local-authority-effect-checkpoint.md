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


## Hardened validation checkpoint

At hardened head `1e84d01c3861a94f6d512a651e95b3606ffefe66`:

- Worker21 focused run `37640305700`: **19 passed**, zero failures/skips.
- Existing Python safety boundary run `37640305792`: **223 passed, 21 skipped**.
  The skips are profile tests under the intentionally older accepted Control
  Plane used by that legacy workflow, not reclassified passes.
- Workflow Sanity and the OpenShell worker-image qualification also completed
  successfully at this head.

The focused suite retains the earlier successful transaction, separate-process
concurrency, shared-budget and crash-boundary tests while adding governor
regressions for non-active projections, exact reconciliation binding and
deterministic expiry while waiting for the SQLite transaction boundary.

This result precedes this evidence-only checkpoint commit. Final PR-head
validation is recorded separately in the PR handoff.


## Recovery-envelope substitution hardening

Recovery now consumes the supplied frozen execution envelope rather than only
`claim_id` plus `effect_id`. Before `applied` or `partial` can be
reported, it verifies:

- supplied decision ID equals the retained claim decision ID;
- supplied effect ID equals the retained claim effect ID;
- the canonical supplied operation commitment equals the claim's committed
  operation;
- the supplied frozen operation digest equals the digest retained
  transactionally when the claim was consumed; and
- the durable destination effect row carries that same retained digest.

A mismatch returns `hold` and the executor exposes `observed_state=unknown`
with an explicit reason. Historical execution is not attributed to the caller's
substituted operation.

Focused regressions cover changed decision ID, target, amount and payload while
retaining the original effect ID, plus successful recovery of the exact original
envelope. Earlier claim/effect and retained-digest corruption tests remain.

## Governor current-main compatibility repair

Integrated executor main `e0c127178247fbe33ec2c80997c464575738be1e`,
including accepted refund-intent PR #14 and repository-location changes.
The transaction-entry conflict preserves the authority/effect marker rejection
and the existing `_check_commit_profile()` ownership check.

Both profile activation paths now reject the other profile under the SQLite
write transaction. The profiles remain mutually exclusive per database; no
combined guarantee or automatic migration is introduced. Four regression cases
cover both activation orders with empty stores and retained claims, asserting
all durable rows remain unchanged after rejection and same-profile reopening.

Local bounded validation against proposed Control Plane
`73e3c65acc47dc43593dcb0420d14032ed410b14`:
- Compatibility + refund-intent batch: 37 passed (includes 4 new cases).
- Authority/effect batch: 24 passed.
- Remaining Python safety batch: 223 passed, 2 live OpenShell skips.

The first compatibility command lacked the Control Plane fixture environment
and reported 31 passed / 6 skipped; the configured rerun above executed all 37.
The initial Python environment lacked pytest; no test pass was claimed until
an isolated environment was installed. Full JavaScript/platform CI remains a
separate gate; these Python results do not substitute for it.
