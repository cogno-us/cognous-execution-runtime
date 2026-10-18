# Internal Module Guide

This document explains the responsibilities and invariants of the legacy compatibility modules. The supported execution path is described in [execution-boundary.md](execution-boundary.md).

## engine/engine.py
- Legacy shell: validates the action, checks the caller-asserted agent identity against the permissions file and logs the request. It performs no side effect and has no sandbox.
- Invariant: it never reports successful execution (`status: unsupported`).

## engine/permissions.py
- Loads permissions from permissions.json.
- Invariant: denies an action type not listed for the agent. No wildcards or implicit grants.

## engine/action_schema.py
- Validates actions against a schema.
- Invariants: rejects malformed or extra fields. Accepts any non-empty `type` string; the permission check decides which types are allowed.

## engine/audit.py
- Appends requests that reach the audit step to the audit log with timestamp and metadata.
- The log is append-by-convention (the file is opened in append mode); it is not tamper-evident, and coverage is not a guarantee that every request on every path is logged (see SECURITY.md).

## Extending Safely
- Add new action types by updating the schema and permission logic.
- Never add implicit permissions.
