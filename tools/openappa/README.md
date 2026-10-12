# OpenAPPA integration status

Status reviewed against Execution Runtime
`e4fa253d020bed3b0873a4bb2e8d3491c766e948` (PR #64 merged).
This directory contains opt-in synthetic qualification tooling and trusted-host
helpers. It is not an installed production adapter or release acceptance.

## Verified current scope

The latest inspected conjunction run is
[38314021317](https://github.com/cogno-us/cognous-execution-runtime/actions/runs/38314021317),
job `115026256703`, at PR #64 head
`d4f8e77841d5900488f9ccb11d6a404ae0d2dae2`: **82 passed in 11.23 seconds**.
The workflow also required exactly 82 JUnit cases with no skips, failures or
errors. This is evidence for that source revision and bounded fixture scope;
it does not establish that all repository or platform CI passed.

| Capability | Current implementation and qualification | Boundary |
| --- | --- | --- |
| Flow and institutional permission | [Conjunction harness](conjunction_harness.py) checks correlated flow allowance before native authority validation and synthetic SQLite execution | The two permissions remain independent; no comprehensive refund information-flow policy |
| Persistent trajectory | [Persistent probe](cognous_persistent_join_main.rs) reopens the existing upstream store/root and retains restrictions | Synthetic tool mapping; no rollback protection or complete lifecycle serialization |
| Local identity and resume | [Local resume](local_resume.py) uses kernel Unix peer UID and explicit host enrollment; missing/replaced state refuses resume | Linux local OS identity, trusted host/directory; no remote identity or hostile same-UID isolation |
| Result feedback | [Feedback wrapper](feedback_harness.py) reports only correlated applied success and keeps effect status separate from feedback status | Partial/unknown results are not success; unavailable feedback never retries the effect |
| Durable feedback bookkeeping | [Journal](feedback_journal.py) commits a claim before delivery and records only an exact correlated Ack | Unconfirmed rows remain unresolved; no atomic coupling to effect or remote feedback |
| Receipt inspection | `read_receipt` in the journal opens existing state read-only and distinguishes absent, unconfirmed and recorded Ack | Absent is not retry permission; unavailable state is an error; no remote receipt proof |
| Crash and contention checks | [Crash cases](test_journal_crash.py) and [process contention cases](test_journal_contention.py) exercise owned child termination and six claimers | Known process checkpoints, not power failure, storage loss or production-load qualification |

The 82-case conjunction gate includes several scopes in this table. Separate
wire, embedded-runtime, trajectory and local-resume workflows remain distinct
qualification surfaces. Do not add historical test totals together or infer
that those independent workflows ran at the PR #64 head.

## Reproduction and source selection

Use the repository's [conjunction workflow](../../.github/workflows/openappa-conjunction.yml)
for the exact dependency setup, Rust probe build, Python environment and strict
JUnit checks. Running Python cases without the actual compiled probes is not
substitute evidence for the connected cases. Other workflows are
[wire fixtures](../../.github/workflows/openappa-wire-fixtures.yml),
[embedded runtime](../../.github/workflows/openappa-embedded-runtime.yml),
[trajectory](../../.github/workflows/openappa-trajectory.yml), and
[local resume](../../.github/workflows/openappa-local-resume.yml).

| Source | Pinned revision |
| --- | --- |
| OpenAPPA | `10aa0e09e32d8c6704bdb030dc3c66140bf81915` |
| Control Plane | `2ea9528eeb87e14ff10f05de06473122b9df540f` |
| Action Manifest | `46c950bed37fe3812000895430bc0312d29e37ce` |

These are fixture pins, not an advancement of the selected hub component lock.
The [PR #64 qualification record](https://github.com/cogno-us/cognous-execution-runtime/pull/64#issuecomment-6133427534)
records the inspected result and the incomplete broader CI status at merge.

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
2. **Specify output admission and sink coverage.** Inventory tool, model-output,
   streaming and external-effect paths for that host; define where current flow
   policy must be checked. The current probe's public-send decision does not
   establish interception of every sink. This design can proceed before the
   deployment environment exists; end-to-end enforcement cannot be qualified
   without that environment.
3. **Define policy and lifecycle coordination.** Resolve expiry, policy-generation
   changes, root continuity and concurrent lifecycle updates. Retain explicit
   check-to-commit limitations until an accepted synchronization contract is
   implemented and independently qualified.
4. **Specify unresolved-feedback operations.** Define trusted reassociation with
   execution evidence, receiver-side receipt requirements, and treatment of
   non-success outcomes. Keep inspection separate from reconciliation and any
   future retry decision. Duplicate refusal is not acknowledgment.
5. **Bound journal operations.** Decide retention, capacity/backpressure, backup,
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
