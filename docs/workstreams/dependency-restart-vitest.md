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

Exact guard-only paths are classified as tooling in the existing application CI
scope calculation. Package manifests, the lockfile, application source, unknown
paths, and mixed tooling/application changes still require application checks.
Existing safety, install-smoke, and CodeQL workflows are not disabled.

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
