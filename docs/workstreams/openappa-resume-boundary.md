# OpenAPPA existing-trajectory lookup boundary

> Historical workstream note. See the [current OpenAPPA status](../../tools/openappa/README.md)
> for consolidated evidence, current scope and remaining work.

Candidate against Runtime `19c46ba1974867ec9b990aba9081d0840ea92424` and upstream
`10aa0e09e32d8c6704bdb030dc3c66140bf81915`. No selected lock changes.

The pinned `Runtime::try_status` API reports unknown roots separately from store,
policy and replay errors. Unlike a session-start event, it appends no trajectory
event. This is a prerequisite for a resume path that refuses to silently create
a replacement root. It is a status read, not an authorization check.

Two additional real-runtime tests extend the three persistence cases:

- Repeated lookup of an unknown root must return `UnknownRoot`, leave that root
  absent from the actual event store, preserve the existing trajectory's audit
  length, and retain its public-send denial.
- An unrelated database containing a different root must not satisfy the
  expected-root lookup or create that root. The unrelated root's audit length
  must remain unchanged; reopening the original store must still deny public send.

All five tests await current-head hosted execution. Earlier three-case success
does not establish the additional assertions. No host resume implementation is
added by this test change.

## Required host contract before adapter implementation

Runtime tenant fields and authority consistency checks do not authenticate a
caller. The reviewed OpenAPPA harness receives synthetic host context from its
test owner; it has no accepted external authentication bridge. Do not promote
that dictionary, a caller-supplied tenant ID, or an `authenticated` boolean into
proof of identity.

The future host must obtain identity through its accepted authentication boundary
and resolve a previously provisioned association in a host-controlled store:

| Input | Required source and behavior |
| --- | --- |
| Tenant and principal | Verified host session; no model-supplied replacement |
| Root and store association | Durable host-owned record scoped to that identity; resume cannot enroll |
| Store access | Existing expected store; missing, malformed, or inaccessible state refuses resume |
| Root lookup | Actual `try_status` succeeds for the associated root; every error refuses resume |
| Runtime continuation | Reuse that root and store; never recover by minting a fresh unrestricted root |
| New enrollment or recovery | Separate explicit host operation and evidence; never an automatic retry |

A matching root name alone does not detect a copied database or rollback. The
association needs a separately defined store identity and continuity/rollback
contract. File-existence checks also leave a path-replacement race and must not
be presented as atomic store binding. These controls are not implemented here.

The tests intentionally open an unrelated existing database to inspect lookup
behavior. They do not prove fail-closed handling of a missing database path:
`LogStore::open(Backend::Sqlite)` can create a database. A production resume
adapter must prevent that creation through its supported store-access contract.

No deployment authentication, missing-file guard, crash recovery, credential
custody, output-admission or production tenant-isolation claim. Preserve C0
check-to-commit limits, optional same-host C1, no C2/C3, no real settlement and
operational #30 HOLD. The existing fresh-store conjunction probe remains test-only.
