# OpenShell worker image qualification checkpoint

Worker 17 bounded qualification of the optional OpenShell worker image.

## Scope and provenance

- Repository: `cogno-us/moltbot-safe`
- Accepted / starting main: `177354e959cc78c59c1a776f018cfbfbf28c927b`
- Branch: `worker17/openshell-image-qualification`
- Executor producer profile: `2.0.0`
- Execution Envelope: `0.2.0`
- OpenShell pin preserved: v0.1.2, upstream commit `6648bd0c290efbc41ba131ee9831ee45cd431f94`
- No hub or adjacent repository changes.
- No live OpenShell infrastructure was provisioned or used by this workstream.

The accepted baseline already contains the repaired destination observation contract.
Producer profile 2.0.0 requires stored destination `state` in
`destination_state`, and the OpenShell adapter checks exact effect, operation
digest, grant, target, amount, unit, payload and embedded state bindings.

## Concrete finding

The existing OpenShell tests did not execute the packaged worker process. Their
transport fake imported `engine.openshell_worker.handle()` directly. Therefore
they established worker-function and SQLite behavior, but not that the Dockerfile
actually packages a worker whose real stdin/stdout entry point satisfies the
repaired adapter contract.

No source-level worker/adapter schema mismatch was found at the accepted baseline:
`engine/openshell_worker.py` delegates to the repaired
`DurableRefundDestination`, and `examples/openshell/Dockerfile` copies the
current `engine/safe_executor.py` into the image. The required correction is
qualification coverage of the packaged process/image, not relaxation or change
of the public authorization, producer or envelope contracts.

## Changes

1. `tests/test_openshell_worker_contract.py`
   - checks the real worker handler against the current repaired observation
     validator;
   - covers applied, duplicate, conflicting effect identity, partial state and
     negative observation mutations;
   - confirms a copied operation digest does not authenticate contradictory
     content.

2. `tools/openshell_image_qualification.py`
   - invokes the exact packaged worker entry point:
     `/usr/local/bin/python3 -I /opt/cognous/openshell_worker.py`;
   - uses JSON stdin/stdout, not a mocked worker response;
   - checks fresh absence, normal commit, duplicate same-operation delivery,
     conflicting effect-ID reuse, restart observation and partial delivery;
   - validates actual worker output through
     `OpenShellRefundDestination._validate_observation`;
   - asserts exact retained SQLite rows and no replacement effect;
   - runs the image without a `--user` override to establish the configured default UID/GID 1000, writable `/var/lib/cognous`, non-writable `/opt/cognous`, and required Apache/MIT license and NOTICE files;
   - performs a separately labeled explicit `--user 1000:1000` check that is not used as evidence of the image default;
   - forwards stdin with `docker run -i` for actual worker requests and partial-state seeding.

3. `.github/workflows/openshell-image-qualification.yml`
   - sets repository-root `PYTHONPATH` for focused tests and direct qualification CLI execution;
   - explicitly checks out `github.event.pull_request.head.sha` for PR runs and verifies `git rev-parse HEAD` equals that submitted head, rather than qualifying GitHub's synthetic merge commit;
   - resolves `python:3.12-slim` to the exact pulled OCI digest and records it;
   - builds the worker image from the verified submitted head SHA;
   - records submitted head SHA, actual checkout SHA, resulting image ID and build inputs;
   - executes the packaged worker qualification and uploads JSON evidence.

The resolved Python base digest is build evidence only. It is not represented as
a pre-reviewed repository pin. The Dockerfile continues to require a digest via
`BASE_IMAGE`; no mutable base reference is passed into the build itself.

## Semantics retained

- The worker emits destination facts: `absent`, `applied`, or `partial`.
- `unknown` remains a host adapter/reconciliation state for unavailable or
  unresolved delivery; it is not fabricated as worker destination state.
- A fresh absent observation does not grant retry permission.
- Duplicate same-operation delivery must not create a second effect.
- Reusing an effect ID for different operation content must fail.
- Partial state must remain partial.
- Cancellation request, stop acknowledgement, destination observation and
  rollback remain separate.
- The documented inspection-to-execution/stop race is unchanged.
- No TypeScript-wide confinement, remote exactly-once, live OpenShell
  enforcement or production-readiness claim is introduced.

## Qualification matrix

| Boundary | Status | Evidence / limitation |
| --- | --- | --- |
| Source worker/adapter contract | Pending repaired final-head CI | Run with repository-root `PYTHONPATH`; previous run 37562115766 failed before collection because `engine` was not importable. |
| Host worker process | Unexecuted | Supported production entry point is the packaged `/opt/cognous` worker; this work does not rewrite its fixed state root for host convenience. |
| Container image build | Pending repaired final-head CI | Previous run never reached build. Repaired gate verifies submitted head before resolving base digest/building and records checkout SHA, base digest and image ID. |
| Packaged worker process | Pending repaired final-head CI | Docker now keeps stdin open with `-i`; qualification runs the image default user, not an imposed identity. |
| Adapter consumption of real worker bytes | Pending repaired final-head CI | Actual packaged stdout is validated through the accepted OpenShell observation validator. |
| Mocked OpenShell transport | Previously covered by repository tests; not redefined here | Existing tests mock only OpenShell transport and use real worker logic/SQLite. |
| Live OpenShell execution | Unexecuted | No authorized isolated live environment was available to this worker. |
| Live confinement enforcement | Unexecuted | Docker execution alone is not OpenShell/Landlock/seccomp qualification. |

## Commands

Focused source check:

```bash
PYTHONPATH=. pytest -q tests/test_openshell_worker_contract.py
```

Image qualification performed by the dedicated workflow:

```bash
docker pull python:3.12-slim
BASE_DIGEST="$(docker image inspect python:3.12-slim --format '{{index .RepoDigests 0}}')"
docker build --build-arg BASE_IMAGE="$BASE_DIGEST" \
  --label org.opencontainers.image.revision="$GITHUB_SHA" \
  -f examples/openshell/Dockerfile \
  -t cognous-refund:worker17-qualification .
PYTHONPATH=. python tools/openshell_image_qualification.py \
  --image cognous-refund:worker17-qualification \
  --output openshell-image-qualification.json
```

Relevant Python safety gate after implementation:

```bash
PYTHONPATH=".:pinned/control-plane/src" \
MOLTBOT_SAFE_CONTROL_PLANE_ROOT="pinned/control-plane" \
MOLTBOT_SAFE_MANIFEST_FIXTURE="pinned/action-manifest/examples/refund_integration_v1_1.manifest.json" \
pytest -q tests
```

That full gate is delegated to the repository's existing Python safety workflow;
this checkpoint must be updated from actual final-head CI evidence before any
claim that it passed.

## Compatibility and migration

No public schema version changes are required. Consumers remain on executor
producer profile 2.0.0 and Execution Envelope 0.2.0.

Any worker image built before the accepted observation repair must be treated as
stale and rebuilt from a source revision that includes the current
`engine/safe_executor.py`. Old image output lacking embedded destination
`state` fails closed under the current adapter. Do not weaken validation for
old images.

No OpenShell dependency upgrade, authorization change, retry permission,
reconciliation relaxation or hub pin update is part of this branch.

## Reviewed failure and targeted repair

Workflow run `37562115766` failed at focused test collection with
`ModuleNotFoundError: No module named 'engine'`. Because that step failed, base
resolution, image build, packaged worker execution and artifact recording were
all skipped. That run therefore supplies no image qualification evidence.

The targeted repair:
- provides repository-root import resolution for both pytest and direct CLI use;
- adds `docker run -i` whenever stdin is supplied;
- removes the unconditional `--user 1000:1000` override from qualification and
  establishes the Dockerfile's configured default identity separately;
- explicitly checks out and verifies the submitted PR head SHA instead of
  implicitly testing the pull-request merge ref.

## Residual work

Final-head CI must establish whether the Docker image actually builds and passes
the packaged worker checks. If the dedicated build fails, preserve the failure
as qualification evidence and correct only a narrowly scoped worker/image
packaging defect. A broader adapter or contract change is out of scope and must
be reported with the smallest reproduction instead.

Live OpenShell execution and confinement remain separately unverified.
