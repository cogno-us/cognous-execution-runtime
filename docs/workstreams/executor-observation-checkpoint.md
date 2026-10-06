# Batch 4C-A executor observation compatibility checkpoint

2026-10-06 UTC. Governor review required; no self-merge. Entry point for this
executor-only batch. Hub PR #4 and its dependency lock remain untouched.

Starting main: `1d308faf664c504b6e310db3c7a310153ef7b067` (also the accepted
executor baseline). Main was rechecked before publication; no concurrent change
was observed. Branch: `worker14b/control-plane-observation-compat`. The PR head
and one-time CI check are recorded in the PR description; this file belongs to
that head rather than embedding a self-referential commit hash.

Control Plane compatibility/test pin advances to
`2ea9528eeb87e14ff10f05de06473122b9df540f`.
Manifest fixture remains `46c950bed37fe3812000895430bc0312d29e37ce`.
No other component or dependency pin changes.

## Interfaces and evidence

Workflow cloning preserves caller ObservationPolicy, including strict values and
missing policy. The observation clock defaults to UTC wall time; deterministic
fixtures explicitly inject their observation clock separately from evaluation
`now`. No production synchronization/trusted clock guarantee is claimed.
Public `reconcile(envelope, now=...)` performs effect-free reconciliation with the
caller policy and explicit evaluation time. Local `observe_historical` remains
raw local observation, not authorization renewal or CP validation.

Execution retains separate local and CP attempts and the CP reconciliation.
Per-invocation reconciliation capture forwards all records to the actual durable
store and avoids selecting another invocation's last reconciliation.
Null validated observation yields unknown observed state, preserving actual
acknowledgement and newly-executed evidence. Rejected evidence is exportable only
outside accepted observation claims. Producer profile advances incompatibly to
2.0.0; envelope stays 0.2.0. See [producer migration](/executor-producer-profile).

Executable cases are in `tests/test_observation_compatibility.py`.
`executor-observation-results.json` beside this checkpoint contains actual
exported identities, acknowledgements, effect rows and accepted/rejected evidence
from the focused run. It records candidate-worktree execution, not an independent
attestation or an assertion that old baseline code produced these results.

| Executed case | Actual destination result | Recovery / classification |
| --- | --- | --- |
| Valid execution, explicit policy | One applied effect; exact amount, unit, payload, grant, target and operation digest | Policy 17 seconds / zero tolerance retained; explicit evaluation time; missing/naive reconciliation time fails closed |
| Missing policy | Zero effects, no dispatch | Denied; retained policy-missing reason |
| Wrong effect, stale time, malformed time, contradictory state after dispatch | One applied effect in each of four runs; acknowledged, observation null | Rejected evidence retained separately; reopen both stores and observe same effect; one local attempt total |
| Unavailable post-dispatch observation | One applied effect; acknowledged, observation null | Unavailability reason retained; same original effect recovered after restart; no replacement |
| Timeout before commit then fresh absence | Zero effects before and after second execute; one local attempt | `observed_absent`, retry false; subsequent execute denied |
| Lost acknowledgement | One applied effect; acknowledgement false | Accepted observation of original effect; recovery adds no local attempt |
| Partial delivery | One partial effect with exact retained operation content | Recovery retains partial, adds no local attempt |
| Producer rejection safeguards | Real applied effect retained | Promoted rejected observation and substituted reconciliation identity fail export |

All are required safety regressions; none was recategorized to make the gate pass.
Legacy `safe_to_retry` parsing remains supported but current absence does not
confer retry permission. These are same-host SQLite and JSON-store observations;
no distributed exactly-once, cross-host recovery, live confinement, authentication
or independent verification is established. Faults target the public destination
observation seam around real dispatch; no replacement authorization or execution
implementation is used. Restart is object/store reopen, not a process-kill test.

## Validation

Environment: Python 3.12.14; pytest 9.1.1; pydantic 2.13.5. Actual accepted CP checkout
and pinned Manifest fixture, not an unpinned package. Reproduce with:

```sh
export PYTHONPATH=.:pinned/control-plane/src
export MOLTBOT_SAFE_CONTROL_PLANE_ROOT=pinned/control-plane
export MOLTBOT_SAFE_MANIFEST_FIXTURE=pinned/action-manifest/examples/refund_integration_v1_1.manifest.json
pytest -q tests
python -m compileall -q engine examples/openshell
MOLTBOT_SAFE_COMPAT_EVIDENCE=docs/workstreams/executor-observation-results.json pytest -q tests/test_observation_compatibility.py
```

Full Python suite: **196 passed, 2 skipped**, zero failures. Repeated in an
isolated environment with the exact CI dependencies (pytest 8.3.5 and pydantic
2.11.9): **196 passed, 2 skipped**, zero failures. Both skips are opt-in
live OpenShell tests; they are unexecuted boundaries, not safety passes. Focused
suite: **11 passed**. Compilation and `git diff --check` passed.
`pnpm lint` and `pnpm test` each failed before executing their checks because
pnpm 11.25.0 dependency setup rejects the existing transitive Git `libsignal`
dependency (`ERR_PNPM_EXOTIC_SUBDEP`). No dependency policy bypass or lock edit.
JavaScript lint/tests therefore remain unverified locally. CI uses the repository
workflow environment and must be assessed on the final PR head; no green CI claim
is made before execution. Check CI once after submission; do not poll queued jobs.

## Remaining work (separate bounded batches)

1. Governor review and final-head CI disposition for this executor PR, including
   the environment-blocked JavaScript checks. No merge by this worker.
2. Explicit producer 2.0.0 migrations in Replay, ODES, Evidence Pack and
   Alvorada/GAX: null/rejected observations, namespace separation, original
   identities and provenance, observed-absence semantics, accepted evidence only.
   Keep historical evidence version/revision pinned. Preserve null successor
   decision attribution; do not invent producer attribution.
3. Rebuild/review the matching OpenShell worker image before using its changed
   destination observation shape. Live provisioning was neither performed nor
   authorized in this batch.
4. Only after reviewed consumer compatibility, update hub component pins in a
   separate batch and rerun qualification. Hub Batch 4C cases 3–5 and research
   qualification remain open; this executor batch does not complete them.
