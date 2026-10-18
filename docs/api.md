# API Reference

Legacy compatibility shell. `AgentEngine` validates, permission-checks and logs an action and never executes it. For the supported execution path see [execution-boundary.md](execution-boundary.md) (`PinnedControlPlaneExecutor` and the producer contract).

## AgentEngine

### Initialization
```
from engine.engine import AgentEngine

engine = AgentEngine(sandbox_dir, permissions_file, audit_log_file)
```

### Methods

#### execute_action(agent_id: str, action: dict) -> dict
- Validates the action schema (raises on a malformed action after logging the refusal)
- Checks the caller-asserted `agent_id` against the permissions file
- Logs the request
- Raises `PermissionError` when the action type is not allowed; otherwise returns `status: unsupported`, `executed: False` (there is no execution adapter)

#### PermissionSystem
- `PermissionSystem.is_allowed(agent_id, action_type) -> bool`: True if the permissions file lists the action type for that agent.
- Loads from a JSON policy file.

#### Action Validation
- `validate_action(action: dict)`: Raises an error if the action is malformed or has extra fields; any non-empty `type` string is accepted.

#### Audit Logging
- Requests that pass schema validation, and refused malformed requests, are logged to the configured audit file with timestamp, action and result. Coverage is limited to what the engine records (see SECURITY.md).

## Safety Model
- The shell performs no side effect: it has no execution adapter and no sandbox.
