# W2 local stop and recovery qualification

Status: qualification scope for bounded V1. This document does not define fleet cancellation.

## Selected existing mechanism

W2 qualifies the already-used local process termination and SQLite reopen path in the atomic local authority/effect profile. The mechanism is intentionally narrow: one parent process terminates one local worker process and then reopens the same local authoritative SQLite destination.

The semantics are separated as follows:

1. **Stop request** — the parent issues `Process.terminate()` to one worker.
2. **Stop acknowledgement** — the parent observes that worker exit after `join()` and a non-null exit code.
3. **Dispatch closure** — that stopped process is no longer alive and cannot originate additional local dispatches. This says nothing about other workers or remote systems.
4. **Quiescence observation** — after the worker has exited, the same local SQLite destination is reopened and its retained effect/attempt state is observed stable across a bounded observation interval.
5. **Destination reconciliation** — the original claim plus exact frozen execution envelope is reconciled against the reopened destination. Reconciliation is not inferred from process exit.

## Transaction boundaries

The existing atomic profile uses one SQLite transaction for attempt insertion, claim consumption, budget increment and effect insertion.

- stop after transaction begin but before commit: SQLite rollback leaves the claim issued and no retained effect/attempt;
- stop after effect insertion but before commit: the uncommitted effect and attempt roll back together;
- stop after commit: the claim remains consumed and the effect and original attempt remain durable.

A stop after commit is therefore **not rollback**. Historical committed effects are never rewritten as reversed merely because the worker process stopped.

## Lost acknowledgement and restart

A lost acknowledgement after commit retains the original `effect_id` and `attempt_id`. Restart reopens the destination and reconciles the exact original envelope. The observed applied state carries `retry_eligible=false`. A repeated execution request does not create a second effect or a second durable execution attempt.

## Qualified negative boundaries

The focused W2 tests cover:

- interruption before commit;
- interruption after commit;
- restart and destination observation;
- lost acknowledgement;
- repeat execution after uncertain acknowledgement;
- stable effect/attempt identity;
- absence of blind retry permission;
- explicit proof that stop does not reverse a committed effect.

## Limits

This is local-process qualification only. It does not establish:

- fleet-wide cancellation or suspension;
- remote worker termination;
- distributed quiescence;
- cancellation of an external system already processing a request;
- rollback, compensation or reversal of committed external effects;
- exactly-once delivery outside the selected SQLite profile;
- authenticated stop requests or operator identity;
- recovery from host loss or corrupted durable storage.

The selected mechanism closes one local worker's dispatch path. Any broader coordinator semantics remain deferred to W7.
