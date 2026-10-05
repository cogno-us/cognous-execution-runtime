# moltbot-safe

Moltbot Safe is the **single-agent constrained execution boundary** in the Cognous Open Source Stack.

## Supported runtime

The supported safety-layer runtime is the Python package under `engine/`. The repository also retains a substantial upstream TypeScript Moltbot application and its history. That application is **not** part of this reviewed execution boundary, and this work does not claim to audit or harden the whole upstream application.

The legacy `engine.AgentEngine` is compatibility-only. It validates, permission-checks and logs, but performs no effect and therefore cannot report successful execution.

The supported path is:

```text
Pinned Control Plane BoundedAuthorizationWorkflow.execute()
        -> current grant / approval / policy / evidence revalidation
        -> Moltbot Safe ControlPlaneRefundDestinationAdapter
        -> strict local execution policy
        -> durable synthetic SQLite refund
        -> destination observation / reconciliation
```

A persisted historical `authorized` decision is not an execution credential. An allow decision is not an executed effect. An execution acknowledgement is not independently verified delivery.

## Pinned integration baseline

- Agent Control Plane: `cogno-us/cognous-agent-control-plane` at `283500652d47a692fb0b99a1172a6d5faffbd9a7`
- Agent Action Manifest v1.1: `cogno-us/cognous-agent-action-manifest` at `46c950bed37fe3812000895430bc0312d29e37ce`
- Alvorada Authority Context 0.1.0: `cogno-us/constitutional-governance-for-institutions` at `fb3d97938969a89e149e8ff8db2756091d1233fc`

CI checks out the exact pinned Control Plane and Manifest revisions and executes integration tests against their real Python implementation and refund fixture.

## Execution Envelope 0.2.0

At API entry Moltbot Safe deep-snapshots the complete operation, including nested payload values, before any trusted resolver or Control Plane callback can run. Validation and destination execution use only this frozen snapshot.

For the single-refund adapter:

- `effects` must be the integer `1` exactly; booleans, zero, negatives, fractions and other counts are rejected;
- amount must be a finite non-negative `int` or `float`, excluding booleans;
- the payload commitment must match the frozen payload;
- effect identity is bound to a digest of the exact frozen operation.

## Trusted institution/domain binding

The pinned Control Plane checks institution and authority domain but does not export them in `AuthorizationBinding`.

Until the upstream contract is extended, Moltbot Safe derives both values from the **trusted Authority Context resolver used by the revalidating Control Plane workflow** and compares them exactly with the execution snapshot. A caller-supplied institution/domain label alone cannot satisfy the boundary.

The exact requested upstream extension is documented in [docs/control-plane-interface-gap.md](docs/control-plane-interface-gap.md).

## Destination, attempts and recovery

The synthetic destination is SQLite only. It does not touch real accounts, payment services, public chains or production credentials.

Each destination submission gets a durable attempt identity. Attempt rows are immutable identities and state changes are append-only attempt events. Reusing an attempt ID is rejected and recorded under a fresh denied attempt; it never overwrites earlier success.

SQLite `BEGIN IMMEDIATE` serializes effect deduplication, content binding and cumulative local effect-count checks for processes sharing one database file. The test suite exercises **separate processes**, not only threads.

A duplicate same-operation delivery is observed/reconciled and reports `newly_executed=false`. A conflicting operation under the same effect ID is rejected. Lost acknowledgement after durable commit remains `unknown` until observation establishes destination state. Partial delivery remains partial/held rather than being blindly re-applied.

Historical observation is available without renewing authority, but observation alone cannot authorize a new execution.

## Isolation boundary

Implemented for the supported path:

- no subprocess execution;
- no network calls from the destination adapter;
- no production credentials;
- dedicated operator-selected SQLite state root;
- path traversal and existing symlink-component rejection before path resolution;
- exact local restrictions on institution, authority domain, adapter, action, target prefix, unit, amount and effect count.

These are application-level restrictions, **not OS confinement**. Filesystem checks still have residual check/use races without an OS-level dirfd/openat-style confinement strategy. A separate host process with sufficient permissions can bypass this Python package. No container, VM, seccomp/AppArmor, network namespace or whole-upstream bypass-resistance claim is made.

## Tests

Focused and pinned integration tests run with:

```bash
PYTHONPATH=".:pinned/control-plane/src" \
MOLTBOT_SAFE_CONTROL_PLANE_ROOT="pinned/control-plane" \
MOLTBOT_SAFE_MANIFEST_FIXTURE="pinned/action-manifest/examples/refund_integration_v1_1.manifest.json" \
pytest -q tests
```

The retained TypeScript Moltbot test suite is upstream application coverage and is reported separately from this Python execution-boundary evidence.

## License and attribution

The repository preserves upstream history and the MIT license. See [LICENSE](LICENSE). This constrained Cognous Python integration layer does not alter the licensing or audit status of the retained upstream application.
