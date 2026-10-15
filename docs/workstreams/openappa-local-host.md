# Opt-in Linux local-host resume candidate

> Historical workstream note. See the [current OpenAPPA status](https://github.com/cogno-us/cognous-stack-qualification/blob/43fb81dc3ec0da17402bef274dcec1831cc5e160/suites/cognous-execution-runtime/tools/openappa/README.md)
> for consolidated evidence, current scope and remaining work.

Base Runtime `a26a1e29b7a6ef67dba5ebd45de977c1b1c81b47`; upstream OpenAPPA
`10aa0e09e32d8c6704bdb030dc3c66140bf81915`. No selected-lock advancement.

`tools/openappa/local_resume.py` implements a non-authorizing local-host binding
helper. A connected Unix socket supplies kernel `SO_PEERCRED`; the UID selects
a durable host-owned association. Callers supply only an opaque session ID,
never a replacement tenant/principal. Explicit operator provisioning binds a UID
to one tenant/principal and sessions to root, absolute store path, device and
inode. Resume cannot enroll, recreate the registry, or silently rebind a session.

This is authentication of a local OS account under an operator-configured
identity mapping, not human authentication or a remote identity provider. A UID
cannot serve multiple tenant/principal identities in this profile. Distinct
tenants therefore need distinct OS accounts. Host code, provisioning authority
and the private directory must be trusted; hostile processes sharing the host
UID are outside this boundary.

Registry reads use SQLite `mode=ro`. State files must be regular, non-symlinked,
host-owned and private (no group/other permission bits), within a private
host-owned directory. Store identity is checked against enrollment and checked
again after the trusted reader returns; descriptors remain open during the read.
Missing files, mismatched identity and invalid registry state refuse resume.
Device/inode checks detect ordinary replacement, not rollback of file contents.

The reader callback is trusted host code and returns status only. The actual
integration probe opens the pinned upstream store using SQLite URI `mode=rw`,
which requests an existing database without CREATE, calls `try_status`, and
checks that the synthetic root's accumulated public-send denial remains intact.
No resume path calls SessionStart or creates a root. Explicit fixture bootstrap
is separate and accepts narrowing consent only; it is not exposed by the helper.

## Qualification

Eighteen candidate tests cover kernel-derived identity, registry reopen, unknown
session/UID, missing registry/store, replacement/symlink/permissions, corrupt
registry, conflicting tenant enrollment, implicit rebind, mid-read replacement,
non-Unix/unconnected sockets, two actual process resumes, missing-file
noncreation by the real upstream opener, and malformed-store nonreplacement.
The workflow requires all 18 cases, no skips, and retains JUnit.

Local execution: 14 tests passed; the unconnected Unix-socket test failed before
calling the helper because this environment denied AF_UNIX socket creation.
That test remains unchanged for hosted CI. Three real-runtime cases require
the hosted Rust compiler; no pass is claimed before their logs are inspected.

## Limits before any deployment

This helper is opt-in tooling, not a listening service or default execution
adapter. The Rust reader is a synthetic fixture probe, not a general deployment
bridge. It does not replace the independent institutional authorization check
qualified in PR #50, nor turn trajectory existence into a flow allowance.

File-path checks are not atomic protection against a malicious same-UID writer.
The supported boundary assumes the private directory has one trusted owner;
host compromise, privileged adversaries, backup restoration, inode reuse,
content rollback, distributed filesystems, remote authentication, revocation of
OS identity mappings and cross-process lifecycle coordination are unqualified.
Recovery/enrollment remains an explicit operator action, not automatic retry.
No credential is minted, no institutional grant is issued and no destination
effect runs through this helper. Production admission/output interception and
durable root continuity across hostile rollback remain separate requirements.

Preserve C0 check-to-commit limits, optional same-host C1, no C2/C3, no real
settlement and operational #30 HOLD. A successful local-host fixture does not
authorize deployment or claim enterprise tenant isolation.

## Enrollment transaction follow-up

The initial implementation did not reserve a SQLite write transaction before
reading the existing UID mapping. Concurrent enrollment of different sessions
could therefore read the same empty mapping before either inserted. Enrollment
now uses `BEGIN IMMEDIATE` across lookup and insert. Resume reads the full UID
identity set and session in one read transaction and refuses inconsistent older
registries rather than choosing a session-specific identity.

Three new cases use independent real SQLite connections: six concurrent
conflicting enrollments must leave one identity, six same-identity sessions must
all succeed, and a deliberately inconsistent older registry must refuse resume.
The concurrency test delays actual reads to expose scheduling windows without
substituting the database. The hosted gate now requires all 21 cases with no
skips. Current-head hosted results remain required before this follow-up lands.


## Non-regular state files

State descriptors are opened with `O_NONBLOCK` before checking their file type,
so a named pipe cannot wait for a writer before the regular-file check runs.
Both enrollment and resume refuse named pipes in registry and trajectory paths;
four subprocess-bounded cases cover these paths. Regular SQLite files retain
the same read, ownership, permission and identity checks. The hosted gate now
requires all 25 cases without skips.
