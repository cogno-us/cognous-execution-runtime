# OpenAPPA host and output-admission contract proposal

Design proposal against Execution Runtime
`97e38f14520c04c6c02c01ec18b6232089561ecc` (PR #65 merge).
This document specifies candidate implementation requirements. Merging it does
not accept a deployment host, enable enforcement, or qualify production coverage.
See the [current status](https://github.com/cogno-us/cognous-stack-qualification/blob/43fb81dc3ec0da17402bef274dcec1831cc5e160/suites/cognous-execution-runtime/tools/openappa/README.md) for executed evidence.

Source-file links in this page point to the quarantine tag `upstream-moltbot-quarantine-20261016`: the upstream application they describe is not on `main` (see the [quarantine record](../quarantine-upstream-moltbot-20261016.md)).

## First bounded host profile

Propose an opt-in Linux, single trusted host profile, building on
[local resume](https://github.com/cogno-us/cognous-stack-qualification/blob/43fb81dc3ec0da17402bef274dcec1831cc5e160/suites/cognous-execution-runtime/tools/openappa/local_resume.py). A connected Unix socket's
kernel UID selects an explicitly enrolled tenant/principal/session/root/store.
The model supplies neither identity replacements nor policy selection. The
profile trusts host code, its operator, and the private directory owner. It does
not cover hostile same-UID processes, privileged host compromise, distributed
storage, remote identity or enterprise tenant isolation.

Before implementation is promoted beyond tooling, the deployment owner must
select the actual service process, supported entrypoints and channels, OS-account
mapping, policy owner, state directory, revocation procedure and shutdown owner.
No actual owner, account, endpoint or credential is assigned by this proposal.
Unsupported paths must be disabled in the opt-in profile or separately qualified;
a statement that a path is unused is not a technical disablement.

## Host-owned admission record

The candidate host constructs an immutable record for each boundary crossing.
It is not a caller-supplied `authenticated` flag or a reusable authorization token.

| Field group | Source and required check |
| --- | --- |
| Identity | Kernel-selected enrolled binding; tenant/principal match the native operation; session/root/store association is existing and verified |
| Continuity | Expected store device/inode and root; lifecycle/policy generation from the accepted host policy source, not from model arguments |
| Correlation | Host-managed run, call, operation, attempt, decision and effect IDs as applicable; no inference that different identifiers are interchangeable |
| Destination | Concrete channel/account/recipient or tool/effect target selected through trusted routing; audience and destination are part of the check |
| Content | Exact canonical tool arguments or final egress representation, including attachments and relevant metadata; hash is correlation, not authentication |
| Timing | Host-observed time, policy validity and cancellation state checked at admission; no model-supplied clock |
| Evidence | Bound flow decision and, for effects, the independent current institutional decision; retain distinct execution and transport observations |

An output without a native effect ID still needs its own host-owned output ID;
it must not borrow an unrelated effect's authorization. The output sequence and
parent run/call association must survive retries or be explicitly unresolved.
Digests of low-entropy content are not privacy guarantees.

## Admission order and failure behavior

1. Resolve the enrolled identity and existing trajectory. Any missing, malformed,
   inconsistent or unavailable binding/store refuses the proposed dispatch;
   never create a replacement root as recovery.
2. Freeze the proposed destination and content after all transformations that
   affect the policy decision. Evaluate current flow policy for this exact
   boundary crossing. For a native effect, independently revalidate institutional
   authority through the accepted executor contract.
3. Require an explicit, correlated allow. Missing adapters, errors, timeouts,
   stale generations, identity mismatches and unknown outcomes refuse dispatch.
   Logging an error and continuing is not an admission decision. A refusal must
   remain a refusal even if later optional hooks modify content or routing.
4. Dispatch only the admitted representation to the admitted destination. A
   routing, payload, attachment or policy-generation change requires a fresh
   check. Queued items are checked when released to transport, not only when
   added to a queue. Cancellation/refusal prevents later queued sends.
5. Record effect outcome, transport acknowledgment and flow feedback separately.
   Partial or ambiguous transport completion remains partial/unknown. No retry
   follows merely from missing observation, an absent journal row or lost Ack.

This order does not close the check-to-commit race. Any proposed serialization
must cover all relevant policy/identity writers and senders, define its commit
boundary, and receive separate qualification. The existing C0 limitation remains;
optional same-host C1 evidence cannot be generalized to network effects.

## Inspected runtime surfaces and proposed controls

The following is a bounded source inventory, not an exhaustive call graph or a
claim that the listed paths are currently governed by OpenAPPA. Links refer to
files inspected at the source revision above.

| Surface and source | Observed behavior | Required candidate control |
| --- | --- | --- |
| [Native tool adapter](https://github.com/cogno-us/cognous-execution-runtime/blob/upstream-moltbot-quarantine-20261016/src/agents/pi-tool-definition-adapter.ts), `toToolDefinitions` | Calls `tool.execute`; converts many tool errors into result objects | Await admission before execution; carry explicit refusal separately from ordinary tool errors; result reporting cannot authorize another attempt |
| Client tools in the same adapter, `toClientToolDefinitions` | Invokes `onClientToolCall` and returns a pending result for client execution | Treat delegation as an egress boundary; no claim of governing the remote effect without an accepted client contract; disable delegation in the first profile unless qualified |
| [Reply dispatcher](https://github.com/cogno-us/cognous-execution-runtime/blob/upstream-moltbot-quarantine-20261016/src/auto-reply/reply/reply-dispatcher.ts) | Queues tool/block/final replies and later invokes `options.deliver`; errors reach `onError` | Check at actual delivery, bind dispatch kind and destination, and retain refusal distinct from queue acceptance or idle status |
| [Outbound delivery](https://github.com/cogno-us/cognous-execution-runtime/blob/upstream-moltbot-quarantine-20261016/src/infra/outbound/deliver.ts) | Normalizes payloads, chunks text, sends channel payloads/media, and supports best-effort handling | Bind the concrete transport representation; cover text, media and channel-specific payloads; best-effort mode cannot convert admission refusal into permission |
| [Telegram delivery](https://github.com/cogno-us/cognous-execution-runtime/blob/upstream-moltbot-quarantine-20261016/src/telegram/bot/delivery.ts) | Calls the channel API, including formatted-text and fallback sends | Cover direct channel paths and fallback content; a common outbound helper alone is not proof of coverage |
| [Assistant streaming](https://github.com/cogno-us/cognous-execution-runtime/blob/upstream-moltbot-quarantine-20261016/src/agents/pi-embedded-subscribe.handlers.messages.ts) and [gateway chat](https://github.com/cogno-us/cognous-execution-runtime/blob/upstream-moltbot-quarantine-20261016/src/gateway/server-chat.ts) | Emits assistant events/partial callbacks; gateway sends chat deltas and final events | Gate before externally visible emission, including gateway clients; a final-message check cannot retract an earlier disclosure |
| [Tool-result persistence wrapper](https://github.com/cogno-us/cognous-execution-runtime/blob/upstream-moltbot-quarantine-20261016/src/agents/session-tool-result-guard-wrapper.ts) | Applies `tool_result_persist` transforms when configured | Treat persistence and subsequent reads as separate policy surfaces; a persistence hook is not a pre-execution or network-egress gate |
| [Plugin hook runner](https://github.com/cogno-us/cognous-execution-runtime/blob/upstream-moltbot-quarantine-20261016/src/plugins/hooks.ts) | Defines tool/message hooks; defaults to catching/logging hook errors | Do not use optional best-effort hook execution as the sole mandatory enforcement boundary |

A symbol search of production `src` for `runBeforeToolCall`, `runAfterToolCall`,
`runMessageSending` and `runMessageSent` found definitions/exports in the hook
runner, but no additional call sites. This limited search is not proof about
all dynamic dispatch or external dependencies. Hook names and types alone are
insufficient evidence that a production path invokes and awaits a gate.

Other channels, extension adapters, browser/network tools, local file writes,
subagents, scheduled jobs, CLI output, logs, telemetry and exported artifacts
were not comprehensively traced here. They remain unqualified surfaces. The
first profile must either demonstrate their technical exclusion or extend the
inventory and tests before claiming complete coverage.

## Streaming and attachments

For an initial bounded implementation, prefer buffering one complete output
within explicit byte/time limits, then checking the final transport representation.
Exceeding a limit refuses that output; it must not silently fall back to streaming.
Buffers need a defined owner, lifetime, cleanup and sensitive-data handling policy.
This buffering strategy is proposed, not implemented or performance-qualified.

Incremental release requires a separate contract for cumulative information-flow
state, chunk order, policy changes, cancellation and already-observed prefixes.
Individual chunk checks do not automatically prove a safe cumulative disclosure.
Reasoning, tool progress, media captions and error text are also output content.

A mutable URL or file path is not an immutable attachment. The host must bind
and transport the checked bytes through a supported mechanism, or refuse the
unsupported attachment path. Destination rewrites, provider formatting and
fallback sends must preserve the checked representation or trigger a fresh
admission decision. Provider acknowledgment is not independent delivery or
settlement evidence.

## Implementation sequence and acceptance evidence

| Step | Bounded deliverable | Required evidence before claiming the step complete |
| --- | --- | --- |
| Host selection | Explicit supported profile, identity lifecycle, entrypoint/sink inventory and technical exclusions | Owner-reviewed contract; source/configuration evidence for each included and disabled route |
| Admission interface | Opt-in host-owned record and explicit allow/refuse/unavailable outcomes, without production wiring | Identity/destination/content mismatch cases; adapter absence and timeout refuse; no caller-authentication shortcut |
| One supported output path | Buffered synthetic output crossing one actual host boundary | Allowed output emitted once; refusal/error/stale generation emits zero bytes; queued mutation and fallback paths covered |
| Full selected-host coverage | Wire every enabled entrypoint, output and effect path; exclude unsupported ones | End-to-end route matrix, streaming/media tests, and bounded controls demonstrating tests detect a missing gate |
| Lifecycle and operations | Revocation, cancellation, shutdown, continuity and unresolved-feedback procedures | Selected-environment tests with exact revisions; C0/C1 limits, unavailable evidence and residual risks recorded |

No new capability in this table is marked qualified by a documentation merge.
Tests of a standalone helper cannot substitute for observing the selected
runtime's actual output boundary. Existing synthetic refund and feedback tests
remain useful regression evidence within their stated scope.

No credential/grant issuance, selected component-lock advancement, default
runtime activation or production release is authorized. Preserve C0 limits,
optional same-host C1, no C2/C3, no real settlement and operational #30 HOLD.

## Bounded implementation follow-up

The [optional final-text dispatcher gate](openappa-reply-admission.md) implements
one admission interface and tests the actual queue's delivery-callback boundary.
The synthetic fixture now uses actual pinned OpenAPPA policy and explicit
ToolResult feedback, with persisted-audit checks for lost receipts and duplicate
feedback. This qualifies a test-owned callback boundary, not an authenticated
deployment host, final provider representation or durable reply observation.
The initial buffering proposal above has this bounded implementation; it does
not establish complete output buffering or production performance.
Streaming, other output routes, complete sink coverage and deployment acceptance
remain unqualified. The proposal's wider requirements are not superseded.


## Gateway route preflight for the Linux development profile

Source inspected at Execution Runtime
`8245db38e56f5298f260332fccf848556ffb8bbf` (PR #76 merge).
Use the proposed single-host Linux, buffered-final-text profile as the development
direction. This inspection does not select a deployed service, OS account,
recipient, credential or production route. It identifies why connecting the
existing dispatcher alone would not complete a gateway integration.

The [gateway chat methods](https://github.com/cogno-us/cognous-execution-runtime/blob/upstream-moltbot-quarantine-20261016/src/gateway/server-methods/chat.ts) construct a
reply dispatcher whose delivery callback collects final text. They pass
`disableBlockStreaming: true` to the reply resolver. Separately, the
[gateway event handler](https://github.com/cogno-us/cognous-execution-runtime/blob/upstream-moltbot-quarantine-20261016/src/gateway/server-chat.ts) publishes agent events,
chat deltas and lifecycle-driven final/error messages. The block-streaming flag
therefore does not establish an all-output buffering boundary.

| Inspected surface | Observed route | Required control in a future opt-in profile |
| --- | --- | --- |
| Agent-run chat output | Assistant events populate buffers and produce chat deltas; lifecycle events produce final/error output through broadcast and session-node delivery | Suppress early content on both output transports; bound the buffer and admit the complete final representation before either transport receives it |
| Agent event output | Agent payloads are broadcast separately and forwarded to the session node; tool event visibility has a separate verbosity check | Treat event content as an output surface; exclude unsupported streams technically and define the small set of content-free lifecycle events allowed before final admission |
| Non-agent final reply | The chat method combines and trims collected reply parts, appends a transcript entry and broadcasts a final message | Check the combined representation and concrete audience after transformations; qualify transcript access separately from live output |
| History | The history method reads, strips envelopes and byte-caps transcript messages before returning them | Disable history in the initial profile or apply an accepted historical-disclosure policy; a prior live-send decision is not automatic permission to disclose history |
| Injection and errors | Injection writes assistant transcript content; chat methods and event handling have error-response paths outside the final-text callback | Exclude injection until separately governed; use bounded, approved error content and cover direct responses as well as broadcasts |

[Gateway method authorization](https://github.com/cogno-us/cognous-execution-runtime/blob/upstream-moltbot-quarantine-20261016/src/gateway/server-methods.ts) already
checks client roles and scopes. This inventory does not claim those checks are
absent or bypassed. Their existence does not establish the proposed Linux
kernel-UID enrollment, tenant/root binding or OpenAPPA output-admission contract.
A client-supplied session key or display identity must not substitute for the
accepted host identity mapping.

Before implementation, choose whether the first supported transport is a
separate local-only text endpoint or a restricted gateway profile. A gateway
profile must account for both broadcasts and session-node delivery and all the
surfaces above. A separate endpoint can have a smaller scope only if existing
channels and gateway routes are technically excluded from that profile.

The next reviewable implementation should provide a host-owned run context,
explicit route exclusions and bounded final-output assembly before connecting
the existing admission gate. Acceptance must observe every included output
transport: denied, cancelled, oversized, unsupported or unavailable checks emit
no protected content; allowed output matches the admitted bytes and audience;
errors, history and lifecycle completion do not introduce another output path.
The test matrix must also distinguish post-send uncertainty from refusal and
must not use absence of a receipt to authorize another send.

No gateway behavior is changed by this preflight. End-to-end deployment
qualification, policy-generation coordination, durable outcome handling and the
existing C0/C1 limitations remain outstanding. The inspected paths are a bounded
inventory, not a comprehensive audit of extensions, tools or all gateway methods.
