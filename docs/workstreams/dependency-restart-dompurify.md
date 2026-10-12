# dompurify dependency restart

Draft replacement for executor PR #6. Base: 6dc7ad10cde3ca7559eec55727c90c4cd2fd58f3.

## Scope

Select DOMPurify 3.4.16 in the UI importer and add only its package/snapshot records from the prior Dependabot candidate.

Existing unrelated package resolutions and snapshot contents are preserved. Older package entries are retained where present; pruning must follow a reviewed package-manager regeneration. No full install or registry-artifact verification is implied by copying previously proposed integrity metadata.

## Validation and blockers

All three static preflight batches pass. Frozen installation and affected runtime tests remain unexecuted; static success is not acceptance.

The dedicated Ubuntu workflow runs independent root-manifest, UI-manifest and lock-graph checks with fail-fast disabled and a three-minute timeout per job. Its only added dependency is a pinned YAML parser. It does not install or execute application packages. Existing application/security workflows remain enabled; this preflight does not replace them or certify other operating systems.

Local application installation was previously blocked by blockExoticSubdeps for the existing libsignal Git dependency. This restart does not disable that policy, add an exception, change that dependency, or claim an install/test pass. Resolve the dependency provenance/policy question through a separately reviewed approved path before application qualification.

## Next bounded validation

1. Review the minimal lock projection and regenerate/prune with the repository-pinned package manager in an approved environment.
2. Complete a frozen install without weakening dependency safeguards; retain the exact command and source head.
3. Run the existing UI Markdown sanitizer and chat-rendering browser tests in bounded Linux batches; Vitest changes also require the remaining UI suite.
4. Record install/runtime evidence and any other-platform gaps before requesting acceptance. Keep this PR draft until those gates pass.

No Python execution contract, accepted hub pin, authority mechanism or production deployment changes.
