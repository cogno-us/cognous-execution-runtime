# Vitest restart: guard-first redesign

PR #29 now proposes static dependency-upgrade guards only. It does not upgrade
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
