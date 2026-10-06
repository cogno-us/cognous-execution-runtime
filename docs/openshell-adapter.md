# Optional OpenShell synthetic execution adapter

Adapter **0.1.0**, Execution Envelope **0.2.0**. Starting Moltbot Safe commit:
`4ed7c9b9fbc8159be70d68f2ca9bd8a8ddc012e3`. This adds the accepted licensing
update to baseline `6b0ba1185bcd390f71df947dda349415e4105f5f`; execution code
was unchanged between those commits.

## Ownership and supported path

The existing Python local destination remains the default. Explicitly construct
`OpenShellRefundDestination` and pass it to `PinnedControlPlaneExecutor` to opt
in. The existing `ControlPlaneRefundDestinationAdapter` still invokes this
path only inside `BoundedAuthorizationWorkflow.execute()` after current grant,
approval, policy and evidence revalidation. No authorization flag is accepted.
The retained TypeScript application and direct host/CLI invocations are outside
this boundary. This does not confine every repository invocation.

Control Plane owns institutional authorization. The executor owns exact-operation
binding, stricter local policy, dispatch intent, attempts and reconciliation.
OpenShell owns the configured execution environment. Launch, exit status and
stop acknowledgements do not establish delivery or institutional authority.

The synthetic effect is a refund row in `/var/lib/cognous/refunds.sqlite3`
**inside a dedicated retained sandbox**. It is not a real payment. Host SQLite
stores bindings, attempts and environment evidence, not the refund itself.
Observation executes the fixed read operation against that same destination.
It is authoritative within this trusted synthetic destination, not independently
verified delivery by an institution.

## Verified upstream interface

Canonical upstream: https://github.com/NVIDIA/OpenShell. Integration pin:
[v0.1.2](https://github.com/NVIDIA/OpenShell/releases/tag/v0.1.2), commit
`6648bd0c290efbc41ba131ee9831ee45cd431f94`. NVIDIA's implementation is
[Apache-2.0](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/LICENSE).
No NVIDIA implementation is copied into this adapter. Retained Moltbot MIT
notices and Cognous Apache-2.0 scope remain intact.

Official source files inspected at that pin:

- [Support matrix](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/docs/about/support-matrix.mdx): Linux amd64/arm64, Apple Silicon, experimental WSL2; Docker 28+, Podman 5, Kubernetes or VM prerequisites. Landlock ABI >=3 and qualified seccomp/task-memory operations are required; kernel version alone is insufficient.
- [Installation](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/docs/about/installation.mdx): `OPENSHELL_VERSION=v0.1.2` pins installer artifacts; gateway installation is an operator task, not performed by this adapter.
- [Sandbox lifecycle](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/docs/how-it-works/sandboxes/overview.mdx): create, exec, stop/start, delete; CPU/memory limits apply on Docker/Podman, not VM. Upload cannot accompany a main command. This adapter bakes code into an image.
- [CLI definitions](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-cli/src/main.rs) and [implementation](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-cli/src/run.rs): fixed `sandbox exec --name ... --workdir ... --timeout ... --no-tty --no-login-shell --env BASH_ENV=/dev/null -- <argv>`; stdin carries JSON. `sandbox_detail_to_json` and `sandbox_to_json` define ID, phase, admitted config, policy and version inspection.
- [Gateway implementation](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-cli/src/commands/gateway.rs): structured runtime version/driver metadata.
- [Policy schema](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/docs/how-it-works/policies/schema.mdx) and [updates](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/docs/how-it-works/policies/manage-policies.mdx): filesystem/process startup settings, live network policy; effective policy includes provider additions/global policy. Hard Landlock requirement fails launch rather than silently dropping enforcement.
- [Logging](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/docs/observability/accessing-logs.mdx): runtime logs are diagnostic enforcement evidence, not refund receipts.

These are source-verified capabilities. Live behavior in this execution
environment remains **unverified**: no OpenShell CLI, Docker, Podman or `/dev/kvm`
is available. No global security settings were changed and no sandbox was launched.

## Selected restrictions

Only the dedicated **local Docker** profile is supported, despite upstream's
broader matrix. `gateway info` must report healthy version `0.1.2`, local
loopback server and only the Docker driver. The CLI binary SHA-256 is pinned
and checked before each call. Use verified upstream artifacts; the provisioner
records the installed binary hash but cannot establish its supply-chain provenance.

The profile uses one CPU and 256Mi memory, nonroot UID/GID 1000, hard Landlock,
read-only runtime/code directories and write access only to `/var/lib/cognous`.
It grants no network endpoints, providers, credential references, host mounts,
Docker socket, service exposure or extra driver settings. The image must contain
no secrets. Gateway transport credentials stay in a dedicated host client HOME;
they are not passed into the sandbox. Only the worker's constant argv is used.
No payload or target becomes shell text. `--no-login-shell`, `BASH_ENV=/dev/null`
and `/usr/bin/env -i` prevent image login files/inherited variables from changing
the worker command. These are not defenses against a malicious host or image.

The remote exec timeout is 1–30 seconds (15 by default); host CLI timeout adds
15 seconds for transport. A timeout is not proof the process stopped. Mandatory
controls outside this fixed profile are unsupported; do not reinterpret an
unsupported requirement as optional. No credentials or network use are supported.

## Provision and run a live qualification

On a separate supported test host, install and verify OpenShell v0.1.2 and a
Docker 28+ gateway using the pinned upstream installation instructions. Do not
weaken seccomp, Landlock or global host security. Give the gateway exclusive
management ownership for this pilot. Use a dedicated client HOME and workspace.
The adapter does not install or start the gateway and does not auto-select one.

Build `examples/openshell/Dockerfile` from repository root with a reviewed
Python 3.12 slim **digest**, then make its resulting immutable OCI digest
available to the local gateway. No public push is required; use your local
image supply workflow. Build-time dependencies and base image provenance are
operator responsibilities. Do not substitute a mutable tag in the provisioner.

```bash
docker build --build-arg BASE_IMAGE="$REVIEWED_PYTHON_DIGEST" \
  -f examples/openshell/Dockerfile -t cognous-refund:qualification .
python examples/openshell/provision.py \
  --binary "$PINNED_OPENSHELL_BINARY" --home "$SYNTHETIC_CLIENT_HOME" \
  --gateway local --name refund-qualification \
  --image "$REVIEWED_WORKER_OCI_DIGEST" --output /tmp/refund-config.json
```

The helper uses the official `sandbox create` interface with a retained
`/bin/sleep infinity` main process, explicit policy and resource limits. It
records the creation request, response, observed sandbox UUID, effective policy,
admission revision, gateway runtime and config digest. No effect is executed
by provisioning. A failed/uncertain create must be inspected; never retry or
recreate a lost destination automatically. The get interface does not return
image/resource configuration, so these are grounded in the trusted creation
receipt, **not independent runtime attestation**. Preserve that receipt.

Load config JSON into `OpenShellConfig`, converting its `executable` list to a
tuple. At the trusted decision-receipt boundary, call
`destination.bind(snapshot_envelope(envelope))`. This durably binds the exact
operation, decision, effect and configuration. Then pass the destination to the
existing `PinnedControlPlaneExecutor`. Do not expose `bind()` as an untrusted
configuration-registration service. Execution still requires actual current
Control Plane revalidation; the binding does not create authority.

```bash
export MOLTBOT_SAFE_OPENSHELL_CONFIG=/tmp/refund-config.json
export MOLTBOT_SAFE_OPENSHELL_BINARY="$PINNED_OPENSHELL_BINARY"
export MOLTBOT_SAFE_OPENSHELL_HOME="$SYNTHETIC_CLIENT_HOME"
PYTHONPATH=.:pinned/control-plane/src \
MOLTBOT_SAFE_CONTROL_PLANE_ROOT=pinned/control-plane \
MOLTBOT_SAFE_MANIFEST_FIXTURE=pinned/action-manifest/examples/refund_integration_v1_1.manifest.json \
pytest -q -s tests/test_openshell_live.py
```

Use the pinned Control Plane and Manifest commits from README. The live tests
run a synthetic authorized refund, inspect exact destination content and
reconcile after recreating the host client object. The second test exercises
permitted writes, prohibited `/tmp` writes and a prohibited network connection.
Only EACCES/EPERM is accepted as a network denial; timeouts/DNS failures are not
proof. Retain matching OpenShell denial logs as deployment qualification evidence.
No live enforcement assertion is satisfied by mocked tests. Start with a fresh
dedicated destination for a first-execution test; do not erase a used destination
just to make a repeat test pass.

## Recovery, evidence and consistency

`environment_bindings` cannot be rebound. `environment_events` is append-only
and records configuration, observed runtime, dispatch request, process receipt,
destination observations and cancellation request. Existing immutable attempts
and append-only transitions remain. Sandbox UUID, operation digest, stable effect
ID and configuration digest survive host restart. Attempt IDs remain separate.

A SQLite transaction reserves dispatch before invoking OpenShell. Two host
processes sharing this journal cannot submit the same effect twice. The remote
SQLite worker also transactionally binds effect content and counts effects per
grant. Separate host journals against the same retained destination still rely
on that remote SQLite deduplication; they are not a distributed authority budget.
The Control Plane's JSON record store has its own concurrency limits; this change
does not make the entire authorization workflow multiprocess-safe.

A successful process response is followed by destination observation. A missing
acknowledgement yields `unknown`, with `newly_executed=false`; an observed row can
subsequently reconcile. An absent row after a recorded dispatch intent remains
unknown: a late process could still commit. Partial observations hold. Lost
sandbox, failed observation or unrecognized output never produces success.
There is no automatic safe recovery for an unknown/absent effect. A crash between
intent and actual submission may therefore require manual investigation even
when no effect happened. Safety is preferred to automatic liveness.

`request_cancel()` records a stop request and then attempts observation. Stop
acknowledgement does not mean prevention/reversal. A stopped sandbox may be
unobservable; report unknown. Preserve destination storage and journal when
investigating. `sandbox delete NAME` is destructive cleanup, never recovery;
only delete after reconciliation and evidence export. Do not infer storage
survival from host-client restart tests or assume all image paths survive every
upstream stop/start/delete lifecycle.

## Configuration and authority limitations

The pinned Control Plane does not bind an execution-environment digest in its
institutional decision. This pilot maps its existing synthetic adapter to one
trusted, locally narrowing configuration and records that association at decision
receipt. Changes afterward hold. It does **not** claim the institution approved
the sandbox image or policy. Upstream extension proposal: add
`execution_environment_commitment` to RuntimeProposal and AuthorizationBinding,
include it in proposal/approval/effect identity validation, and require manifest
constraints for the accepted environment profile. No adjacent repository is changed.
The earlier institution/domain extension remains documented separately.

Effective policy, admission revisions and sandbox identity are checked before
observation/dispatch and again on post-dispatch observation. OpenShell exec has
no policy-revision compare-and-execute precondition in the selected CLI contract.
An administrator changing policy between check and exec is a residual race.
Exclusive trusted gateway administration, a reviewed immutable worker image and
protected host configuration are therefore mandatory deployment assumptions.
A post-dispatch mismatch holds the result; it cannot retroactively prevent an effect.
The same applies to authority revocation after the Control Plane's effect-time
check: no continuous or transactional cross-system authorization claim is made.

No host OS confinement, remote exactly-once delivery, production readiness,
universal bypass resistance, independently authenticated institutional resolver,
or audit of upstream TypeScript is claimed. A privileged host or gateway admin,
mutable image supply chain, compromised worker, direct sandbox CLI user, or
process able to edit either database can bypass this application boundary.

## Migration

Existing local callers require no configuration change. The optional adapter
uses a distinct host journal and remote destination; do not move existing local
refund rows into it or reuse old effect IDs as new payments. The local executor
now holds an explicit unknown observation, labels duplicate commit acknowledgements
as reconciled, and avoids claiming a newly executed effect on timeout. Interface
version 0.2.0 and original local SQL effect schema remain unchanged.

## Validation record for this change

Locally executed against actual pinned Control Plane source
`2ea9528eeb87e14ff10f05de06473122b9df540f` and Manifest fixture
`46c950bed37fe3812000895430bc0312d29e37ce`:

```bash
PYTHONPATH=.:../control-plane/src \
MOLTBOT_SAFE_CONTROL_PLANE_ROOT=../control-plane \
MOLTBOT_SAFE_MANIFEST_FIXTURE=../action-manifest/examples/refund_integration_v1_1.manifest.json \
../.venv/bin/pytest -q tests
python -m compileall -q engine examples/openshell
git diff --check
```

Result: **82 passed, 2 skipped**. Both skips are live OpenShell tests. New tests
mock only the OpenShell transport and use the real worker and SQLite; integrated
tests additionally use actual pinned Control Plane implementation/Manifest fixture.
They cover authority revocation/expiry/policy/evidence changes, operation and
runtime substitution, exact effects, no-effect fake success, pre/post-commit
transport loss, restart, cancellation, partial holds and separate OS processes.
The earlier Python baseline tests also passed. No live kernel enforcement was run.
`python examples/openshell/provision.py --help` and Python compilation succeeded.

`pnpm lint` was attempted per repository guidance but the environment's automatic
dependency setup failed with `ERR_PNPM_EXOTIC_SUBDEP` for `libsignal` under
`@whiskeysockets/baileys` (`blockExoticSubdeps`). No security control was disabled.
The retained TypeScript lint/build/test suites are not reported as passed.

## Adapter change history

- 0.1.0: optional pinned OpenShell synthetic destination, durable environment
  binding, conservative recovery and opt-in live qualification ([PR #5](https://github.com/cogno-us/moltbot-safe/pull/5)).
  Thanks @titanicprime for the integration requirements and review boundaries.

### PR 5 targeted correction pass

Governor review of `559128a786759bfb7271c347d6d3f0c57c874aab` reproduced two
failures before modification: a contradictory amount with a copied digest was
reconciled, and cancellation called stop despite substituted or unavailable
identity. Four focused reproduction cases failed against that reviewed code.

Observation now requires the exact response shape and typed effect/state fields.
Applied and partial results require effect ID, operation digest, grant ID, target,
finite numeric amount (never boolean/string), unit and object payload. Each is
compared with the frozen operation. Canonical payload comparison distinguishes
nested booleans from numbers; amount allows SQLite's integer-to-REAL conversion.
Absent results must contain an empty destination object. Missing, malformed or
contradictory observations cannot reconcile successfully or cause replacement
execution. Dispatch reservations survive errors, lost acknowledgement and restart.
Rejected observations are journaled as `observation_rejected`.

Cancellation performs a fresh bound sandbox/configuration inspection **before**
calling stop. UUID substitution, missing identity or failed inspection records
`cancellation_held`, returns unknown and issues no stop request. The pinned
[CLI implementation](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-cli/src/run.rs)
and [StopSandboxRequest contract](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/proto/openshell.proto)
use name/workspace; `request_id` deduplicates requests and is not an expected
sandbox UUID. No immutable-ID/conditional stop precondition is available in this
interface. **The inspection-to-stop race remains**, so exclusive trusted gateway
administration is still mandatory. Stop acknowledgement never establishes rollback.

The worker image recipe now copies `LICENSE-APACHE-2.0`, `NOTICE`, `LICENSE`
(retained MIT) and `engine/LICENSE` into the image. The source notices are
unchanged; the base image retains its own notices. Image construction remains
unverified because Docker is unavailable.

Correction validation, using the same pinned dependencies and commands above:
**163 passed, 2 skipped** locally. Coverage includes altered/missing/wrong-type
fields after completion, restart and lost acknowledgement; non-finite amounts;
malformed response shapes; nested payload types; actual pinned Control Plane
reconciliation; and cancellation identity failures with no stop calls. Python
compilation and `git diff --check` passed. OpenShell transport remains mocked;
both live qualification tests remain skipped and live enforcement unverified.
