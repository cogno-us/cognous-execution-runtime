# Contract conformance: execution-attempt lifecycle (producer)

- Contract: `execution-attempt-lifecycle`, version `0.1.0-draft` (a draft, not frozen; any change may break).
- Source: ODES `cogno-us/open-decision-evidence-standard` at commit `d1e17fa155140f371050aff4b61da0f2cc84f44a`, vendored in `tests/contracts/execution-attempt-lifecycle/` (`CONTRACT.json` lists the SHA-256 of every file).
- Role: producer (`runtime-executor`). Tracking: `cogno-us/cognous-control-plane#38`, step P2.
- Test: `tests/test_contract_lifecycle_conformance.py`. Test-only change; nothing under `engine/` was modified.

## Level claimed

Level P (structure, rules and a real producer run) is claimed **only** for the cases marked conformant below. It is not claimed for the runtime as a whole: the cases marked as findings do not validate and are strict xfails. This is a statement about the draft contract as vendored, not a certification.

## What the tests do

1. Drift check: recompute SHA-256 of every file in `CONTRACT.json`; fail on modified, missing or unlisted files. No dependency on the ODES repository.
2. Load the vendored `validate.py` and validate documents mapped from real runs (synthetic ids and data, synthetic SQLite destinations, no OpenShell).
3. Each case maps what the runtime emits today (rows as stored, result dataclass as is). The runtime emits the legacy export layout; where the contract's new layout would need different output, the case is a finding, not reshaped.
4. Findings are strict xfail (a runtime fix shows up as a failure that tells you to remove the xfail) and a second, non-xfail test pins the exact violation codes reported today.

Route B uses a synthetic subclass of the real `AtomicAuthorityEffectDestination` (real schema, real executor, real `reconcile_claim`) whose atomic claim step writes the same rows as the real one, because the real step needs the Control Plane package, which is not a dependency here. Authority validation is therefore not exercised; the cross-component scenarios cover the real destination.

## Cases

Route names: route A = `PinnedControlPlaneExecutor` / `LocalDestinationExecutor` (`engine/safe_executor.py`), route B = `AtomicLocalControlPlaneExecutor`.

| Case | Status |
|---|---|
| Route A executed | conformant |
| Route A partial | conformant |
| Route A failed | known non-conformance F-P2-RT-2 |
| Route A denied (effect bound to other operation) | known non-conformance F-P2-RT-3 (also F-P2-RT-2) |
| Route A unknown, ack lost, effect observed applied (safe_executor timeout path) | known non-conformance N-1 |
| Route A unknown, malformed ack after commit, OpenShell-style fake destination | known non-conformance N-1 |
| Route A unknown, ack unobserved (observation fails) | known non-conformance F-P2-RT-1 |
| Unknown with null observation | not exercised: no runtime path emits it (asserted by a test) |
| Route B executed | conformant |
| Route B partial | conformant |
| Route B denied (local policy) | known non-conformance F-P2-RT-2 |
| Route B post-commit observation failure, recovery status applied | conformant |
| Route B lost ack, reconciliation down, `unknown` placeholder | conformant |
| Route B effect-free reconcile, result `observed` | conformant |
| Route B recovery status `hold` | not exercised: real code reaches it only with no durable effect, where the atomic step leaves no attempt rows to map |
| Route C (`engine/control_plane_adapter.py`, `control_plane_attempts`, resolution journal) | not exercised: needs the Control Plane package, and the route C name is a qualification term the runtime owner has not confirmed |
| `export_execution_artifacts`, route A normal | conformant |
| `export_execution_artifacts`, route B normal | conformant |
| `export_execution_artifacts`, route B post-commit recovery | known non-conformance F-P2-RT-4 |
| `export_execution_artifacts`, route B effect-free reconcile | known non-conformance F-P2-RT-4 |

## Findings

| Id | Summary | Evidence |
|---|---|---|
| N-1 | Result `unknown` carries a validated observation (`unknown_with_validated_observation`). Owner decided the runtime reports `reconciled`/`partial` (P3). | `engine/safe_executor.py:892-909`; `engine/openshell_environment.py:375-386` |
| F-P2-RT-1 | When the follow-up observation fails the timeout handler fabricates a validated observation with state `unknown` instead of null. The N-1 change does not cover it. | `engine/safe_executor.py:893-897` |
| F-P2-RT-2 | `ExecutionResult.observation` defaults to `{}`; failed and denied results carry `{}`, which the contract (null, validated or recovery observation) refuses (`schema_violation`). | `engine/safe_executor.py:399`, `910-922`; `engine/atomic_local_control_plane_executor.py:104-137` |
| F-P2-RT-3 | Route A records attempt event `denied`, outside the contract's closed attempt-event enum (`unknown_enum_value`). | `engine/safe_executor.py:827`, `843` |
| F-P2-RT-4 | The export puts the recovery report in `observations` and emits no `recovery_observations` (legacy layout, refused by this draft; the runtime moves in P3). | `engine/producer_contract.py:247-249`, `286` |

Note, not a finding: the export carries no `contract`, `contract_version` or `producer` members; the tests add them when mapping.

## Refresh the vendored copy

From a checkout of the ODES repository at the commit to adopt:

```
python contracts/tools/vendor_contract.py copy --contract execution-attempt-lifecycle \
  --dest <runtime>/tests/contracts/execution-attempt-lifecycle --role producer --level P --odes-commit <full sha>
```

Never edit vendored files by hand (the drift test fails). After a refresh, rerun `PYTHONPATH=.:src python -m pytest -q tests`, review any strict xfail that now passes (remove its xfail and update the table above), and update the version and commit in this document.
