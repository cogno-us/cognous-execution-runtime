# Architecture

Moltbot Safe provides one supported constrained execution boundary for the current Cognous synthetic pilot.

## Supported path

```text
Control Plane canonical issued decision
        |
        v
ExecutionEnvelope 0.1.0
        |
        v
SafeExecutor
  |-- exact decision/binding validation
  |-- stricter local execution policy
  |-- frozen operation
        |
        v
DurableRefundDestination (SQLite)
  |-- transactional effect-id binding
  |-- local cumulative effect limit
  |-- authoritative local observation
        |
        v
reconciliation / hold / observed applied
```

The execution caller does not provide an authoritative decision body. `InProcessDecisionSource` retrieves the canonical issued decision by ID from a configured trusted callable.

## Runtime boundary

The supported safety layer is the Python `engine/` package, specifically `SafeExecutor`.

The retained TypeScript Moltbot application is not inside this reviewed execution boundary. The legacy Python `AgentEngine` is compatibility-only and performs no side effect.

## Consistency boundary

The synthetic destination uses SQLite `BEGIN IMMEDIATE` to serialize writers sharing one database file. Effect identity, operation content and cumulative grant effect count are checked and committed within that local transactional boundary.

This does not provide a distributed transaction, remote exactly-once delivery or a distributed budget.

## Isolation boundary

The supported executor makes no subprocess or network calls and uses no production credentials. Its filesystem capability is limited by application logic to the configured SQLite state root, with traversal and symlink checks.

These are application-level restrictions, not operating-system isolation. A directory is not a sandbox. Host/container isolation and protection against alternate host-process invocation remain deployment responsibilities.

See [execution-boundary.md](execution-boundary.md) for the complete contract.
