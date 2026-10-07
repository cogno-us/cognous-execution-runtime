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
- The initial local Node bootstrap used the environment's pnpm 11 fallback and
  rejected existing git-based libsignal. Installing the repository-pinned pnpm
  10.23.0 completed the frozen dependency install without dependency or policy
  changes. Full Node lint passes (2,516 files, zero warnings/errors).
- Linux and Windows CI timed out in the workspace-path test's first invocation,
  which loaded unrelated plugins; timeout cleanup then disrupted the next test's
  working directory. The suite now imports the existing `fast-coding-tools`
  isolation helper used by neighboring suites. All six real filesystem/exec
  assertions remain, with unchanged timeouts. Focused local validation passes all
  six tests (69 ms test time); final-head cross-platform CI remains required.

## Remaining acceptance

Review final-head upstream CI and evidence. Qualify/export this profile separately
in the hub before selecting any new component revision. Preserve the existing
legacy equivalent-intent duplicate characterization. Real institutional identity
issuance, distributed reservation, safe release/cancellation, live OpenShell intent
propagation and Replay schema adoption are not established by this batch.

## CI batching and smoke follow-up

The Node workspace-path suite passed after isolation; the next Linux run failed
only the CLI command-routing smoke test during unrelated plugin initialization.
That suite now stubs plugin loading while asserting that routing requests it.
Both affected suites pass locally: 19 tests. Full Node lint and focused formatting
pass. No test timeout was increased and no assertions were removed.

CI now runs one PR campaign rather than duplicate branch-push and PR campaigns.
Platform batches run in order: install, Linux, Windows, Android, macOS JavaScript,
macOS app. Each matrix has at most two concurrent jobs. Failed prerequisite batches
block later batches; blocked jobs are not passes. All existing test commands remain.

The installer smoke checks the external published upstream installer, not this
PR's Python package. It invokes the existing shell harness directly; host pnpm
bootstrap and dependency installation were unused by that harness and are removed.
The job has a 15-minute limit, its harness step 12 minutes, installer download 60
seconds, installer execution 5 minutes, and npm/version/help probes 30 seconds.
The installer is downloaded completely before execution. Existing version and CLI
assertions remain. Local shell fixtures exercised success, failed download (28),
and installer timeout (124), with no success reported in the failure cases. These
fixtures do not establish that the live external installer works. Final-head CI
and live installer results remain pending.

## Four-shard CI follow-up

The completed `d21f2cd` campaign passed Python safety and OpenShell image
qualification, but failed Node safe-bins and Bun tools-invoke HTTP tests at their
120-second limits. External installer smoke exited 124 during upstream build-tool
installation. Windows, Android and macOS were skipped behind the failed Linux gate.
These outcomes remain failures/skips, not passes.

The new campaign replaces each monolithic JavaScript test job with four Vitest
shards on Linux Node, Linux Bun, Windows Node and macOS Node. Native Vitest test
file discovery confirms the default configuration covers exactly the union of the
previous unit, extension and gateway configurations: 899 files. The installed
Vitest sequencer partitions them into 225, 225, 225 and 224 files without overlap
or omission. Existing test exclusions are retained; no new test exclusion is added.
Each matrix retains fail-fast=false and max-parallel=2. Linux/Windows command steps
are capped at 12 minutes, macOS commands at 20, and jobs at 30. Downstream platforms
continue after a failed preceding platform batch unless cancelled; the failed jobs
still fail the workflow. This supersedes the earlier fail-blocking platform order.

The safe-bins test now uses the existing fast-coding-tools helper to isolate
unrelated plugin/image/web initialization while retaining its real exec allowlist
assertions. Before isolation it timed out locally at a diagnostic 15-second limit;
after isolation it passes (one test, 26 ms). Repository lint passes with zero
warnings/errors. The local gateway test cannot initialize because this execution
environment rejects os.networkInterfaces(); its nine skipped tests are not a pass.
The gateway test remains unchanged for diagnosis in its CI shard.

Installer smoke remains a separate bounded workflow with its previous failure
preserved. This change does not fix or suppress the external installer timeout.
Final-head CI must establish the new batch results before acceptance.
