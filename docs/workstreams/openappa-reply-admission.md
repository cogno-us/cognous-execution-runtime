# Optional final-text dispatcher admission

Candidate based on Runtime `d0d161616337e1315ffde94ec6cb6c188800e6cc`.
This is a bounded implementation of the admission interface proposed in the
[host contract](openappa-host-contract.md), not a configured OpenAPPA production
adapter or full output interception.

`createReplyDispatcher` accepts an optional trusted-host `textAdmission` option.
Without it, the existing dispatch behavior and scheduling remain unchanged.
There is no product configuration switch, automatic installation or default
activation. A future host must explicitly wire the checker and delivery target.

The profile accepts final plain-text replies only, at most 16,384 UTF-8 bytes
**after** normalization and prefix expansion. Non-text metadata, media, channel
payloads, block streaming and tool replies are unsupported and stop the profile.
Queued text is copied into a frozen payload. Immediately before delivery, a
frozen check request binds a fresh output ID, final kind, host-supplied destination
ID and normalized text through SHA-256 over their JSON representation. Only an
explicit allow echoing that exact digest permits the delivery callback.

Checks have a finite timeout (default 5 seconds; supported range 1–30,000 ms).
Refusal, missing/uncorrelated response, thrown error or timeout stops this
admission instance and prevents later queued delivery. A late allow does not
resume the queue. An unsupported enqueue also stops outstanding checks before
delivery. Already-started delivery cannot be undone. A new dispatcher is a new
in-memory instance, not a durable retry authorization.

`sendFinalReply` returning true still means queue acceptance, not permission or
successful delivery. Existing `onError` receives failures; `waitForIdle` only
reports queue completion. No audit persistence or receiver acknowledgment is
added. The checker must be side-effect-free: timeout cannot forcibly terminate
arbitrary callback code, and a synchronous callback can block the JS event loop.

## Qualification

Sixteen tests use the actual reply dispatcher with a synthetic trusted checker
and delivery spy. They cover correlated normalized text, immutable queued input,
unique output IDs, refusal, missing/wrong receipts, checker exceptions, timeout
with late allow, unsupported output forms, size limits, current policy at queue
release, stopping an outstanding check and invalid timeout configuration.
Eighteen existing routing tests cover default behavior. The dedicated workflow
requires all 34 tests with no skips/failures/errors.

The observed boundary is invocation of the dispatcher's delivery callback, not
network packets or provider receipt. These tests do not execute OpenAPPA policy
for this new path. Existing Python/Rust conjunction evidence must not be presented
as qualification of this TypeScript admission interface.

## Remaining host responsibilities

The host must authenticate identity, bind destinationId to the actual deliverer,
select current policy and lifecycle state, and supply an accepted checker.
Neither a destination string nor a matching digest authenticates a caller. The
trusted delivery callback must send exactly the checked content to that target;
later formatting, routing or fallback changes need their own bound admission.
The current interface does not bind attachment bytes, implement policy-generation
synchronization, enforce cancellation of an in-flight provider request, or close
C0 check-to-commit races.

The implementation covers only this queue path. Agent events, gateway streaming,
direct channel APIs, tools, client delegation, persistence and other routes
remain separate surfaces. Complete coverage requires source mapping and actual
selected-host tests, not simply enabling this option. Preserve optional same-host
C1 limits, no C2/C3, no real settlement, selected component locks and operational
#30 HOLD. No credential/grant issuance or production activation is included.

## Actual-policy fixture follow-up

A dedicated Rust probe links the pinned OpenAPPA runtime
`10aa0e09e32d8c6704bdb030dc3c66140bf81915` to the actual TypeScript dispatcher
in a test-only bridge. Explicit fixture bootstrap creates a synthetic trajectory;
checks reopen only that existing SQLite store/root. The fixed `synthetic-public`
destination maps to the shared fixture's `send_public` policy. The Rust process
computes its response digest from the exact received output record, including
text, destination, kind and output ID, rather than echoing a caller's digest.

Six additional cases require actual allowance before one callback delivery,
persisted refusal after narrowing and repeated process reopen, missing/corrupt
store noncreation/nonreplacement, rejection of an actual allowance for different
text, and rejection of a destination outside the fixed mapping. A dedicated
workflow builds the real probe, checks TypeScript, and requires all 40 tests
(the six connected cases plus the existing 34) with no skips or failures.
Ordinary test runs without the probe skip the six connected cases; those skips
are not qualification evidence and are rejected by the dedicated gate.

This extends the synthetic-checker qualification above to actual pinned policy
for this narrow fixture. The fixed public-sink mapping is not a general output
policy. The bridge uses synchronous child-process calls with bounded process
limits for testing; it is not a production checker or event-loop deadline design.
It does not enroll/authenticate a production caller, transmit network content,
report successful delivery back to OpenAPPA, qualify output-policy changes during
an in-flight send, or cover other runtime routes. Existing host, C0/C1, no-retry,
lock and operational HOLD limitations remain. Hosted results must be inspected
before claiming these connected cases pass.

## Cooperative admission cancellation

The optional host configuration now accepts an `AbortSignal`. Already-cancelled
profiles reject enqueue; cancellation before dispatch avoids the policy call;
cancellation during an outstanding check ends the admission wait and stops later
queued delivery. The checker receives a per-check signal as its second argument;
host cancellation and admission timeout abort that signal. The stopped state is
set before notifying the checker, so an allow returned during abort cannot release
output. Timer and host abort listeners are removed when the check finishes.

Four additional unit cases cover these boundaries, including a send already
started before cancellation: that first callback remains an observed send, while
the second queued output is withheld. The timeout case also checks cancellation
notification to the checker. One connected case obtains actual pinned OpenAPPA
allowance and cancels before release; callback delivery must still remain zero.
The strict unit/routing gate now requires 38 cases and the actual-policy gate
requires 45 (38 plus seven connected cases).

Cancellation is cooperative. It cannot terminate arbitrary checker code, reverse
a started provider send, remove an OpenAPPA dispatch record already persisted by
a check, or prove remote non-delivery. The synchronous Rust test bridge remains
non-production and cannot process JS cancellation while its child call blocks.
This change introduces no production host wiring or automatic retry.

## Bounded pending queue

The opt-in profile accepts at most 32 pending replies per dispatcher, including
any current policy check or delivery callback. Together with the existing
16,384-byte per-reply limit, this bounds the accepted pending text to 512 KiB;
it is not a bound on total process memory, checker allocations or provider buffers.
A completed queue entry releases its capacity. The ungated dispatcher retains
its existing behavior.

The 33rd pending reply is rejected and stops the profile, rather than silently
dropping one item and continuing. Overflow or unsupported output aborts an active
check and ends the admission wait without waiting for its deadline. All queued
replies are withheld after the stop; an already-started send cannot be undone.
A checker that ignores its signal may continue executing, but cannot release
delivery. Capacity exhaustion provides no permission to recreate the dispatcher
and retry uncertain output.

Three new cases check overflow before dispatch, overflow during a never-settling
check, capacity release, and preservation of ungated backlog behavior. Strict
workflow totals become 41 unit/routing cases and 48 cases with the existing seven
actual-policy cases. This is local admission backpressure, not distributed rate
limiting, per-tenant quotas, durable deduplication or production-load qualification.

## Stop after delivery failure

An opted-in dispatcher now stops its admission instance when the delivery
callback rejects, before invoking the existing error handler. Already queued
replies do not obtain new policy checks or reach delivery; later enqueue is
refused. The ungated dispatcher retains its existing continuation behavior.

A rejected delivery promise is not evidence that nothing was sent. Unit cases
cover failure before a synthetic send and receipt loss after a synthetic send;
the latter retains the observed first send while withholding the second. A
connected case obtains actual pinned OpenAPPA allowance before the synthetic
send/receipt loss and verifies that the queue neither sends again nor requests
another allowance. Three added unit/routing cases and one connected case bring
the strict workflow totals to 44 and 52 respectively (44 plus eight connected).

This change stops subsequent output in the current in-memory profile. It does
not undo a send, determine provider outcome, persist delivery status, close the
OpenAPPA dispatch, or authorize replay through a new dispatcher. The host still
needs an accepted observation/recovery contract; production activation remains
off and all existing C0/C1, lock, authority and operational HOLD limits remain.
