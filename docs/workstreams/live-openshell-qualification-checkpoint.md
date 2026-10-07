# Live OpenShell qualification checkpoint

Worker 17 bounded live-enforcement qualification workstream.

## Scope and starting state

- Repository: `cogno-us/moltbot-safe`
- Actual starting `main`: `ff4eab5228c19f73aeb2a61d48046dd29111c6a9`
- Accepted image-qualification merge: `ff4eab5228c19f73aeb2a61d48046dd29111c6a9`
- Reviewed image source: `112a5c4b8a3337f6afbf52c399e5e1286625876f`
- Branch: `worker17/live-openshell-qualification`
- Executor producer profile: `2.0.0`
- Execution Envelope: `0.2.0`
- OpenShell: v0.1.2, upstream commit `6648bd0c290efbc41ba131ee9831ee45cd431f94`
- Control Plane pin preserved: `2ea9528eeb87e14ff10f05de06473122b9df540f`
- Action Manifest pin preserved: `46c950bed37fe3812000895430bc0312d29e37ce`
- Alvorada pin preserved: `fb3d97938969a89e149e8ff8db2756091d1233fc`

At workstream start, current `main` was exactly the accepted image-qualification
merge. The only open pull requests found were legacy producer-profile PRs #7 and
#8, both based on older pre-2.0.0 work. This branch does not consume them or
modify their files.

## Feasibility result

Live prerequisites are **unavailable in the current execution environment**.

Observed directly:

```text
command -v docker     -> missing
command -v podman     -> missing
command -v openshell  -> missing
test -e /dev/kvm      -> false
```

There is no explicitly authorized isolated OpenShell gateway/sandbox exposed to
this worker, no dedicated OpenShell client HOME/config, and no gateway-accessible
immutable worker image manifest digest was supplied.

Accordingly, **no live OpenShell execution or confinement test was attempted**.
Direct Docker execution, direct worker invocation, and mocked transport would not
satisfy this workstream and were not substituted for live evidence.

Machine-readable feasibility evidence:
`docs/workstreams/live-openshell-readiness-results.json`.

## Accepted packaged-image evidence reused

The preceding accepted workstream already established packaged-worker
compatibility through actual Docker stdin/stdout execution.

Accepted workflow run: `37563119581`

Recorded accepted evidence:

- qualified source SHA:
  `112a5c4b8a3337f6afbf52c399e5e1286625876f`
- resolved Python base:
  `python@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f`
- built local image ID:
  `sha256:3bfc362dccae2e217a720a219a6159f5f78ae96b24030778c9fa9a27d0cec7cd`
- focused source contract tests: 6 passed.

The local Docker image ID is a content identity for that CI build, **not** an
OpenShell-consumable repository manifest digest. A future live run still requires
an immutable `name@sha256:<manifest>` image already available to the authorized
gateway. This branch does not publish one.

## Readiness implementation

### `tools/live_openshell_readiness.py`

Read-only opt-in readiness gate. It:

- records actual repository SHA when run from a checkout;
- records the preserved Cognous/OpenShell dependency pins;
- checks the pinned CLI path/hash, Docker/Podman availability and `/dev/kvm`;
- requires an explicit `--authorized-isolated-environment` operator assertion;
- requires an existing dedicated client HOME, existing sandbox config and
  immutable `name@sha256` worker image;
- when those inputs exist, uses the accepted adapter's read-only gateway/sandbox
  inspection path to capture actual runtime, sandbox UUID, effective policy,
  configuration-admission revisions, resource settings, workdir and worker argv;
- returns exit code 2 when prerequisites are blocked;
- never provisions, executes, stops, deletes or mutates a sandbox.

A configuration file alone is not treated as enforcement evidence.

### Focused tests and readiness-only CI

`tests/test_live_openshell_readiness.py` checks immutable image syntax,
fail-closed prerequisite handling, explicit authorization and inspected-image
binding.

`.github/workflows/live-openshell-readiness.yml` runs those focused tests and
the non-mutating readiness gate. It is **readiness CI only**. A passing workflow
does not qualify live OpenShell execution or confinement.

## Exact setup and run procedure for an authorized host

These commands are **not executed by this workstream**. They are the bounded,
reproducible continuation once an authorized isolated host exists.

### 1. Verify the pinned OpenShell client and local gateway

Use the v0.1.2 binary corresponding to upstream commit
`6648bd0c290efbc41ba131ee9831ee45cd431f94`.

The environment must satisfy the support assumptions already documented in
`docs/openshell-adapter.md`: supported host architecture, usable Docker 28+
gateway, required Landlock/seccomp support, loopback local server and only the
Docker driver for this profile.

Do not disable host security controls to make the test pass.

### 2. Make the accepted worker available by immutable manifest digest

The gateway must already be able to resolve a reviewed image such as:

```bash
export REVIEWED_WORKER_OCI_DIGEST='local/cognous-refund@sha256:<reviewed-manifest-digest>'
```

Do not substitute a mutable tag. Do not publish an image as part of this
qualification batch.

### 3. Provision one disposable synthetic sandbox on the already-authorized gateway

```bash
python examples/openshell/provision.py \
  --binary "$PINNED_OPENSHELL_BINARY" \
  --home "$SYNTHETIC_CLIENT_HOME" \
  --gateway local \
  --workspace default \
  --name refund-live-qualification \
  --image "$REVIEWED_WORKER_OCI_DIGEST" \
  --output /tmp/cognous-live-openshell-config.json
```

Provisioning is permitted only on an explicitly authorized isolated local
qualification host. The helper does not install/start the gateway or publish an
image. Preserve both the config JSON and its `.creation.json` receipt.

### 4. Run the read-only readiness gate before executing effects

```bash
PYTHONPATH=. python tools/live_openshell_readiness.py \
  --binary "$PINNED_OPENSHELL_BINARY" \
  --home "$SYNTHETIC_CLIENT_HOME" \
  --config /tmp/cognous-live-openshell-config.json \
  --image "$REVIEWED_WORKER_OCI_DIGEST" \
  --authorized-isolated-environment \
  --output /tmp/cognous-live-openshell-readiness.json
```

A nonzero result is a blocker. Required missing/failed checks must not be
overridden or relabeled as qualified.

### 5. Run the existing opt-in live tests

```bash
export MOLTBOT_SAFE_OPENSHELL_CONFIG=/tmp/cognous-live-openshell-config.json
export MOLTBOT_SAFE_OPENSHELL_BINARY="$PINNED_OPENSHELL_BINARY"
export MOLTBOT_SAFE_OPENSHELL_HOME="$SYNTHETIC_CLIENT_HOME"

PYTHONPATH=".:pinned/control-plane/src" \
MOLTBOT_SAFE_CONTROL_PLANE_ROOT="pinned/control-plane" \
MOLTBOT_SAFE_MANIFEST_FIXTURE="pinned/action-manifest/examples/refund_integration_v1_1.manifest.json" \
pytest -q -s tests/test_openshell_live.py
```

These existing tests exercise the authorized synthetic effect/restart path plus
filesystem/network probes. They do **not** by themselves establish every case in
the requested live matrix below. Missing cases remain unexecuted until an
authorized live harness extends or exercises them.

## Required live qualification matrix

| Case | Current status | Required qualified observation |
| --- | --- | --- |
| Effective runtime/policy/sandbox identity | **Unexecuted** | Actual v0.1.2 runtime, loopback gateway, Docker driver, sandbox UUID, admitted revision and effective policy captured from live inspection. |
| Valid authorized operation | **Unexecuted** | Accepted Control Plane path creates exactly one synthetic SQLite effect with exact bindings. |
| Revoked/denied operation | **Unexecuted** | No destination effect and no OpenShell worker dispatch. |
| Duplicate same operation | **Unexecuted** | Same effect identity, no additional row/effect. |
| Restart under original identity | **Unexecuted** | Re-observation of original effect without replacement dispatch. |
| Conflicting effect/operation binding | **Unexecuted** | Rejected; original row unchanged. |
| Lost acknowledgement | **Unexecuted** | Unknown until later destination observation; no blind retry. |
| Partial delivery | **Unexecuted** | Remains partial/unresolved; no blind retry. |
| Prior attempt plus fresh absence | **Unexecuted** | Absence does not confer replacement dispatch permission. |
| Nonroot identity | **Unexecuted live** | Actual sandbox process UID/GID matches configured 1000/1000. |
| State-directory write | **Unexecuted live** | Write succeeds only in intended state path. |
| Code/outside-path writes | **Unexecuted live** | Writes denied by actual confinement. |
| Network egress | **Unexecuted live** | Positive control establishes networking tool/path; prohibited egress fails with policy denial such as EACCES/EPERM, not merely DNS/timeout. |
| CPU/memory/time limits | **Unexecuted** | Actual admitted limits recorded and bounded execution observed where the pinned interface exposes it. |
| Wrong/missing sandbox identity cancellation | **Unexecuted live** | Fresh inspection failure issues no stop. |
| Actual stop request | **Unexecuted live** | Stop request/ack, sandbox/process state and destination state recorded separately. No rollback/absence inference. |

A live qualification result is not valid unless every required case is executed
or explicitly reclassified by a separately reviewed scope decision. Failed,
missing or skipped required cases cannot produce a qualified result.

## Evidence requirements for the live continuation

Machine-readable evidence must retain:

- source, dependency and OpenShell revisions;
- immutable image manifest digest;
- CLI binary SHA-256;
- sandbox UUID/name/workspace;
- effective policy plus policy/config/provider revisions;
- worker argv, workdir, CPU/memory settings and operation commitment;
- commands, UTC timestamps and exit codes;
- decision/effect/attempt identities with executor and Control Plane namespaces
  kept separate;
- acknowledgements, observations, rejected observations and unresolved states;
- exact synthetic destination rows;
- declared configuration separately from observed enforcement;
- each case classified as executed/pass, executed/fail, skipped or unexecuted.

Do not infer network confinement from DNS failure or timeout. Do not infer
rollback or destination absence from a successful stop acknowledgement.

## Remaining trust assumptions

Even after a future successful live run, the following remain outside the pinned
interface's guarantees unless separately established:

- trusted and exclusive gateway administration during the qualification;
- integrity/provenance of the supplied immutable worker image in the gateway's
  local image supply;
- host and gateway administrator resistance;
- the documented inspection-to-exec and inspection-to-stop races;
- absence of an atomic expected-UUID/revision conditional stop in OpenShell
  v0.1.2;
- production institutional authentication;
- remote exactly-once delivery;
- TypeScript-wide Moltbot confinement;
- production deployment efficacy.

Cancellation request, stop acknowledgement, process termination, destination
observation and rollback remain distinct facts.

## Current disposition

This is a **blocked readiness workstream**, not a live qualification.

No paid infrastructure was provisioned. No production accounts or credentials
were accessed. No image was published. No service was deployed. No hub,
consumer, producer-version, GAX, Replay, ODES, Evidence Pack or unrelated
TypeScript changes were made.
