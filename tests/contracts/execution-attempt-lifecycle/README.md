# Execution-attempt lifecycle contract

Version: `0.1.0-draft`. Status: spike, proposal only. Not part of `pder-v0.1`, not referenced by any `odes` code path, and not a qualification input. See `docs/engineering/ODES-INTERCOMPONENT-CONTRACTS.md` for the design this spike exercises.

## Contract statement

This contract governs the boundary between the producers of execution-attempt records and the importer that consumes them:

- Producers: the runtime executor (routes A, B and C, including `engine/control_plane_adapter.py` for route C) and the Control Plane bounded workflow (`bounded.py` attempt records, resolution-check journal and resolution checks).
- Consumer: the Replay bundle importer (`importers.py` `_validate_v2_executor_rows` and `_validate_repaired_run`, and `refusal_evidence.py` `validate_run`).

A conforming document states `contract: "execution-attempt-lifecycle"` and `contract_version: "0.1.0-draft"` and carries at least one of `attempts` (executor rows with `attempt_events` and optionally `execution_result`), `control_plane_attempts`, or `resolution_journal` (with `resolution_checks`). `schema.json` (JSON Schema draft 2020-12) defines members and types. The rules below are the cross-record half, implemented by `validate.py`.

The shapes are modelled on the real producer records (SQLite `attempts` and `attempt_events` rows, `EffectAttempt`, `ExecutionResult`, journal events and resolution checks). Fixtures are small and synthetic.

## Member model (optional-members proposal)

1. Declared required members must be present. Declared optional members (for example `execution_result.acknowledged`) may be absent.
2. Every declared object has a closed member set. A member that is neither declared nor inside `extensions` is refused with `undeclared_member`. It is never silently dropped (this preserves V1 finding F-10).
3. Every declared object may carry `extensions`, an object whose member names match `^x-[a-z0-9][a-z0-9._-]*$`. Consumers ignore extension members for lifecycle logic, must preserve them when re-emitting, and must include them in any digest. Producers never emit undeclared members; new data goes in a declared member or in `extensions`.
4. Adding an optional declared member is a minor bump. Adding a required member, or changing the meaning of a status, is a major bump.

DECIDED (owner, André de Lima, 2026-10-17): the extension namespace is a reserved `extensions` object with `x-` prefixed names, consumers must preserve it and include it in digests, and undeclared members elsewhere stay refused.

Placement (owner decision, 2026-10-17): this contract is intended to become an optional extension profile of the ODES core (a declared, versioned behavioural profile with its own fixtures), not a companion interface spec. See section 4.1 of the design document. The spike remains outside `src/odes`.

## Producer names

The contract uses the names the runtime repository defines. Qualification's route letters map to them as follows (the route C row is a qualification term the runtime repo does not define, so the runtime owner must confirm it):

| Qualification term | Runtime name | Where |
|---|---|---|
| route A | `PinnedControlPlaneExecutor` | `engine/safe_executor.py` |
| route B | `AtomicLocalControlPlaneExecutor` ("atomic claim route") | `engine/atomic_local_control_plane_executor.py` |
| route C | Control Plane adapter over the safe executor | `engine/control_plane_adapter.py` |

Fixture ids keep the `route-b` wording for stability; read them as the atomic local executor.

## Export layout (decided 2026-10-17, ODES item 7)

- `observations` carries validated Control Plane observations only. A record with `status` and no `state` is refused there.
- `recovery_observations` carries typed recovery reports. It is optional. When present it equals the report carried by `execution_result.observation`, or is empty when the result carries none (`recovery_observations_mismatch`).
- `execution_result.observation` still carries the report as before.
- Legacy layout (recovery report inside `observations`): producers move to the new layout in the runtime's P3 change; consumers accept both during alpha and drop the legacy layout when the contract freezes. This draft refuses the legacy layout.

## Closed enumerations (decided 2026-10-17, ODES item 6)

Result status, observation state, recovery status, attempt event status and the other enumerations in `schema.json` are closed. A value outside them is refused with `unknown_enum_value` and is never coerced. Adding a value is a major bump. Where the unknown value sits inside a union (for example the observation), the refusal may surface as `schema_violation`.

## Known producer non-conformance (found by the P0 inventory, finding N-1)

The runtime's safe executor can return result `unknown` together with a validated observation when an OpenShell destination acknowledgement is malformed after the effect was observed as applied (`engine/safe_executor.py` timeout path, `engine/openshell_environment.py` commit). That combination is invalid here (`unknown_with_validated_observation`). Owner decision 2026-10-17: the runtime changes, and reports `reconciled` or `partial` when a validated observation exists. Until that runtime change lands, the runtime is non-conformant at this point. The change is scheduled for P3, after a probe that reproduces the path.

## Rules

Executor rows (`attempts`, `attempt_events`, `execution_result`):

- `attempt_id` is unique across `attempts`. Every attempt has events. Events start with exactly one `attempted` event (`missing_start_event`, `repeated_start_event`, `attempt_without_events`).
- `event_id` strictly increases and `created_at` does not decrease across the event sequence (`event_order`).
- Observation kinds. An `execution_result.observation` is null, a validated Control Plane observation (it has `state`), or a typed recovery observation (it has `status` and `retry_eligible`, and no `state`; closed member set). They are different evidence. A recovery observation is destination-side recovery output (the runtime `reconcile_claim` record, or the `reconciliation_unavailable` placeholder). It is never validated evidence of effect, and consumers must not treat it as one.
- Result status `unknown` means no validated observation exists. It may carry observation null or a recovery observation, and must not carry a validated observation (`unknown_with_validated_observation`). `observed_state` is `unknown`, except that it equals the recovery record's `status` when that is `applied` or `partial` (`unknown_state_mismatch`). On an `unknown` result, `applied`/`partial` means "recovery reports", not "validated". `attempted`/`acknowledged` are not constrained by this rule.
- A recovery observation is carried only by results `unknown` (post-commit recovery) or `observed` (effect-free reconcile) (`recovery_observation_status`).
- Recovery path rules. A recovery report never makes an effect retry-eligible (`retry_eligible` must be false, `recovery_retry_eligible`). `applied`/`partial` reports bind `claim_state`, `effect_id` and `operation_digest`; `hold` and `unknown` reports carry a non-empty `reason` (`recovery_report_incomplete`). A `hold` report establishes no destination state. On an `observed` result (effect-free reconcile) `attempt_id` is null and `attempted`, `acknowledged` and `newly_executed` are false (`effect_free_reconcile_claims_attempt`), and `observed_state` equals the report status when that is `applied`/`partial`, else `unknown` (`recovery_state_mismatch`). These rules mirror what Replay enforces on import (replay #37).
- For a result whose attempt is in `attempts` and whose status is not `observed`: the last event must not be `attempted` (`terminal_event_missing`) and must equal the result status (`status_terminal_mismatch`). The single exception is terminal `executed` with result `unknown`, which is an acknowledged but unobserved delivery.

Control Plane attempts (`control_plane_attempts`):

- A lifecycle (rows sharing `attempt_id`, in order) starts with an `attempted` row, unless it is a single `acknowledged` or `partial` row whose `acknowledgement` is exactly `{"reconciled_existing": true}` (`missing_start_record`).
- `reconciled_existing`, when present, must be `true`, and only on that single-row shape (`invalid_reconciled_marker`).
- No lifecycle record repeats verbatim (`repeated_record`).

Resolution journal and checks (`resolution_journal`, `resolution_checks`):

- Journal events follow open, then any inputs, then completed or interrupted, with the binding fields unchanged (`journal_protocol`). A completed check binds to a resolution check by stage, inputs and reasons, because the two sets of ids are separate producer ids today (`journal_unbound_completion`, `journal_check_reused`, `duplicate_check_id`).
- Checks that do not overlap in the journal (one completed before the other opened) must appear in `resolution_checks` in that order (`resolution_checks_out_of_order`). Overlapping checks may complete, and be listed, in either order.

## Fixtures

Each fixture file is `{fixture_id, synthetic, expected_outcome, reason, [expected_refusal], document}`. `fixtures/valid/` must be accepted. `fixtures/invalid/` must be refused with `expected_refusal` as the only violation.

Valid: `executor-unknown-ack-unobserved`, `executor-unknown-recovery-observation-route-b`, `executor-unknown-recovery-unavailable-route-b`, `executor-unknown-recovery-hold-route-b`, `executor-observed-effect-free-reconcile-route-b`, `executor-unknown-recovery-separate-member-route-b`, `executor-normal-observations-member`, `control-plane-reconciled-existing-acknowledged`, `control-plane-reconciled-existing-partial`, `resolution-checks-overlap-a-completes-first`, `resolution-checks-overlap-b-completes-first-listed-open-order`, `resolution-checks-overlap-b-completes-first-listed-completion-order`, `executor-normal-attempted-executed-observed`, `control-plane-normal-attempted-acknowledged`, `extension-namespace-member-preserved`.

Invalid: `control-plane-dropped-start-record`, `executor-dropped-start-event`, `executor-unknown-with-validated-observation`, `executor-unknown-recovery-state-contradiction`, `executor-terminal-failed-vs-status`, `executor-dropped-terminal-event`, `resolution-checks-sequential-reorder`, `undeclared-member-outside-extensions`, `control-plane-start-row-reconciled-existing-false`, `extension-key-without-x-prefix`, `recovery-report-in-observations-member`, `recovery-observations-differs-from-result`, `result-status-unknown-enum-value`, `recovery-retry-eligible-true`, `recovery-applied-missing-operation-digest`, `recovery-hold-missing-reason`, `effect-free-reconcile-claims-attempt`, `effect-free-reconcile-state-mismatch`.

## Run

```
python contracts/execution-attempt-lifecycle/validate.py <document.json>...   # prints violations; fixtures wrap the document
PYTHONPATH=src python -m pytest -q tests/test_contract_execution_attempt_lifecycle.py
```

`validate.py` is stdlib only. The test suite also cross-checks `schema.json` against the `jsonschema` package when it is installed.

## Versioning and change rules (0.x)

While the version is `0.x-draft` nothing is frozen and any change may break. The freeze rules in the engineering document apply from `1.0.0`: patch for clarifications and added fixtures, minor for optional additions, major for required additions or semantic change, with a published deprecation window.

## Known gaps in this spike

- `execution_result.observation` is opaque here. It belongs to the bounded-workflow observation contract.
- No rule constrains an `observed` result's terminal event beyond what the producers do today.
- Behaviour such as the Control Plane `close_uncertain_attempts` closeouts is outside this spike.
