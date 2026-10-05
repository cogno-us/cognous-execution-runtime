# moltbot-safe

Moltbot Safe is the **single-agent constrained execution boundary** in the Cognous Open Source Stack.

## Supported runtime

The supported safety-layer runtime is the small Python package under `engine/`. The repository also retains a substantial upstream TypeScript Moltbot application and its history. That TypeScript application is **not** part of the supported execution boundary described here, and this work does not claim to audit or harden the whole upstream application.

The legacy `engine.AgentEngine` is compatibility-only. It may validate, permission-check and log an action, but it performs no effect and therefore returns `status: unsupported` with `executed: false` for allowed actions.

The supported execution path is `engine.safe_executor.SafeExecutor`.

## What the supported path does

`SafeExecutor` consumes a versioned execution envelope derived from the pinned Agent Control Plane bounded-authorization pilot. It:

1. resolves the canonical issued decision through a configured trusted **in-process** decision lookup;
2. rejects caller-supplied authorization claims as authority;
3. verifies the decision is `authorized` and that its bound actor, principal, manifest/proposal commitments, action, adapter, target, payload commitment, amount/unit, grant, requirement and effect limit match the requested operation;
4. applies stricter local execution policy without widening upstream authority;
5. freezes the validated operation and performs one synthetic local refund effect;
6. records the effect durably in SQLite with a stable effect identity and separate attempt identity;
7. observes destination state independently of the execution acknowledgement; and
8. reconciles lost acknowledgements, duplicates, restart recovery, partial delivery and concurrent local attempts.

An allow/authorized decision is not an executed effect. An execution acknowledgement is not independently verified delivery.

## Pinned integration baseline

- Agent Control Plane: `cogno-us/cognous-agent-control-plane` at `283500652d47a692fb0b99a1172a6d5faffbd9a7`
- Agent Action Manifest v1.1: `cogno-us/cognous-agent-action-manifest` at `46c950bed37fe3812000895430bc0312d29e37ce`
- Alvorada Authority Context 0.1.0: `cogno-us/constitutional-governance-for-institutions` at `fb3d97938969a89e149e8ff8db2756091d1233fc`

See [the execution-boundary contract](docs/execution-boundary.md).

## Trust and authentication boundary

The pilot uses an **in-process trusted call** to retrieve the canonical Control Plane decision by `decision_id`. It does not provide network transport authentication, service identity, key custody or production credential management. A copied decision JSON, matching hash, or caller-provided `authorized` flag is insufficient.

The pinned Control Plane `AuthorizationBinding` does not include `institution_id` or `authority_domain`, although the Control Plane checks them before issuing its decision. Moltbot Safe preserves `institution_id` in its execution envelope and narrows it through local policy, but cannot independently re-bind it to the pinned decision. The exact proposed upstream extension is documented in [the Control Plane interface gap](docs/control-plane-interface-gap.md).

## Synthetic destination and consistency

The only implemented effect is a local synthetic refund written to SQLite. It does not touch payment services, accounts, public chains, networks or production credentials.

SQLite `BEGIN IMMEDIATE` serializes writers sharing the same database file, providing transactional local effect deduplication and cumulative `max_effects` enforcement across processes using that file. This is **not** distributed budgeting and is **not** an exactly-once guarantee for remote systems.

A reused `effect_id` with different operation content is rejected. Repeated delivery of the same operation is reconciled rather than re-applied. Partial delivery remains held.

## Isolation boundary

Implemented for the supported path:

- no subprocess execution;
- no network calls;
- no production credentials;
- a dedicated operator-selected state root;
- rejection of database path traversal and symlinked database paths;
- exact adapter/action/target/unit/amount/effect-count local restrictions;
- unsupported actions unavailable through `SafeExecutor`.

Not implemented or claimed:

- OS/container isolation;
- confinement of the full upstream TypeScript application;
- prevention of a separate host process bypassing the Python package;
- network namespace enforcement;
- distributed transaction or budget coordination;
- transport authentication between Control Plane and executor;
- independent institutional verification of the final effect.

A directory alone is not treated as a sandbox.

## Tests

Focused Python tests:

```bash
PYTHONPATH=. pytest -q
```

The tests assert destination state, not only logs, including authorization binding, substitution rejection, local narrowing, effect-ID content binding, duplicate delivery, lost acknowledgement, restart recovery, partial delivery, transactional concurrent limits, malformed legacy actions, path traversal, symlink paths and the non-executing legacy API.

The large TypeScript Moltbot test suite is upstream application coverage and should be reported separately from these safety-layer tests.

## License and attribution

This repository preserves the upstream history and MIT license. See [LICENSE](LICENSE). The supported Python boundary is a constrained Cognous integration layer; it does not alter the licensing or audit status of the retained upstream application.
