# Persistent OpenAPPA flow and authority qualification

> Historical workstream note. See the [current OpenAPPA status](https://github.com/cogno-us/cognous-stack-qualification/blob/f99bb7591f52ca5a26d64a50d9ef466b4749ad4e/suites/cognous-execution-runtime/tools/openappa/README.md)
> for consolidated evidence, current scope and remaining work.

Candidate based on Runtime `ac309e43a95cb163762f2f6d317e3bcec913ad30`.
The conjunction workflow keeps its existing upstream pins: OpenAPPA
`10aa0e09e32d8c6704bdb030dc3c66140bf81915`, Control Plane
`2ea9528eeb87e14ff10f05de06473122b9df540f`, and Manifest
`46c950bed37fe3812000895430bc0312d29e37ce`.

The earlier conjunction used a fresh in-memory flow runtime for every request.
This additional fixture bootstraps one SQLite trajectory, optionally accumulates
a restriction in another process, and checks the next request in a third process
using the existing-only opener and `try_status`. It does not call SessionStart
on the decision path. The actual OpenAPPA decision is correlated to the exact
request bytes before the existing executor revalidates institutional authority.
Tests inspect the actual synthetic SQLite destination and effect count.

The fixture explicitly maps `host/cognous_fixture/refund` to the trajectory
fixture's `send_public` sink while retaining native operation arguments. This
mapping tests the conjunction with an accumulated audience restriction; it is
not a general declaration that refunds are public egress or a production adapter.
The same policy and root allow the clean history and deny the restricted history.
No caller-provided allow/deny policy selector exists in the persisted check path.

## Cases and qualification

Eight additional cases cover the four combinations of clean/restricted history
and valid/revoked authority; revocation after resumed flow allows; and missing,
corrupt or mismatched-root state. Restricted cases repeat the check in another
process and assert that the destination remains absent. Missing and corrupt files
must not be recreated or replaced. The existing 20 conjunction tests remain,
with a strict hosted total of 28 and no skipped, failed or errored cases.

Rust execution requires hosted CI in the current development environment.
Local Python collection and lint do not establish actual runtime success.
Current candidate results are recorded in the pull request after log inspection.

## Remaining integration boundary

This is a test-owned host and fixture, not a listening service. The kernel-UID
resume helper is separately qualified and is not connected by these tests.
Authenticated host mapping, lifecycle serialization, outcome acknowledgement
back into OpenAPPA, policy lifecycle, crash recovery, rollback protection and
production admission/output interception remain separate work. A flow decision
is neither an institutional grant nor a reusable permit; ordinary process reopen
is not a power-loss guarantee. No real settlement or production effect occurs.

No selected lock or default runtime changes. Preserve C0 check-to-commit limits,
optional same-host C1, no C2/C3 and operational #30 HOLD.
