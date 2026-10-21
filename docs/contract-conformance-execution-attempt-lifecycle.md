# Contract conformance: execution-attempt lifecycle (producer)

- Contract: `execution-attempt-lifecycle`, version `0.1.0-draft` (a draft, not frozen; any change may break).
- Source: ODES `cogno-us/open-decision-evidence-standard` at commit `e5fe3822a2d3c3f6885fd61acccb4bb6281041be` (refreshed from `a9db024c`, which carried owner decisions 13-17 of the decision log, section 12 of `ODES-INTERCOMPONENT-CONTRACTS.md`), vendored in `tests/contracts/execution-attempt-lifecycle/` (`CONTRACT.json` lists the SHA-256 of every file).
- Role: producer (`runtime-executor`). Tracking: `cogno-us/cognous-control-plane#38`, steps P2 and P3.
- Test: `tests/test_contract_lifecycle_conformance.py`. P3 changed `engine/` to conform (see Findings).

## Level claimed

Level P (structure, rules and a real producer run) is claimed for every case marked conformant below, which after the P3 runtime change is every exercised case. It is not claimed for the runtime as a whole: route C, route B `hold` and the resolution journal are not exercised (see the table). This is a statement about the draft contract as vendored, not a certification.

## What the tests do

1. Drift check: recompute SHA-256 of every file in `CONTRACT.json`; fail on modified, missing or unlisted files. No dependency on the ODES repository.
2. Load the vendored `validate.py` and validate documents mapped from real runs (synthetic ids and data, synthetic SQLite destinations, no OpenShell).
3. Each case maps what the runtime emits (rows as stored, result dataclass as is). Nothing is reshaped to fit.
4. Extra tests pin the behaviour changed in P3 (reconciled/partial after a lost acknowledgement, `unknown` carrying a validated `absent`/`unknown` observation after an ambiguous dispatch, strict null-observation rule (null observation implies `observed_state` `unknown` for every status), no `{}` observation, export layout, terminal event agreement).

Route B uses a synthetic subclass of the real `AtomicAuthorityEffectDestination` (real schema, real executor, real `reconcile_claim`) whose atomic claim step writes the same rows as the real one, because the real step needs the Control Plane package, which is not a dependency here. Authority validation is therefore not exercised; the cross-component scenarios cover the real destination.

## Cases

Route names: route A = `PinnedControlPlaneExecutor` (`engine/control_plane_adapter.py`) over `LocalDestinationExecutor` (`engine/safe_executor.py`), route B = `AtomicLocalControlPlaneExecutor`. See "Route names" in [execution-boundary.md](execution-boundary.md).

| Case | Status |
|---|---|
| Route A executed | conformant |
| Route A partial | conformant |
| Route A failed | conformant (was F-P2-RT-2) |
| Route A denied (effect bound to other operation) | conformant (was F-P2-RT-3 and F-P2-RT-2; the contract admits attempt event `denied`). Null observation, `observed_state` `unknown` (decision 15) |
| Route A lost ack, effect observed applied | conformant, result `reconciled` (was N-1) |
| Route A malformed ack after commit, OpenShell-style fake destination | conformant, result `reconciled` (was N-1) |
| Route A ack unobserved (observation fails) | conformant, `unknown` with observation null (was F-P2-RT-1) |
| Route A ack lost, follow-up observation `absent`/`unknown` | conformant (extra tests): `unknown` carrying that validated observation, `observed_state == observation.state` (decision 14; was `unknown` with observation null) |
| Route A pre-commit existing observation `unknown` | conformant (extra test): `unknown` carrying the observation (decision 14) |
| Route B executed | conformant |
| Route B partial | conformant |
| Route B denied (local policy) | conformant (was F-P2-RT-2) |
| Route B post-commit observation failure, recovery status applied | conformant |
| Route B lost ack, reconciliation down, `unknown` placeholder | conformant |
| Route B effect-free reconcile, result `observed` | conformant |
| Route B recovery status `hold` | not exercised: real code reaches it only with no durable effect, where the atomic step leaves no attempt rows to map |
| Route C (`engine/control_plane_adapter.py`, `control_plane_attempts`, resolution journal) | not exercised: needs the Control Plane package. The runtime does not write `control_plane_attempts` rows (the Control Plane does), so `started_at` constancy is not a runtime property. The route C name is a qualification term the runtime owner has not confirmed |
| `export_execution_artifacts`, route A normal | conformant |
| `export_execution_artifacts`, route B normal | conformant |
| `export_execution_artifacts`, route A after timeout, validated `absent`/`unknown` | conformant (extra test): observation in `observations`, equal to `execution_result.observation` (decisions 14, 17) |
| `export_execution_artifacts`, route B post-commit recovery | conformant (was F-P2-RT-4) |
| `export_execution_artifacts`, route B effect-free reconcile | conformant (was F-P2-RT-4) |

The P3 contract rules also hold for normal runs: normal executions report `executed` (never `observed`), `executed` always carries its observation, and `observed` results have `attempt_id` null and `attempted`/`acknowledged`/`newly_executed` false.

## Findings and resolutions

| Id | Resolution | Evidence |
|---|---|---|
| N-1 | Fixed. With a validated applied/partial observation the result is `reconciled`/`partial` and the terminal attempt event equals it. The Control Plane adapter still raises TimeoutError for a lost acknowledgement and, when the Control Plane validates applied/partial while the local result is `unknown`, reports `reconciled`/`partial` and appends the matching terminal event. | `engine/safe_executor.py` timeout handler; `engine/control_plane_adapter.py` `commit` and `execute` |
| F-P2-RT-1 | Fixed. Follow-up observation failure returns observation null with `observed_state` `unknown`; a validated `absent`/`unknown` follow-up is carried (decision 14, which replaced the P3 rule that nulled it). | `engine/safe_executor.py` timeout handler |
| F-P2-RT-2 | Fixed. `ExecutionResult.observation` defaults to null. | `engine/safe_executor.py` `ExecutionResult` |
| F-P2-RT-3 | Resolved on the contract side (ODES e33d541a added `denied` to the attempt-event enum); no runtime change. | vendored `schema.json` |
| F-P2-RT-4 | Fixed. Validated observations only in `observations`; recovery report in optional `recovery_observations`. Profile stays 2.0.0, which does not distinguish the layouts (open question for the owner). | `engine/producer_contract.py` `export_execution_artifacts` |

Note, not a finding: the export carries no `contract`, `contract_version` or `producer` members; the tests add them when mapping.

## Local divergence from the vendored contract (to be upstreamed)

`validate.py` here differs from ODES `e5fe3822` in one rule: `_executor_rules` no longer returns early when `attempts` is absent. A missing `attempts` member is an empty row list, and the event and result rules run whenever `attempts`, `attempt_events` or `execution_result` is present. This closes the case where an `executed` result naming a nonexistent attempt validated. Three invalid fixtures and the matching `CONTRACT.json` hashes were added (`executor-ghost-result-without-attempts-member`, `executor-events-out-of-order-without-attempts-member`, `control-plane-empty-attempts-ghost-result`). The change must be proposed to ODES; until a refresh adopts it, a plain refresh would drop it (the tests `test_missing_attempts_member_*` then fail, which is the intended reminder).

## Refresh the vendored copy

From a checkout of the ODES repository at the commit to adopt:

```
python contracts/tools/vendor_contract.py copy --contract execution-attempt-lifecycle \
  --dest <runtime>/tests/contracts/execution-attempt-lifecycle --role producer --level P --odes-commit <full sha>
```

Last refresh: ODES `e5fe3822` (24 files added: fixtures for attempt identity, result and observation naming, state matrix, digest and text edge cases, a journal refusal case; `validate.py`, `schema.json` and `README.md` updated; label stays `0.1.0-draft`; no runtime change needed, the full suite passes unchanged). Previous refresh: ODES `a9db024c` (draft decisions 13-17: strict null-observation rule `null_observation_state_mismatch`, `unknown` may carry validated `absent`/`unknown`, no `{}` observation, mixed layout refused). Never edit vendored files by hand (the drift test fails). After a refresh, rerun `PYTHONPATH=.:src python -m pytest -q tests`, review any strict xfail that now passes (remove its xfail and update the table above), and update the version and commit in this document.
