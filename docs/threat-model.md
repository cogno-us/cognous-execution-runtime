# Threat Model

Scope: the supported Python `SafeExecutor` synthetic refund path only. This document does not claim to cover the retained TypeScript Moltbot application or host operating system.

## Assets

- integrity of the issued Control Plane decision consumed by the executor;
- exact target, payload, adapter, amount/unit and grant binding;
- durable effect identity and attempt lineage;
- synthetic destination state;
- local cumulative effect limit.

## In-scope threats

- execution without a canonical authorized decision;
- copied or tampered decision data;
- target, payload, adapter or action substitution;
- reuse of one effect ID for different operation content;
- duplicate delivery after lost acknowledgement or restart;
- concurrent local attempts exceeding the declared local limit;
- local policy widening upstream authority;
- database path traversal or symlink substitution;
- legacy paths falsely reporting successful execution.

## Implemented mitigations

- canonical decision lookup by `decision_id` through a trusted in-process boundary;
- exact comparison against available Control Plane `AuthorizationBinding` fields;
- payload commitment verification;
- additional local allow/restriction policy;
- immutable operation values after validation;
- SQLite transaction around dedupe, operation binding, grant count and effect commit;
- durable destination observation before retry/reconciliation;
- no subprocess, network or production-credential capability in the supported adapter;
- traversal and database-symlink rejection;
- compatibility engine reports `unsupported`, never successful execution.

## Residual and out-of-scope risk

- transport/service authentication is not implemented; the pilot uses an in-process trusted call;
- the pinned Control Plane decision does not expose institution/domain in its persisted binding;
- a separate host process can bypass this Python package if the deployment grants it access;
- OS/container sandboxing, seccomp/AppArmor, network namespaces and credential brokers are not implemented here;
- no distributed budget, distributed transaction or remote exactly-once guarantee is claimed;
- an executor acknowledgement is not independent institutional verification;
- synthetic resolver identity/authority fixtures upstream are unauthenticated test infrastructure.

See [control-plane-interface-gap.md](control-plane-interface-gap.md) for the proposed downstream-binding extension.
