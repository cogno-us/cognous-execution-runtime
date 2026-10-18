# Executor producer profile 2.0.0

The supported Python execution boundary exports a versioned producer contract for
consumers such as Agent Replay Bundle and the GAX/IMX reference integration.

Profile ID: `urn:cognous:profiles:moltbot-safe-executor-producer`  
Profile version: `2.0.0`
Execution Envelope version: `0.2.0`

The contract contains the exact execution envelope/result plus bounded destination
effects, attempts, append-only attempt events and producer observation records.

The profile version is an interface version. It is not a repository revision.
A repository revision can be emitted separately as source-asserted provenance.
Consumers must not treat that assertion as independently established provenance.

The producer export does not create authority, approvals, grants, policies or
resolvers. Those remain caller-supplied inputs to the Control Plane/executor path.

Legacy unversioned exports remain historical artifacts. They are not relabeled as
profile 1.0.0; consumers that continue to accept them must do so through an
explicit legacy compatibility path with revision-pinned handling.

## Migration from 1.0.0

Profile 2.0.0 explicitly supports the repaired Control Plane observation contract.
The envelope remains 0.2.0. Old profile 1.0.0 and unversioned evidence remain
revision-pinned historical evidence; do not relabel them or assume consumers
accept 2.0.0 without an explicit compatibility change.

- `execution_result.observation` can be null. Its observed state is then unknown,
  even when acknowledged and newly executed are true. The destination may contain
  an effect; null establishes neither absence nor rollback nor retry permission.
- `control_plane_evidence` on the result and export retains the actual upstream
  attempt, acknowledgement, and reconciliation (including policy, evaluation time,
  reasons and rejected evidence). The upstream attempt no longer lives inside an
  accepted observation. `control_plane_attempts` now includes upstream attempts
  even when the primary `attempt_identity` belongs to the executor namespace.
- `observations` carries validated Control Plane observations only (members with
  `state`). A typed route B recovery report goes in the optional
  `recovery_observations` list (emitted only when a report exists) and is still
  carried by `execution_result.observation`. Profile version stays 2.0.0: the
  profile id and version do not distinguish this layout from the earlier one in
  which the report sat in `observations`, so a consumer must accept or refuse
  `recovery_observations` explicitly.
- The runtime's producers emit the following rules; `export_execution_artifacts` does not refuse every result that breaks them (for example an `unknown` result with a null observation and `observed_state` `applied` is accepted), and route B can return `unknown` with `observed_state` `applied` together with a recovery report.
- Failed and denied results carry `observation` null (previously `{}`) and `observed_state` `unknown`. Whenever `observation` is null, `observed_state` is `unknown` for every status (strict rule, lifecycle contract decision 15); `observation` is never `{}`.
- An `unknown` result may carry a validated `absent` or `unknown` observation (for example the follow-up reading after a lost acknowledgement), with `observed_state` equal to `observation.state`; `observations` then holds that observation and equals `execution_result.observation` (lifecycle contract decisions 14 and 17). `unknown` never carries a validated `applied` or `partial` observation: those report `reconciled`/`partial`.
- `observations` never includes a rejected Control Plane observation.
  `rejected_observations` retains supplied rejected evidence, including a wrong
  effect identifier as attributed rejected content, not an accepted identity.
  Unavailable observations produce no invented evidence.
- Local acknowledgements may contain raw destination evidence. They are not
  accepted observation claims. Local-only historical observation remains raw
  local evidence without Control Plane validation; use `reconcile(envelope,
  now=timezone_aware_time)` for explicit policy validation.
- Destination evidence now includes the actual stored `state` in
  `destination_state`; OpenShell transport validation requires it and checks its
  agreement with outer state. Rebuild the reviewed worker image with this code
  before using that transport. Old worker output fails closed; no live image was
  provisioned in this batch.

Replay, ODES, Evidence Pack and Alvorada/GAX consumers must explicitly accept this
profile, keep attempt namespaces separate, preserve null/rejected observations,
and distinguish acknowledged execution from validated observation. Preserve
`observed_absent` and `retry_eligible=false`; historical `safe_to_retry` records
are readable history, not renewed permission. The hub must update pins only after
those separately reviewed migrations and rerun release qualification. No consumer
compatibility or hub green release is claimed here.
