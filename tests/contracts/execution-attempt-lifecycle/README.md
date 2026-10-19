# Execution-attempt lifecycle contract

Version: `0.1.0-draft`. Status: spike, proposal only. Not part of `pder-v0.1`, not referenced by any `odes` code path, and not an ODES-core / `pder-v0.1` input; used by the qualification contract matrix and the verifier mutation harness. See `docs/engineering/ODES-INTERCOMPONENT-CONTRACTS.md` for the design this spike exercises.

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

Draft changes of 2026-10-17 (P3, still `0.1.0-draft`, not frozen):
- `denied` joins the attempt-event statuses: route A writes it when policy denies an attempt before execution (valid fixture `executor-denied-attempt-event`). Reversible while the contract is a draft.
- `started_at` of a control-plane attempt is fixed at start and identical on every row of that `attempt_id` (`attempt_started_at_changed`).
- Result `observed` is the effect-free observation. With any observation kind it carries `attempt_id` null and `attempted`, `acknowledged`, `newly_executed` false (`observed_result_claims_attempt`). A normal execution reports `executed`.
- Result `executed` carries the observation that established it; null is refused (`executed_without_observation`).
- Members stay optional in the contract. Replay stops requiring members the contract leaves optional (decision item 11, Replay-side half).

Draft changes of 2026-10-17, owner decisions, second batch (P3, still `0.1.0-draft`):
- `observation` is null or a typed observation; `{}` is never valid (invalid fixture `empty-object-observation`, refused structurally as `schema_violation`).
- Narrow relaxation: result `unknown` may carry a validated observation only when its `state` is `absent` or `unknown` (the follow-up reading after an ambiguous dispatch: timeout or lost acknowledgement). `unknown` with a validated `applied` or `partial` observation stays invalid (`unknown_with_validated_observation`). No new enum value. This also settles the route C case.
- For such a result `observed_state` equals `observation.state` (`unknown_state_mismatch`).
- Strict null-observation rule: whenever `observation` is null, `observed_state` is `unknown`, whatever the result status (`null_observation_state_mismatch`). A denied or failed result with null observation therefore reports `unknown`, not `absent`. Valid fixture `executor-denied-attempt-event` changed accordingly.
- `x-` extension members are accepted and preserved on Control Plane attempt rows, resolution journal events, journal inputs, resolution checks and check inputs, as on every declared object. Control Plane attempt rows stay a closed set (`undeclared_member`, `invalid_extension_key`).
- Mixed layout is refused: `observations` must list the validated observation the result carries, or be empty (`observations_mismatch`), and a report placed in `observations` is `schema_violation`.

Result status, observation state, recovery status, attempt event status and the other enumerations in `schema.json` are closed. A value outside them is refused with `unknown_enum_value` and is never coerced. Adding a value is a major bump. Where the unknown value sits inside a union (for example the observation), the refusal may surface as `schema_violation`.

## Known producer non-conformance (found by the P0 inventory, finding N-1)

The runtime's safe executor can return result `unknown` together with a validated observation when an OpenShell destination acknowledgement is malformed after the effect was observed as applied (`engine/safe_executor.py` timeout path, `engine/openshell_environment.py` commit). Owner decision 2026-10-17: the runtime reports `reconciled` or `partial` when a validated `applied`/`partial` observation exists (`unknown_with_validated_observation` keeps that meaning). A validated `absent` or `unknown` follow-up reading may ride on result `unknown` (second batch of decisions, same day). Until the runtime change lands it is non-conformant at the `applied`/`partial` point.

## Rules

Executor rows (`attempts`, `attempt_events`, `execution_result`):

- `attempt_id` is unique across `attempts`. Every attempt has events. Events start with exactly one `attempted` event (`missing_start_event`, `repeated_start_event`, `attempt_without_events`).
- Attempt state machine (decision 37, evidence check hardening, items ODES-2 and ODES-3). Executor attempt events move only along `attempted` to `executed`, `partial`, `failed`, `unknown`, `denied` or `reconciled`; `unknown` to `executed`, `partial`, `failed` or `reconciled`; `executed` or `partial` to `unknown` or `reconciled` (the Control Plane adapter appends `unknown` after them when the follow-up observation is not accepted, decision 39-P); `failed`, `denied` and `reconciled` are terminal. Control Plane attempt rows move `attempted` to `acknowledged`, `unknown`, `failed` or `partial`; `unknown` to `acknowledged`, `failed` or `partial`; `acknowledged`, `partial` and `failed` are terminal. Any other change of status within one attempt, including reopening a terminal attempt or `failed` after `acknowledged`, is `attempt_transition`. An identical consecutive Control Plane row and a `reconciled_existing` single row are not transitions.
- All rows of one Control Plane attempt carry one `effect_id` and one `decision_id` (`attempt_identity_changed`).
- `execution_result.effect_id` and `decision_id`, and `execution_result.observation.effect_id`, equal the effect and decision of the attempt rows when attempts are present, and the result's observation names the result's own effect when there are none (`result_binding_mismatch`).
- `event_id` strictly increases and `created_at` does not decrease across the event sequence (`event_order`).
- Observation kinds. An `execution_result.observation` is null, a validated Control Plane observation (it has `state`), or a typed recovery observation (it has `status` and `retry_eligible`, and no `state`; closed member set). They are different evidence. A recovery observation is destination-side recovery output (the runtime `reconcile_claim` record, or the `reconciliation_unavailable` placeholder). It is never validated evidence of effect, and consumers must not treat it as one.
- Observation null: `observed_state` is `unknown` for every result status (`null_observation_state_mismatch`). `{}` is never a valid observation.
- Result consistency. The result's status, flags and observation agree in the producer's matrix (`result_state_matrix`): failed, partial, unknown, executed and reconciled require `attempted`; an acknowledgement requires `attempted`; a fresh execution is acknowledged and has status executed, partial or unknown; denied and failed carry no acknowledgement, no fresh execution and `observed_state` unknown; executed is acknowledged, fresh and observed `applied`; reconciled is not fresh and observed `applied`; partial is observed `partial`; executed, reconciled, partial and observed retain an observation; a validated observation's `state` is the result's `observed_state`; `observed_state` is one of applied, partial, absent, unknown. The rule runs after the more specific rules, so a document they already refuse keeps their code.
- Result attempt binding (`result_attempt_unresolved`, decision 40). A result whose status claims an attempt (`failed`, `partial`, `unknown`, `executed`, `reconciled`) names an `attempt_id` that matches a row of `attempts`. A null `attempt_id` and an id that matches no row are both refused, whatever the flags and observation say; the independent verifier refuses the same records under the same code. Results `observed` and `denied` claim no attempt and keep their own rules. The rule runs after the rules that already refuse the record's rows and events, and before the terminal-event comparison, so a record they refuse keeps their code, and a repeated attempt row stays `duplicate_attempt_row` alone. Fixtures: for each of the five statuses, `result-<status>-null-attempt-id` and `result-<status>-unregistered-attempt-id`; for `partial`, `unknown` and `reconciled`, `result-<status>-flags-contradict-without-attempt-id` (a combination of flags and state that the producer's matrix also refuses; for `failed` and `executed` the only such combination without an attempt id is the null-id fixture itself).
- Result status `unknown` means no validated evidence of effect exists. It may carry observation null, a recovery observation, or a validated observation whose `state` is `absent` or `unknown`; a validated `applied`/`partial` observation is refused (`unknown_with_validated_observation`). `observed_state` is `unknown` for null; equals the recovery record's `status` when that is `applied` or `partial` and `unknown` otherwise for a recovery observation; equals `observation.state` for a validated observation (`unknown_state_mismatch`). On an `unknown` result, recovery `applied`/`partial` means "recovery reports", not "validated". `attempted`/`acknowledged` are not constrained by this rule.
- A recovery observation is carried only by results `unknown` (post-commit recovery) or `observed` (effect-free reconcile) (`recovery_observation_status`).
- Recovery path rules. A recovery report never makes an effect retry-eligible (`retry_eligible` must be false, `recovery_retry_eligible`). `applied`/`partial` reports bind `claim_state`, `effect_id` and `operation_digest`; `hold` and `unknown` reports carry a non-empty `reason` (`recovery_report_incomplete`). A `hold` report establishes no destination state. On an `observed` result (effect-free reconcile) `attempt_id` is null and `attempted`, `acknowledged` and `newly_executed` are false (`effect_free_reconcile_claims_attempt`), and `observed_state` equals the report status when that is `applied`/`partial`, else `unknown` (`recovery_state_mismatch`). These rules mirror what Replay enforces on import (replay #37).
- For a result whose attempt is in `attempts` and whose status is not `observed`: the last event must not be `attempted` (`terminal_event_missing`) and must equal the result status (`status_terminal_mismatch`). The single exception is terminal `executed` with result `unknown`, which is an acknowledged but unobserved delivery.

Control Plane attempts (`control_plane_attempts`):

- A lifecycle (rows sharing `attempt_id`, in order) starts with an `attempted` row, unless it is a single `acknowledged` or `partial` row whose `acknowledgement` is exactly `{"reconciled_existing": true}` (`missing_start_record`).
- `reconciled_existing`, when present, must be `true`, and only on that single-row shape (`invalid_reconciled_marker`).
- No lifecycle record repeats verbatim (`repeated_record`).

Resolution journal and checks (`resolution_journal`, `resolution_checks`):
- Refusal rows persist. A refusal row (for example an effect-time refusal for status unreadable) stays in the journal, and a later authorised decision with its own `effect_id` is valid in the same run; the refusal does not block it (fixture `journal-refusal-then-valid-request`).
- Journal events follow open, then any inputs, then completed or interrupted, with the binding fields unchanged (`journal_protocol`). A completed check binds to a resolution check by stage, inputs and reasons, because the two sets of ids are separate producer ids today (`journal_unbound_completion`, `journal_check_reused`, `duplicate_check_id`).
- Rule `reasons_match` (decision 39-A). A completed check binds to a resolution check only when the journal completion's reasons are a subset of the check's reasons AND every additional reason on the check is in `POST_JOURNAL_GATE_CODES`, a closed set owned by this contract: the codes the Control Plane appends after the journal completion row is written. Set (8): `operation_grant_mismatch`, `operation_renewal_unrecorded`, `operation_not_admitted`, `operation_merged_effect_exists`, `operation_merged_effect_unresolved`, `dedupe_unresolved_attempt_blocks_new_key`, `dedupe_operation_effect_applied`, `input_observation_time_regressed` (derived from Control Plane `6093aca7`: `bounded.py` `_admit_operation`, `_operation_gate`, `_effect_gate_n4`, `record_resolution_check`; `merge.py` `merge_gate`). The constant is `POST_JOURNAL_GATE_CODES` in `validate.py`; a consumer that vendors it records its sha256 over the codes sorted and joined by a line feed. Adding a code is a change of this contract.
- Rule `reasons_minimum` (decision 39-E). A completed journal check's reasons include the floor its own recorded input events imply (`reasons_minimum`): an input read `unreadable` that no later read of the same input rescued as `cached_positive` implies `status_unreadable`; an input read `nonce_mismatch` implies `status_nonce_mismatch` (checked by the journal contract, which carries that profile value); a last read of an `authority_context` that is missing with no read state implies `authority_context_missing`; a last read of a `purpose` that is missing with no read state implies `purpose_unreadable`; any input whose last read is missing and not rescued implies at least one reason. Table: `IMPLIED_REASONS` and `implied_reasons` in `validate.py` (Control Plane `6093aca7`, `bounded.py` `_resolve_journal`, `_read_grant`, `_read_input`, `capture`; `runtime_authority.validate_extensions`). It is a floor, not a full derivation: the required-input list is not recorded, so `required_input_not_read` is not derived (it is written next to `status_unreadable` by the producer but implied by nothing in the record).
- Checks that do not overlap in the journal (one completed before the other opened) must appear in `resolution_checks` in that order (`resolution_checks_out_of_order`). Overlapping checks may complete, and be listed, in either order.

## Fixtures

Each fixture file is `{fixture_id, synthetic, expected_outcome, reason, [expected_refusal], document}`. `fixtures/valid/` must be accepted. `fixtures/invalid/` must be refused with `expected_refusal` as the only violation.

Valid: `executor-unknown-ack-unobserved`, `executor-unknown-recovery-observation-route-b`, `executor-unknown-recovery-unavailable-route-b`, `executor-unknown-recovery-hold-route-b`, `executor-observed-effect-free-reconcile-route-b`, `executor-unknown-recovery-separate-member-route-b`, `executor-normal-observations-member`, `control-plane-reconciled-existing-acknowledged`, `control-plane-reconciled-existing-partial`, `resolution-checks-overlap-a-completes-first`, `resolution-checks-overlap-b-completes-first-listed-open-order`, `resolution-checks-overlap-b-completes-first-listed-completion-order`, `executor-normal-attempted-executed-observed`, `control-plane-normal-attempted-acknowledged`, `extension-namespace-member-preserved`, `executor-denied-attempt-event`, `executor-unknown-validated-absent-after-ambiguous-dispatch`, `executor-unknown-validated-unknown-after-ambiguous-dispatch`, `control-plane-attempt-extensions-preserved`, `resolution-journal-checks-extensions-preserved`.

Invalid: `control-plane-dropped-start-record`, `executor-dropped-start-event`, `executor-unknown-with-validated-observation`, `executor-unknown-recovery-state-contradiction`, `executor-terminal-failed-vs-status`, `executor-dropped-terminal-event`, `resolution-checks-sequential-reorder`, `undeclared-member-outside-extensions`, `control-plane-start-row-reconciled-existing-false`, `extension-key-without-x-prefix`, `recovery-report-in-observations-member`, `recovery-observations-differs-from-result`, `result-status-unknown-enum-value`, `recovery-retry-eligible-true`, `recovery-applied-missing-operation-digest`, `recovery-hold-missing-reason`, `effect-free-reconcile-claims-attempt`, `effect-free-reconcile-state-mismatch`, `empty-object-observation`, `executor-unknown-with-validated-partial-observation`, `executor-unknown-validated-absent-state-mismatch`, `null-observation-state-absent`, `null-observation-state-applied-failed-result`, `mixed-layout-observations-disagree-with-result`, `control-plane-attempt-undeclared-member`, `control-plane-attempt-extension-key-without-x-prefix`, `resolution-journal-extension-key-without-x-prefix`, `resolution-check-undeclared-member` (also `executed-without-observation`, `observed-result-claims-attempt`, `attempt-started-at-changed`, `result-failed-null-attempt-id`, `result-partial-null-attempt-id`, `result-unknown-null-attempt-id`, `result-executed-null-attempt-id`, `result-reconciled-null-attempt-id`, `result-failed-unregistered-attempt-id`, `result-partial-unregistered-attempt-id`, `result-unknown-unregistered-attempt-id`, `result-executed-unregistered-attempt-id`, `result-reconciled-unregistered-attempt-id`, `result-partial-flags-contradict-without-attempt-id`, `result-unknown-flags-contradict-without-attempt-id`, `result-reconciled-flags-contradict-without-attempt-id`).

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

## Input hardening (decision 32, 2026-10-18)

The reference validator never raises: any parsed JSON value gets a list of typed violations. Non-finite numbers (NaN, Infinity) and text that is not valid Unicode are refused as `schema_violation`; `main()` reads files with a strict loader (duplicate members, NaN, Infinity, overflowing numbers and non-UTF-8 input are refused, exit status 2, line `input_refused`); schema patterns match the whole string (`(?!\n)$` in `schema.json`) and times are ISO-8601 with ASCII digits and a UTC offset. No code, member or accepted form changed meaning; no code was added.

Invalid fixtures added: `trailing-line-break-digest`, `unpaired-surrogate-text`.
