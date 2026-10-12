# Durable local feedback status

Candidate based on Runtime `89556d5aa7c6278a726b5815544c9aee82c1864c`.
Existing conjunction pins remain OpenAPPA
`10aa0e09e32d8c6704bdb030dc3c66140bf81915`, Control Plane
`2ea9528eeb87e14ff10f05de06473122b9df540f`, and Manifest
`46c950bed37fe3812000895430bc0312d29e37ce`.

The opt-in `JournaledReporter` wraps the trusted success-feedback callback.
Explicit initialization creates a new private host-owned SQLite file; lookup
never creates, replaces or migrates an existing journal. Rows contain a digest
of the enrolled binding plus call ID, the exact request digest, and status.
Request payloads, result bodies and authority records are not stored here.

A SQLite write transaction claims the call as unconfirmed before invoking the
reporter. Concurrent or later callers observe this status and do not deliver
again. Only the exact correlated Ack can advance the row to acknowledged.
Reusing the binding/call ID with different request bytes is refused. On reopen,
an acknowledged row returns the recorded receipt and an unconfirmed row remains
unconfirmed, without invoking the callback. Neither status authorizes an effect.

The existing feedback wrapper keeps destination execution status separate from
journal failure. Failure to persist a receipt cannot undo an applied effect or
permit a new one. A crash after claim, after remote feedback, or before storing
the Ack can strand the row as unconfirmed. This is deliberate: duplicate refusal
cannot resolve delivery ambiguity. There is no automatic retry or administrative
state-reset API in this module.

## Qualification

Nine local SQLite cases cover process reopen of acknowledged/unconfirmed state,
minimal storage, initialization refusal, invalid receipts, call identity reuse,
concurrent claimers, and missing/public/corrupt journals. Four connected cases
use the actual pinned runtime, native executor and synthetic SQLite destination:
success, lost receipt after feedback persisted, unsent feedback, and unavailable
journal after execution. Restart reads must preserve status without another
callback or destination effect. The hosted conjunction gate retains all previous
53 cases and requires 66 with no skips, failures or errors. Local tests do not
substitute for hosted Rust qualification; results are recorded in the PR.

## Limits

This is host bookkeeping, not a receiver-issued cryptographic receipt or an
independent effect observation. It trusts the enrolled binding, host callback,
private directory owner and existing file protections. It cannot detect journal
rollback/deletion/recreation by its trusted owner. It is not atomically coupled
to either destination commit or OpenAPPA feedback: a crash before the claim can
leave no row, and a crash after delivery can leave an unconfirmed row. The tests
exercise ordinary process reopen and transport-loss exceptions, not power loss
or forced host termination. Payload-free hashes are not a privacy guarantee for
low-entropy inputs. Journal retention/quotas and migrations remain unimplemented.

Trusted re-association with durable execution evidence, resolution of unconfirmed
rows, receiver-side receipts, non-success outcomes and production deployment
remain separate work. No effect-execution or feedback-retry entrypoint is added.
No selected component lock, default runtime or institutional authority changes.
Preserve C0 check-to-commit limits, optional same-host C1, no C2/C3, no real
settlement and operational #30 HOLD.
