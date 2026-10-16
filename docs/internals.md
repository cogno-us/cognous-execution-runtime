# Internal Module Guide

This document explains the responsibilities, invariants, and safety constraints of the core modules in Cognous Execution Runtime.

## engine/engine.py
- Orchestrates action validation, permission checks, sandboxed execution, and audit logging.
- Invariants: No action is executed without passing validation and permission checks. All results are logged.
- Safety: Never bypasses the sandbox. No dynamic code execution.

## engine/permissions.py
- Loads and enforces least-privilege permissions from permissions.json.
- Invariants: Denies any action not explicitly allowed. No wildcards or implicit grants.
- Safety: All permission logic is explicit and auditable.

## engine/action_schema.py
- Validates actions against a strict schema.
- Invariants: Rejects malformed, ambiguous, or extra fields. Only known action types allowed.
- Safety: No implicit or default behaviors. All validation is explicit.

## engine/audit.py
- Appends every attempted action (allowed or denied) to audit.log with timestamp and metadata.
- Invariants: No action occurs without a log entry. Log is append-only.
- Safety: No log truncation or overwriting. Log format is human-readable and machine-parseable.

## Extending Safely
- Add new action types by updating the schema and permission logic.
- Never add implicit permissions or bypass sandboxing.
- All extensions must maintain minimalism, explicitness, isolation, least privilege, transparency, predictability, and auditability.
