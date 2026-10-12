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
