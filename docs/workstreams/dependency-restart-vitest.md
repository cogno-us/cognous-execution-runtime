# vitest dependency restart

Draft replacement for executor PR #3. Base: 6dc7ad10cde3ca7559eec55727c90c4cd2fd58f3.

## Scope

Retain the proposed Vitest 4.1.11 manifest change as an explicitly incomplete draft; do not fabricate a regenerated lock or assume browser-adapter compatibility.

Existing unrelated package resolutions and snapshot contents are preserved. Older package entries are retained where present; pruning must follow a reviewed package-manager regeneration. No full install or registry-artifact verification is implied by copying previously proposed integrity metadata.

## Validation and blockers

UI preflight fails on the retained candidate: Vitest 4.1.11 disagrees with the 4.0.18 lockfile and browser adapter. Generate and review a coherent coordinated lock before any install or merge.

The dedicated Ubuntu workflow runs independent root-manifest, UI-manifest and lock-graph checks with fail-fast disabled and a three-minute timeout per job. Its only added dependency is a pinned YAML parser. It does not install or execute application packages. Existing application/security workflows remain enabled; this preflight does not replace them or certify other operating systems.

Local application installation was previously blocked by blockExoticSubdeps for the existing libsignal Git dependency. This restart does not disable that policy, add an exception, change that dependency, or claim an install/test pass. Resolve the dependency provenance/policy question through a separately reviewed approved path before application qualification.

## Next bounded validation

1. Resolve the UI manifest/browser-adapter/lock mismatch; rerun static preflight.
2. Complete a frozen install without weakening dependency safeguards; retain the exact command and source head.
3. Run the existing UI Markdown sanitizer and chat-rendering browser tests in bounded Linux batches; Vitest changes also require the remaining UI suite.
4. Record install/runtime evidence and any other-platform gaps before requesting acceptance. Keep this PR draft until those gates pass.

No Python execution contract, accepted hub pin, authority mechanism or production deployment changes.
