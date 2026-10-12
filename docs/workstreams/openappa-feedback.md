# Synthetic execution outcome feedback

> Historical workstream note. See the [current OpenAPPA status](../../tools/openappa/README.md)
> for consolidated evidence, current scope and remaining work.

Candidate based on Runtime `812ef39e9ceb4f1e4c4adade75c8d0c2d65ea8d5`.
Existing conjunction pins remain OpenAPPA
`10aa0e09e32d8c6704bdb030dc3c66140bf81915`, Control Plane
`2ea9528eeb87e14ff10f05de06473122b9df540f`, and Manifest
`46c950bed37fe3812000895430bc0312d29e37ce`.

`feedback_harness.py` wraps the local authenticated conjunction and captures
its actual native execution result. Success feedback requires an executed or
reconciled status, an applied observation, and matching decision/effect/attempt
identifiers. It passes the exact flow request and enrolled binding to a trusted
callback. The Rust fixture reports a minimal synthetic success body through
actual `HookEvent::ToolResult`, retaining the original call ID and arguments.
Its acknowledgment is correlated to the exact request bytes.

The returned `FeedbackOutcome` separates execution status from feedback status.
Feedback can be acknowledged, unconfirmed or not-reported. Missing, malformed
or lost receipts leave executed status intact. The helper never retries an
effect or a feedback delivery. Partial, unknown, denied and unmatched results
are not promoted into successful tool outcomes. No raw destination observation,
control-plane evidence or error text is sent as the tool result body.

## Qualification

Nine cases cover success, unavailable feedback, receipt loss after actual
feedback, a wrong receipt digest, mismatched result identity, flow denial,
revoked authority, partial execution and lost destination acknowledgment.
Tests inspect the synthetic SQLite destination and reopen the actual OpenAPPA
audit in a separate process. `Closed/Ran` events with the declared `egress` effect distinguish persisted
feedback from a receipt that the host did not receive. Executor invocation
counts require no automatic re-execution after feedback failures.
The conjunction gate retains its previous 41 cases and requires all 50 with
no skipped, failed or errored cases. Local collection/lint are not Rust runtime
qualification; hosted exact-head results are recorded in the pull request.

## Limits

This is a test-owned host and refund-to-public-sink fixture, not a general
production adapter. The minimal synthetic body is not evidence of real settlement.
The callbacks and local directory owner remain trusted. Feedback receipt
correlation is not process attestation or a reusable permit. Non-success outcome
mapping, durable delivery/outbox, feedback deduplication, crash recovery, concurrent
lifecycle serialization and production output interception remain unqualified.
There is a gap between destination commit and feedback: a successful destination
effect may have unconfirmed feedback. No delivery retry is authorized by this
status, and no atomic transaction spans the destination and OpenAPPA store.

No selected component lock, default runtime or institutional authority changes.
Preserve C0 check-to-commit limits, optional same-host C1, no C2/C3, no real
settlement and operational #30 HOLD.


## Feedback recovery boundary

The follow-up gate adds three actual feedback-only delivery cases after an
unconfirmed host result: feedback was not sent, feedback persisted but its
receipt was lost, and an unknown call ID was reported before the correct one.
The probe exposes a correlated `refuse` response when ToolResult is not Ack,
rather than treating that result as a process crash. The prior 50 cases remain;
the strict total becomes 53.

Pinned `classify_report_identified` only attributes results to open dispatches.
Once the dispatch is closed, it cannot distinguish an already-consumed report
from an unknown report. The expected recovery boundary is therefore: an open
dispatch can accept its matching report; closed and unknown reports refuse.
Duplicate refusal must never be promoted to acknowledged delivery. Tests require
unchanged audit on refusal, exact successful close/admission events on delivery,
and exactly one native execution and one destination effect throughout.

This qualifies explicit test-owned delivery, not an automatic retry worker or
durable outbox. The test retains exact request bytes in memory, so it does not
prove host-crash recovery. Before production recovery, design durable trusted
request/outcome association and a receipt protocol that preserves unconfirmed
state without replaying the destination operation. Aggregate audit projections
do not by themselves provide per-call delivery receipts. All preceding trust
boundaries and operational #30 HOLD remain.
