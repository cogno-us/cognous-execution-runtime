# Refund intent registry checkpoint

## Scope

Starting executor main: `12b9c55637e2472a1a5ce3036c787e9427c6abd8` (includes
Worker 17's merged work). Hub remains pinned to its accepted executor revision.
This batch adds an unused, opt-in storage primitive and tests only. It does not
claim that the execution path prevents logical-intent duplicates.

`engine/refund_intent.py` provisions immutable synthetic domain requests, scoped
by institution, domain, customer, effect class and request ID. Business binding
includes payload, target, adapter, amount, unit and effect count. Authentication
of a real institutional domain service remains outside this synthetic profile.
Only trusted host code may provision or access the database.

Atomic SQLite transactions reserve one original effect and its full operation
digest. A separate durable transition consumes its dispatch claim once. A new
proposal receives original linkage, not ownership. Changed authority cannot
consume the original claim; current authorization must still be established by
the Control Plane. There is no release, expiry, retry or reassignment API.

The tables coexist in the synthetic destination database, but the existing
`DurableRefundDestination.commit` API does not consult them. Therefore direct
legacy dispatch is not prevented by this batch. Integrating that enforcement and
Control Plane admission is a required next batch before adoption. Registry state
is not yet exported into Replay or evidence artifacts.

## Validation

- 21 new tests passed, including four synchronized competing processes, crash
  before reservation commit, crash after reservation, crash after dispatch claim,
  restart, changed operation, forged/wrong-scope identity, storage loss, same-effect
  ownership conflicts, and absent observation followed by a late original commit.
- Complete Python suite: 244 passed, two explicitly skipped live OpenShell tests
  because a live gateway and qualified worker image were not configured.
- Local integration used Control Plane `248d899634d9db3518e831bc7ab568a48733f825`
  and Manifest `46c950bed37fe3812000895430bc0312d29e37ce`. Existing CI also exercises
  the repository's older supported Control Plane pin; its result is separate.
- Repository-wide `pnpm lint`/`pnpm test` were attempted. The runtime wrapper
  began bootstrapping missing dependencies; both commands failed during setup
  with `ERR_PNPM_EXOTIC_SUBDEP` for the existing git-based libsignal dependency.
  The supply-chain policy was not weakened. Neither Node gate completed. No Node source or dependency files were changed.

## Next acceptance gate

Wire trusted request validation into current-authority admission and enforce the
original reservation at every supported destination dispatch path. Test actual
replans with independent approvals, revocation, partial effects and interrupted
originals. Retain `retry_eligible=false`; an absence observation does not prove
that an original can no longer commit. Qualify this new profile separately from
the existing equivalent-intent duplicate characterization. Do not advance hub
pins until upstream integration and evidence review are complete.
