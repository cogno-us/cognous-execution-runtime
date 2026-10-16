# Security Policy

This policy covers the Cognous Python execution boundary under `engine/` only. The upstream Moltbot TypeScript/native application is **quarantined**: it is preserved only at the tag `upstream-moltbot-quarantine-20261016`, is not shipped on `main`, is not maintained or reviewed, and carries 18 known unrepaired security findings. See [docs/quarantine-upstream-moltbot-20261016.md](docs/quarantine-upstream-moltbot-20261016.md). Do not deploy that application as part of the Cognous stack.

## Security Posture (engine/ boundary only)
- **Least Privilege**: The legacy `AgentEngine` checks requests against explicit `permissions.json` entries; the supported `SafeExecutor` path applies exact local restrictions on institution, authority domain, adapter, action, target prefix, unit, amount and effect count.
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

### 2. Isolation by Default
All agent actions must occur inside the sandbox directory.  
No direct filesystem access outside the sandbox is permitted.

### 3. Transparent Execution
Every attempted action—allowed or denied—must be logged in `audit.log`.

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
