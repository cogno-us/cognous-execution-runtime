# Local host binding through persistent flow and authority

> Historical workstream note. See the [current OpenAPPA status](https://github.com/cogno-us/cognous-stack-qualification/blob/f99bb7591f52ca5a26d64a50d9ef466b4749ad4e/suites/cognous-execution-runtime/tools/openappa/README.md)
> for consolidated evidence, current scope and remaining work.

Candidate based on Runtime `8068e9e67c3f76bd6b87876499704c87cd0a446b`.
The conjunction workflow retains OpenAPPA
`10aa0e09e32d8c6704bdb030dc3c66140bf81915`, Control Plane
`2ea9528eeb87e14ff10f05de06473122b9df540f`, and Manifest
`46c950bed37fe3812000895430bc0312d29e37ce`.

`local_join_harness.py` connects the separately qualified local resume and
persistent conjunction fixtures. It snapshots the request and trusted host
context, obtains a binding through the connected Unix socket's kernel UID,
and performs a status-only read of the enrolled existing root. Tenant,
principal and root must match the host context before flow is checked.
The trusted flow callback receives the persisted binding, including its store
path, rather than a separately supplied store selector. The native executor
then revalidates institutional authority and inspects the synthetic destination.

The Rust fixture adds a status-only mode using the actual `try_status` API.
It opens existing SQLite without CREATE and never starts a session on resume.
Status success does not allow an effect; an independent correlated flow response
and the native authority check are still required. Missing or inconsistent host
state returns `host-refused`; a failed reader process returns `host-unavailable`.
Both stop before the flow callback and native executor.

## Qualification

Thirteen connected cases cover all four combinations of clean/restricted
trajectory and valid/revoked authority; mismatched tenant, principal and root;
other UID, unknown session, missing registry, missing or corrupt store; and
revocation during the status read. Tests use real kernel socket-pair credentials,
actual pinned Rust process reads/decisions, the real native executor, and SQLite
destination state/count assertions. The strict conjunction gate retains the
previous 28 cases and requires all 41 with no skips, failures or errors.

Local collection and lint do not qualify the Rust runtime. Hosted current-head
results and any failures are recorded in the pull request after log inspection.

## Limits

This is opt-in synthetic harness code, not a listener, public API, deployment
adapter or credential issuer. Its refund-to-public-sink mapping is test-owned.
The operator configures one tenant/principal per UID; this authenticates a local
OS account, not a remote user. Tenant labels are not institutional grants.
Host configuration, callbacks and the private directory owner remain trusted.
Hostile same-UID processes, content rollback, lifecycle serialization and changes
between status, flow check and effect remain outside the qualified boundary.
The helper does not close that race or acknowledge execution outcomes back into
OpenAPPA. Production admission/output interception and recovery remain separate.

No default runtime or selected component lock changes. Preserve C0
check-to-commit limits, optional same-host C1, no C2/C3, no real settlement and
operational #30 HOLD.
