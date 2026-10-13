# OpenAPPA integration status

Status reviewed against Execution Runtime
`11d44f65b7d24ad6eda5acbb5ec30d3f8f3346c5` (PR #75 merged).
This directory contains opt-in synthetic qualification tooling and trusted-host
helpers. It is not an installed production adapter or release acceptance.

## Verified current scope

The optional final-text reply gate is implemented, with no production caller
enabling it. The latest inspected [actual reply admission run](https://github.com/cogno-us/cognous-execution-runtime/actions/runs/38333063089),
job `115086943539`, at PR #75 head
`0301fccc3dfe6c138af9fe7d3ba23eda457faf28` passed the pinned Rust build,
full TypeScript type check and **57 cases**: 44 admission/routing cases plus
13 connected OpenAPPA cases. The workflow requires exactly 57 JUnit cases with
no skips, failures or errors. The 44 admission/routing cases are included in
that total, not additional coverage to add to it.

This gate snapshots normalized final text, checks current policy before the
queued delivery callback, and requires a digest-correlated allowance. Timeout,
refusal, cancellation, unsupported output, queue overflow or delivery rejection
stops later output on that dispatcher. Accepted text is bounded to 16 KiB per
reply and 32 pending replies. Cancellation cannot undo a send already started;
a rejected delivery may have partially completed and is not retry permission.
These are callback-boundary guarantees, not authentication, final provider-wire
binding, durable outcome recording or complete sink interception.

The latest inspected conjunction run is
[38333063009](https://github.com/cogno-us/cognous-execution-runtime/actions/runs/38333063009),
job `115086943497`, at PR #75 head
`0301fccc3dfe6c138af9fe7d3ba23eda457faf28`: **96 passed in 20.61 seconds**.
The workflow also required exactly 96 JUnit cases with no skips, failures or
errors. This is evidence for that source revision and bounded fixture scope;
it does not establish that all repository or platform CI passed.

| Capability | Current implementation and qualification | Boundary |
| --- | --- | --- |
| Optional final-text admission | [Reply gate](../../src/auto-reply/reply/reply-admission.ts) and [actual policy cases](../../src/auto-reply/reply/reply-admission.openappa.test.ts) qualify bounded queued text and stop behavior | Trusted host must bind identity and destination; no production caller, provider receipt or policy-generation atomicity |
| Synthetic reply outcome feedback | [Reply probe](cognous_reply_admission_main.rs) reports explicit synthetic success to actual OpenAPPA and reopens its audit | Persisted closure and observed Ack are separate; no production outcome adapter or durable reply journal |
| Flow and institutional permission | [Conjunction harness](conjunction_harness.py) checks correlated flow allowance before native authority validation and synthetic SQLite execution | The two permissions remain independent; no comprehensive refund information-flow policy |
| Persistent trajectory | [Persistent probe](cognous_persistent_join_main.rs) reopens the existing upstream store/root and retains restrictions | Synthetic tool mapping; no rollback protection or complete lifecycle serialization |
| Local identity and resume | [Local resume](local_resume.py) uses kernel Unix peer UID and explicit host enrollment; missing/replaced state refuses resume | Linux local OS identity, trusted host/directory; no remote identity or hostile same-UID isolation |
| Result feedback | [Feedback wrapper](feedback_harness.py) reports only correlated applied success and keeps effect status separate from feedback status | Partial/unknown results are not success; unavailable feedback never retries the effect |
| Durable feedback bookkeeping | [Journal](feedback_journal.py) commits a claim before delivery and records only an exact correlated Ack | Unconfirmed rows remain unresolved; no atomic coupling to effect or remote feedback |
| Optional receipt capacity | `JournaledReporter(..., max_receipts=N)` counts existing rows and claims under one SQLite writer reservation | Host-configured row bound only; no deletion, byte quota, persisted configuration or prevention of an already-completed effect |
| Host-wide journal status | `read_journal_status` returns total, acknowledged and unconfirmed row counts from one read-only snapshot | Host-only aggregate, not a tenant endpoint, integrity audit, capacity reservation, delivery proof or retry permission; unavailable state raises |
| Receipt inspection | `read_receipt` in the journal opens existing state read-only and distinguishes absent, unconfirmed and recorded Ack | Absent is not retry permission; unavailable state is an error; no remote receipt proof |
| Crash and contention checks | [Crash cases](test_journal_crash.py) and [process contention cases](test_journal_contention.py) exercise owned child termination and six claimers | Known process checkpoints, not power failure, storage loss or production-load qualification |

The 96-case conjunction gate includes several scopes in this table. Separate
wire, embedded-runtime, trajectory and local-resume workflows remain distinct
qualification surfaces. Do not add historical test totals together or infer
that those independent workflows ran at the same head. The 57 reply cases and
96 conjunction cases qualify different boundaries and are not one integrated
end-to-end result.

## Reproduction and source selection

Use the repository's [conjunction workflow](../../.github/workflows/openappa-conjunction.yml)
for the exact dependency setup, Rust probe build, Python environment and strict
JUnit checks. Running Python cases without the actual compiled probes is not
substitute evidence for the connected cases. Other workflows are
[wire fixtures](../../.github/workflows/openappa-wire-fixtures.yml),
[embedded runtime](../../.github/workflows/openappa-embedded-runtime.yml),
[trajectory](../../.github/workflows/openappa-trajectory.yml),
[local resume](../../.github/workflows/openappa-local-resume.yml),
[optional reply admission](../../.github/workflows/reply-admission.yml), and
[actual reply admission](../../.github/workflows/openappa-reply-admission.yml).
The ordinary test suite skips connected reply cases when the compiled probe is
absent; only the dedicated actual-admission workflow enforces the full count.

| Source | Pinned revision |
| --- | --- |
| OpenAPPA | `10aa0e09e32d8c6704bdb030dc3c66140bf81915` |
| Control Plane | `2ea9528eeb87e14ff10f05de06473122b9df540f` |
| Action Manifest | `46c950bed37fe3812000895430bc0312d29e37ce` |

These are fixture pins, not an advancement of the selected hub component lock.
The [PR #75 qualification record](https://github.com/cogno-us/cognous-execution-runtime/pull/75#issuecomment-6136216337)
records both inspected runs and incomplete broader CI at merge. Earlier PR
records remain historical evidence at their own source revisions, not current
combined qualification. No record here claims all platform checks passed.

## Remaining work and order

The [host and output-admission contract proposal](../../docs/workstreams/openappa-host-contract.md)
records a bounded source inventory, candidate requirements and implementation
gates. It is a design proposal, not acceptance of a deployment or completed
output enforcement.

1. **Define the deployment host contract.** Select the supported host, identity
   boundary, enrollment/revocation owner, protected store lifecycle and concrete
   request/output path. The current Linux UID helper is one bounded option, not
   a general enterprise authentication bridge. This contract is the prerequisite
   for implementing or enabling a production adapter.
2. **Complete host-specific sink coverage.** The optional final-text callback
   gate is implemented and synthetically qualified. Inventory tool, model-output,
   streaming and external-effect paths for that host; define where current flow
   policy must be checked, including downstream formatting, chunking and direct
   provider sends. The current gate does not establish interception of every
   sink or bind the final provider representation. This design can proceed before
   the deployment environment exists; end-to-end enforcement cannot be qualified
   without that environment.
3. **Define policy and lifecycle coordination.** Resolve expiry, policy-generation
   changes, root continuity and concurrent lifecycle updates. Retain explicit
   check-to-commit limitations until an accepted synchronization contract is
   implemented and independently qualified.
4. **Connect output outcomes and unresolved-feedback operations.** The reply
   fixture can explicitly report synthetic successful delivery with ToolResult,
   inspect persisted closure and distinguish lost feedback receipts. This is not
   a production outcome adapter or durable reply receipt journal. Stopping after
   a send error does not resolve that outcome. Define
   trusted reassociation with execution evidence, receiver-side receipt
   requirements, and treatment of non-success outcomes. Keep inspection separate from reconciliation and any
   future retry decision. Duplicate refusal is not acknowledgment.
5. **Complete journal operations policy.** Optional `max_receipts` now refuses
   new feedback claims at a host-selected row limit without deleting evidence.
   All trusted writers must use the same limit; omission preserves existing
   behavior. This is not a byte quota or an execution-admission reservation.
   Decide retention, deployment capacity/backpressure, backup,
   migration and rollback policy. Do not silently delete unconfirmed claims or
   recreate missing journals: that can erase the basis for suppressing duplicate
   delivery. These policies can be designed and locally tested without deployment.
6. **Qualify the accepted deployment.** Exercise identity revocation, shutdown,
   storage recovery, output interception and operational response in the selected
   environment. Synthetic local qualification does not satisfy these gates.

No selected component lock, credential/grant issuance, default runtime
activation or production effect is authorized by this status document. Preserve
C0 check-to-commit limitations, optional same-host C1, no C2/C3, no real settlement
and operational orchestrator #30 HOLD.


## Next integration decision

The next production-adapter step requires an owner-selected host contract, not
another increase in synthetic test counts. The proposed starting point remains
one trusted Linux host, explicit kernel-UID enrollment and buffered final text.
That proposal has not selected a real service or accepted its security boundary.

Before wiring a production caller, record the service process, supported
entrypoints/channels, account-to-tenant mapping, policy owner, private state
location, revocation/shutdown owners and technical exclusions for every other
output path. Then bind one selected route end to end, including its final
provider representation and separate outcome/feedback observations. An available
test machine alone does not supply these decisions.

Work possible before deployment includes designing durable reply observations,
policy/lifecycle synchronization and journal retention/backup procedures against
that proposed contract. Such work must retain its provisional status; fixture
success cannot choose the host boundary or authorize activation.

## Historical design notes

The [initial conjunction](../../docs/workstreams/openappa-conjunction.md),
[persistent conjunction](../../docs/workstreams/openappa-persistent-conjunction.md),
[local conjunction](../../docs/workstreams/openappa-local-conjunction.md),
[resume boundary](../../docs/workstreams/openappa-resume-boundary.md),
[local host](../../docs/workstreams/openappa-local-host.md),
[feedback](../../docs/workstreams/openappa-feedback.md), and
[journal](../../docs/workstreams/openappa-feedback-journal.md) notes preserve
staged design and qualification history. Their candidate/pending language and
intermediate test counts refer to their stated revisions, not current status.
Their trust limitations still apply unless a later accepted change explicitly
supersedes them.
