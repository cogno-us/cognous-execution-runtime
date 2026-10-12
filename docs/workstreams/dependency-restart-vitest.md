# Vitest restart: guard-first redesign

PR #29 proposes dependency-upgrade guards and narrowly scoped validation/test-harness repairs. It does not upgrade
Vitest. Root and UI manifests and pnpm-lock.yaml retain the accepted main
baseline at 6dc7ad10cde3ca7559eec55727c90c4cd2fd58f3; UI Vitest and its
browser-playwright adapter both remain 4.0.18. This is not a security clearance
or a claim that the baseline is current.

## Verified failure

The earlier head bac59657cf5734dad01d753ad893c64255f8101a changed only the UI
Vitest requirement to 4.1.11. The lockfile and browser adapter retained 4.0.18.
GitHub CI run 37707336373, install-check job 113084811514, failed during frozen
installation with ERR_PNPM_OUTDATED_LOCKFILE. Static UI preflight in run
37707336423 independently detected the mismatch. These were deterministic
input failures, not stalled tests.

The separate local dependency-install policy rejection of an existing libsignal
Git dependency was not the cause of this GitHub failure. That policy remains
intact; no override or bypass is introduced.

## Revised process

Independent root, UI, graph, and regression jobs run on Linux, with three-minute
job limits and one-minute parser-install/check steps. No application dependency
installation is needed for these static checks. Regression tests reject a
manifest-only upgrade, relabeled specifiers with stale resolved versions,
missing importer/transitive snapshots, missing package records, and malformed
input. Alias and workspace-link behavior is retained.

The historical guard-only scope classification skipped application jobs. That
was not evidence that the repository landing gates passed. The added
`dependency-guard-application.yml` now runs actual `pnpm lint`, `pnpm build` and
`pnpm test` for guard changes, each after frozen installation. The aggregate
requires success from every matrix entry; skipped/cancelled/failed jobs cannot
pass it. Existing application CI, safety, install-smoke and CodeQL workflows
remain enabled. The new workflow path is not exempt from existing CI scope,
so this repair also triggers the existing application matrix.

Static checks establish manifest/lock consistency and recorded graph closure,
not package provenance, workspace target existence, semver-range satisfaction,
peer compatibility, successful installation, or runtime behavior.

## Executed local validation

- Eight regression tests passed.
- Root, UI, and graph preflight batches passed against the unchanged baseline.
- Workflow YAML parsing and application-scope assertions passed, including
  mixed guard/UI-manifest changes and lockfile changes.
- Final-head GitHub CI must be evaluated separately; local results are not a
  substitute for that evidence.

## Deferred upgrade acceptance

A future Vitest version upgrade must coordinate Vitest, the browser adapter,
and any coupled workspace dependencies using an approved dependency source.
It must include a coherent generated lockfile, pass frozen installation, and
complete bounded UI unit/browser runtime batches before acceptance. Do not
manufacture integrity values, relax installation policy, remove failing runtime
coverage, or advance accepted hub pins to make that upgrade appear complete.

## Required validation repair

Starting PR head: `78f28aa41532d785b00fda456497d56256e1b8b0`.
The new Linux workflow preserves the literal repository test command, including
unit, extensions and gateway groups from `scripts/test-parallel.mjs`; no test
config, manifest, lockfile or failure-suppression setting is changed. Jobs have
25-minute limits, frozen install has eight minutes, and each actual gate has
twelve minutes. At most two matrix jobs run simultaneously; each test child
has two workers. Gate logs and source/manifest identities are retained.

The local `pnpm lint`, `pnpm build`, and `pnpm test` attempts on this repair
checkout could not reach application execution: automatic dependency setup is
blocked by `ERR_PNPM_EXOTIC_SUBDEP` for the existing `libsignal` Git
subdependency. No local dependency safeguard was changed. The repository's
AGENTS.md local-before-commit landing requirement is therefore **unsatisfied**.
CI evidence, when available, is additional evidence and is not represented as
satisfying that local instruction. This validation-only branch update is not
a landing commit or merge. A maintainer with an approved dependency environment
must run the local landing gate before merging.

The eight static regression tests and three preflight batches remain separate
from application validation. Pending workflow jobs and intentionally skipped
individual test cases are never counted as executed passing tests.

## Shared Windows test harness repair

The full application matrix also reaches the same Windows termination and
temporary-directory tests repaired for PRs #30 and #31. The termination test
previously re-raised real signals into the Vitest worker. This follow-up uses
a `process.kill` spy and asserts the exact re-raise call (or its absence), while
still checking lock-file removal and restoring listeners. It also gives the
embedded runner temporary-directory cleanup five bounded retries; persistent
cleanup failures still fail. Production signal behavior remains unchanged.

The two test files and Windows qualification workflow are byte-identical to
the sibling dependency repairs. The workflow runs all session-lock cases then
the original Windows shard without exclusions or ignored worker failures.
Local application test execution remains blocked as documented above. Prior
head `589fae01b7958e39e055f0f49e858e96e2873d69` passed actual Linux lint
and build; its test results are historical after this follow-up. Final-head
Linux and Windows validation must run again before any acceptance.

## A2UI prerequisite repair — 2026-10-12 UTC

At head `a7b8a8325671629593d4da84c6f071c85297829e`, Linux application
job 113111340004 failed only `serves the gateway-hosted A2UI scaffold`
(HTTP 503 instead of 200; 4817 other tests passed in that test population).
macOS shard job 113122144792 failed the same assertion (1541 other tests
passed in that population). The source resolver returns 503 when the generated
bundle is absent. The independent matrix build job does not share its checkout
with the test job.

The Linux test job now builds the bundle in its own checkout, requires a
nonempty output, and retains the build log before executing the unchanged
`pnpm test` command. The macOS shard commands receive the same prerequisite
already present on accepted main. No test assertion, case, failure handling or
required aggregate is removed. Dependency manifests and lockfile are unchanged.

Separate remaining blockers: job 113127128112 reported SwiftFormat failures
on 143 of 202 files; this repair does not reformat that unrelated tree. A local
trial merge of current main also exposed add/add conflicts in
`.github/workflows/dependency-restart.yml` and
`tools/dependency_restart_preflight.py`; it was aborted without resolving by
discarding either side. This narrow repair does not claim merge readiness.

Full local application gates and new-head CI must be recorded separately.
Structural workflow checks are not evidence that the generated bundle or
application tests passed. No dependency policy exception, runtime change,
release-lock advancement or production authorization is introduced.

## Main reconciliation — 2026-10-12 UTC

Merged accepted main `8e7d634cf0a22b6f3c1fc6d93afe92c7414fa243` into
the candidate. Both add/add conflicts were resolved by retaining the candidate's
strict guard API, exact-resolution/importer checks and eight regression cases,
which extend the simpler main guard. The dependency workflow keeps all three
main preflight batches and adds its bounded regression job/triggers.

Main already contains the Swift formatting and macOS A2UI prerequisites, so
those accepted changes are now inherited instead of repaired again. The
Windows harness files and all runtime/dependency files match main. The net
diff from main is seven guard, workflow, changelog and documentation paths.

Validation on the reconciled working tree: eight regressions and root/UI/graph
preflight pass. New-head CI and full local application gates remain separate
requirements; this reconciliation does not count earlier failing jobs as passes.

Local application checkpoint on the reconciled tree: frozen installation with
pnpm 10.23.0 succeeded. A task-local Corepack shim kept nested pnpm invocations
on that version. `pnpm lint` passed (2517 files, zero warnings/errors), and
`pnpm build` passed including generation of the A2UI bundle. The targeted canvas
host suite passed all seven tests, including the previously failing scaffold.

Full local tests remain unqualified. The first attempt exhausted Node's default
2 GB heap and was stopped. A retry with CI's 4 GB heap and one worker reached
security-audit failures; a focused run confirmed 30 passing and 10 failing tests,
all failing in `os.networkInterfaces()` with `uv_interface_addresses` error 1.
A standalone Node call reproduced that environment restriction without any
repository code. The full retry was then stopped; neither partial run is counted
as a completed suite. No mock, exclusion or production change was introduced to
bypass the restriction. This remains a draft integration repair; AGENTS.md's
full local landing gate must still pass in a suitable environment before merge.

## Hosted full-suite budget correction

At head 9f279edc9eb0c7cd6644bd4eac715e2195c6fefc, main CI run
38201706575 succeeded. Dedicated application run 38201706482 passed lint
and build, but test job 114656328934 reached its 12-minute step timeout
while test files were still completing. Its log contains no assertion-failure
or fatal-error marker; this is not a completed test-suite pass.

Increase only the test step budget to 30 minutes (lint/build remain 12),
and the enclosing job budget to 45 minutes to accommodate installation and
artifact retention. Preserve two workers, 4 GB heap, the original full test
command, A2UI prerequisite, fail-fast behavior and all-success aggregate.
No exclusions or assertions change. The local full-suite environment blocker
and separate landing requirement remain unchanged.

## Post-PR37 main reconciliation

Integrated main 31beca6e90dfd775bb177115ebec481ede3abd95 after the accepted
music-metadata update. The only merge conflict was two independent changelog
entries; both are retained. The narrowed lock and its qualification note are
inherited unchanged from main. Guard tests and root/UI/graph checks must cover
this combined tree; earlier hosted full-suite results belong to 45ea97d and
are not represented as new-head qualification. No tests or gates are bypassed.
