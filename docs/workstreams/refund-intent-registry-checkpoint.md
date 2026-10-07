# Refund intent registry checkpoint

## Scope and starting point

Starting executor main: `12b9c55637e2472a1a5ce3036c787e9427c6abd8`, preserving
Worker 17's merged work. Hub selected dependencies remain unchanged. PR #14 adds
an opt-in synthetic refund profile, not production domain authentication or
protection from hostile host code and database administrators.

## Implemented boundary

Trusted host provisioning records a domain-issued request ID scoped by institution,
authority domain, customer and refund effect class. The immutable business binding
includes action, adapter, target, amount, unit, payload and effect count. Supported
actions are the local synthetic refund fixture and Manifest routine refund v1.
High-consequence refund actions require a separately designed profile.

Creating `RefundIntentRegistry(destination)` opts an empty destination database
into the profile. Initialization and the empty-state check are serialized against
writers. Existing effects cannot be backfilled by guessing intent from payload
similarity; incomplete profile schemas fail closed. Provisioning must never be
exposed to agents. The same trusted domain request must retain the same ID across
replans; arbitrary new trusted IDs are not semantically deduplicated.

`PinnedControlPlaneExecutor.execute(..., refund_intent=intent)` binds a per-call
`IntentRefundDestination` without mutating shared workflow state. Existing proposal,
approval, current-authority and execution-policy checks remain in place. Only the
Control Plane's authorized apply path reaches reservation and dispatch consumption.

Atomic SQLite transactions durably reserve the original effect ID and full operation
digest, then consume its one-time dispatch claim before committing an effect. These
are separate local transactions: a crash in between can leave a permanently held
original, not an automatically retried request. The effect transaction rechecks
ownership. A fresh proposal or approval cannot replace the owner. There is no
release, expiry, reassignment or retry API. A process already past dispatch claim
may still commit after an absence observation; new requests remain held.

The legacy destination write path now rejects databases with an intent registry.
Databases that have not enabled the profile keep their prior behavior. Direct SQL,
subclass overrides by hostile host code, alternate database copies and other
production destinations are outside this bounded trusted-host claim.

`export_intent` produces read-only sidecar evidence with original effect/digest,
business commitment, dispatch state and `retry_eligible=false`. It explicitly says
that the registry does not establish authority. It is not a new Replay producer
profile, and existing envelope/profile versions remain unchanged.

## Validation

- 33 profile tests: scoped IDs and immutable bindings; four synchronized competing
  processes for reservation and actual destination effects; crash before/after
  reservation and after dispatch claim; restart; missing storage/schema; read-only
  evidence; refusal of inferred migration; legacy write rejection.
- Actual pinned Control Plane tests use a multi-use grant and separately current
  bound approvals. Same-intent replan produces one total effect; distinct trusted
  requests with identical business details produce two. Revocation and omitted
  intent produce no effects. Partial and lost-ack cases retain one original effect
  and no retry permission. A paused original commits late while replans remain held.
- Complete local Python suite: 256 passed, two live OpenShell tests skipped because
  a gateway and qualified worker image were not configured.
- Local integration: Control Plane `248d899634d9db3518e831bc7ab568a48733f825`,
  Manifest `46c950bed37fe3812000895430bc0312d29e37ce`. Existing CI tests its own older
  supported Control Plane pin; that outcome must be checked independently.
- Repository-wide Node lint/tests previously failed during dependency bootstrap
  with `ERR_PNPM_EXOTIC_SUBDEP` on existing git-based libsignal. No dependency or
  supply-chain policy was changed. No Node gate pass is claimed.

## Remaining acceptance

Review final-head upstream CI and evidence. Qualify/export this profile separately
in the hub before selecting any new component revision. Preserve the existing
legacy equivalent-intent duplicate characterization. Real institutional identity
issuance, distributed reservation, safe release/cancellation, live OpenShell intent
propagation and Replay schema adoption are not established by this batch.
