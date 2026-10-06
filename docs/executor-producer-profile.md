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
