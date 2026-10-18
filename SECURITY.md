# Security Policy

This policy covers the Cognous Python execution boundary under `engine/` only. The upstream Moltbot TypeScript/native application is **quarantined**: it is preserved only at the tag `upstream-moltbot-quarantine-20261016`, is not shipped on `main`, is not maintained or reviewed, and carries 18 known unrepaired security findings. See [docs/quarantine-upstream-moltbot-20261016.md](docs/quarantine-upstream-moltbot-20261016.md). Do not deploy that application as part of the Cognous stack.

## Security Posture (engine/ boundary only)
- **Least Privilege**: The legacy `AgentEngine` checks requests against explicit `permissions.json` entries; the supported `SafeExecutor` path applies local restrictions on institution, authority domain, adapter, action, target, unit, amount and effect count (targets match by prefix by default; the opt-in `target_match` modes `segment` and `exact` are stricter; targets with NUL, control or format characters or dot-segments are always refused).
- **Isolation**: Application-level restrictions only, **not OS confinement**. The default path performs no subprocess or network calls from the destination adapter and rejects path traversal and existing symlink components, but residual filesystem check/use races remain and a separate host process with sufficient permissions can bypass this Python package. See [docs/execution-boundary.md](docs/execution-boundary.md).
- **Transparency**: The supported path records durable attempt identities and append-only attempt events for destination submissions it handles. Audit coverage is limited to the events the engine actually records; it is not a guarantee that every attempted request, on every path, produces a log entry.
- **No Implicit Capabilities**: No inferred or default permissions.
- **No Autonomous Expansion**: The engine cannot grant itself new abilities.

## Threat Model Boundaries
- **In Scope**: Malicious or buggy agents, misconfigured permissions, audit log tampering.
- **Out of Scope**: OS-level sandbox escapes, external attackers not acting through the agent.

## Responsible Disclosure
If you discover a vulnerability, open a private security advisory or contact the maintainers. Do not disclose vulnerabilities publicly until resolved. Provide details, steps to reproduce, and suggested mitigations if possible.

The engine should never grant an agent more access than explicitly defined in `permissions.json`.

### 2. Isolation by Default (engine/ only)
Within the `engine/` boundary, the legacy `AgentEngine` performs no side effects (it only validates, permission-checks and logs), and the supported `SafeExecutor` path writes only to an operator-selected SQLite state root. These are application-level checks, not OS confinement: residual filesystem check/use races remain, and a separate host process with sufficient permissions can bypass this Python package. The quarantined upstream application is not covered.

### 3. Transparent Execution (engine/ only)
The engine records the audit and attempt events it implements: legacy `AgentEngine` entries for requests that pass schema validation, and durable attempt identities and append-only attempt events on the supported path. This does not guarantee that every attempted request, on every path, produces a log entry.

### 4. No Implicit Capabilities
The engine must never infer or assume permissions.  
All capabilities must be explicitly declared.

### 5. No Autonomous Expansion
The engine must not add new abilities, escalate privileges, or modify its own permissions without human intervention.

---

## Threat Model (High-Level)

**moltbot-safe** assumes the following:

### In-Scope Threats
- Malicious or buggy agent requests  
- Attempts to escape the sandbox  
- Attempts to access unauthorized files  
- Attempts to perform network operations without permission  
- Attempts to modify permissions or audit logs  
- Malformed or adversarial action schemas  

### Out-of-Scope Threats
- Host OS compromise  
- Kernel-level attacks  
- Hardware-level attacks  
- Supply-chain attacks outside this repository  

This project focuses on **application-level safety**, not full system hardening.

---

## Security Roadmap

- Add optional network sandboxing  
- Add permission scopes for subprocesses  
- Add integrity checks for audit logs  
- Add test suite for sandbox escape attempts  
- Add fuzz testing for action schema validation  

---

## Responsible Use

This project is intended to help developers build safer agentic systems.  
It should not be used to grant unrestricted system access to autonomous agents.

Use responsibly, and always review permissions before deploying.

---

Thank you for helping keep **moltbot-safe** secure.
