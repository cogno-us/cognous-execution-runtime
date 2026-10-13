# OpenAPPA feedback journal operations

Development runbook for the trusted Linux host helpers at Execution Runtime
`3858c1e2735081187606a038a685e28c22143856` (PR #81 merge). These procedures
describe existing tooling; they do not select a deployment, install a service,
or approve retention, recovery or production use. See [status](README.md) for
exact revision-specific test evidence.

## State and ownership

The [journal](feedback_journal.py) stores a digest of the bound call identity,
the request digest and a feedback state. It does not store the original request,
execution evidence, receiver receipt body or independently observed settlement.
Digests are not anonymization. Keep files and operational records private.

The host operator owns the journal and its private directory. The implementation
rejects symlinked directory paths and requires a regular, same-owner private
source file. It trusts the directory owner and privileged host processes; it
does not protect against their replacing or rolling back state.

Use `initialize` only for an explicitly new journal, never as an error handler
for missing or unreadable existing state. Initialization is not a migration or
recovery operation. The journal, enrollment registry and OpenAPPA trajectory
store are separate state objects. Snapshotting one does not checkpoint all three.

## Inspection and response

Call `read_journal_status(path)` for one read-only count snapshot. It returns
`total`, `acknowledged` and `unconfirmed`. It exposes host-wide activity, so it
is not a tenant-facing status endpoint. Counts do not reserve capacity or prove
database integrity or remote delivery.

For an individual receipt, use `read_receipt(path, binding, raw)` with the
original trusted binding and exact request bytes from an approved evidence
source. Identity includes the enrolled store path, device and inode; rebuilding
a similar binding after relocation is not a supported reassociation procedure.
Do not reconstruct requests from model output or place raw requests in shell
arguments, public issue comments or ordinary logs.

| Observation | Meaning | Operator response |
| --- | --- | --- |
| `ack` from receipt inspection | The host recorded the exact expected feedback Ack | Retain the record; it is neither new execution permission nor independent receiver proof |
| `unconfirmed` | The feedback claim was committed but an exact Ack was not durably recorded | Preserve the claim and correlated evidence; delivery may already have happened; do not retry or mark acknowledged manually |
| `absent` | No matching claim was observed in this journal snapshot | Verify the selected journal and original binding; absence does not permit effect or feedback retry |
| Unavailable state or identity/digest mismatch | Inspection cannot establish the requested bookkeeping observation | Stop the affected workflow for operator investigation; do not convert the error to an empty journal or create replacement state |
| Capacity exception | No new feedback claim was admitted by that reporter | Preserve all rows and pause upstream new work through an accepted host control; an earlier effect may already have completed |

There is no supported automatic reconciliation, retry or manual-acknowledgment
API. An operator investigation must keep execution, transport observation and
feedback observation separate. If the exact original request or binding is
unavailable, retain that uncertainty instead of guessing a receipt association.

## Capacity and retention

`JournaledReporter(..., max_receipts=N)` enforces a positive integer row limit
while holding the SQLite writer reservation. Every trusted writer must use the
same configured limit. The setting is not persisted in the journal; omitting it
leaves a writer uncapped. Counts include both acknowledged and unconfirmed rows.
Existing matching claims can still be inspected or returned when the limit is
reached, without another callback delivery.

Choose an operational warning threshold below the approved limit and define
who can stop admission of new work. The helper provides neither monitoring nor
that upstream stop control. A count check before execution is not a reservation
and cannot guarantee space after execution. Row limits are not byte quotas;
filesystem space and snapshot storage need separate monitoring.

Do not delete acknowledged rows to make room: they also suppress duplicate
feedback delivery. Do not delete unconfirmed rows, rotate to a blank journal,
or prune the original after exporting a snapshot. No retention or migration
policy has been accepted. Raising a limit requires a host capacity decision,
not reinterpretation of existing receipts.

## Snapshot procedure

1. Identify the existing journal and a new destination in an existing private,
   host-owned directory. Keep the destination separate from live state. Use a
   unique filename; the export refuses an existing destination.
2. Invoke `export_journal_snapshot(source, destination)` from trusted host code.
   It opens the source read-only and uses SQLite backup, rather than copying a
   live database file and possibly omitting committed state in a WAL.
3. Consume the destination only after the call returns successfully. The helper
   creates it with mode `0600` and flushes the file before return. This is not
   power-loss qualification, directory-entry durability or an atomic publication
   protocol for another process watching the directory.
4. Inspect the completed snapshot using `read_journal_status(destination)`.
   Record the runtime revision, export start/end time, completion result and
   private artifact identity in approved operator records. Optional file hashes
   support later comparison; they do not prove origin or prevent rollback.
5. Keep the live journal in place. A consistent snapshot can exclude later
   committed claims, and live counts can change during or after export. Do not
   require equal live and snapshot counts under concurrent activity.

On ordinary export failure, the helper attempts to remove only the incomplete
file it created. Cleanup itself can fail. Process termination can leave an
incomplete file. A destination's presence, readable schema or plausible counts
does not prove successful completion: retain it as an unaccepted artifact until
investigated, and use a new destination for any subsequent export attempt.
Retrying an export is not retrying execution or feedback.

Snapshots can contain the entire host's receipt history. Define access controls,
storage limits and approved destinations before copying them elsewhere. This
runbook does not authorize publication or off-host transfer.

## Recovery remains gated

Do not point a live reporter at a snapshot or replace an active journal with one.
A stale snapshot can omit a claim that already caused delivery; restarting from
it can permit another callback. Independently restoring the trajectory store,
enrollment registry and journal can also break their association. A successful
export does not establish an accepted restore procedure.

Before recovery or migration is implemented, the deployment owner must accept:

- Who stops all writers and senders, and what evidence establishes that stop.
- How the journal, enrollment, trajectory and external observations share a
  recovery boundary, including work completed after a snapshot.
- How rollback is detected and original call identities survive relocation.
- How unresolved outcomes remain blocked without inventing acknowledgment or
  retry permission, and who approves any reconciliation decision.
- Retention, disk exhaustion, restore rehearsal and shutdown acceptance tests
  in the selected environment, with exact source revisions and limitations.

No production activation, component-lock change, credential/grant issuance or
release permission follows from this document. Preserve C0 check-to-commit
limits, optional same-host C1, no C2/C3, no real settlement and operational
orchestrator #30 HOLD.
