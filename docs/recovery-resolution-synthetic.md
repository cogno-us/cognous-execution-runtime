# Synthetic recovery resolution prototype

Status: experimental draft for [Runtime issue 46](https://github.com/cogno-us/cognous-execution-runtime/issues/46). The [orchestrator contract proposal](https://github.com/cogno-us/cognous-stack-orchestrator/pull/93) is still under review. This prototype makes design decisions testable; it is not acceptance or implementation of the entire proposed contract.

## Explicit opt-in

`engine.recovery_resolution.SyntheticRecoveryLedger(existing_store.path, synthetic=True)` opens an existing `RevocationCloseoutStore` database. It adds only `recovery_synthetic_events`, `recovery_synthetic_projection`, their index and append-only triggers. Nothing imports this module automatically. Existing v1 writers, closeouts and historical overdue reports remain unchanged.

The profile is `cognous-recovery-resolution/synthetic-0.1`. There is no production admission mode. The database is one trusted local scope; it supplies no tenant authentication. Use synthetic fixtures only. Do not submit secrets or real evidence. References are never fetched. Reference presence, content digests, counters, coverage and finality flags are synthetic assertions, not proof of destination truth.

## API and provisional rules

Construct a `SyntheticObservation` containing original effect/attempt identity, adapter/client binding, state, timezone-aware observation time, ordering domain/counter, observation reference/digest, observer, watermark, provider acceptance and protected evidence location references. Unavailable synthetic evidence must be labeled explicitly, for example `synthetic:unavailable`; this does not establish admission assurance.

Call `append(event, expected_revision=n, predecessor_digest=digest)`. For the first event use `0` and `None`. Persist the submitted envelope for exact replays. The return value separates the immutable original `admission` from the latest `current` projection. `history(attempt_id)` returns retained envelopes and receipts in local revision order; `current(attempt_id)` returns a zero-revision awaiting-observation projection when there is no stream. That default is not evidence that the attempt exists; append validates retained identity.

Canonicalization `python-json-sort-ascii-compact/1` uses Python JSON, sorted keys, ASCII escaping, compact separators, no NaN, and UTC ISO timestamps. It commits the profile, canonicalization version, every submitted observation field, expected revision and predecessor. Receipt fields are excluded. This is a deliberately Python-specific draft format, not RFC 8785. Receipts retain the original v1 identity, policy version, local time, digest, kind and synthetic assurance. No authentication or signature verification is implied.

`BEGIN IMMEDIATE` serializes event append and projection replacement in one SQLite transaction. Stale revisions/predecessors raise HOLD without writing; the caller must re-read and re-evaluate evidence. An exact duplicate returns its historical receipt plus the current projection, including any later conflict. Same-ID changed content at the current revision appends a minimized disagreement commitment. Reuse across attempts is rejected. Append-only triggers protect against accidental UPDATE/DELETE through ordinary SQL; they are not protection against a privileged database editor.

The synthetic policy admits structurally complete observations from the previous 24 hours, with no future clock allowance. Counters must strictly increase within the same declared domain/adapter/client binding. These checks test mechanics only. Missing/ambiguous identities and malformed or stale observations raise exceptions without favorable promotion; rejected-input audit persistence is not implemented.

Applied plus complete coverage and finality yields `resolved` **only in this synthetic profile**. Partial remains `partial_followup`; unknown/absent and incomplete applied remain `awaiting_finality`. Contradictory, incomparable or regressive evidence enters permanent `conflict_review`. Historical applied evidence is retained. No adjudication or source-correction escape exists. All projections keep retry false, execution authority none and settlement verification false.

## Accounting and legacy behavior

`metrics(now=...)` uses one read transaction and reports its evaluation time. It starts with all v1 in-doubt dispositions, including attempts with no observation. Attributable attempts occupy exclusive work-state buckets. Only unresolved attributable attempts contribute to current overdue; all dispositions remain in the historical count. Missing/ambiguous bindings are reported as unattributable effects, not fabricated attempts. Invalid/missing deadlines are reported for attributable unresolved attempts.

Legacy closeouts are not imported, upgraded or modified. A legacy-only closed row remains awaiting observation in the synthetic profile. The physical SQLite file changes when new tables are created, but v1 rows and interpretation do not. No legacy migration or consumer mapping is claimed.

## Validation and remaining work

Run:

```sh
python -m pytest -q tests/test_recovery_resolution.py tests/test_o6_p1d_revocation_closeout.py
```

Tests cover retained provenance and restart by reopening, atomic rollback under an injected projection failure, independent concurrent SQLite connections, duplicate after conflict, ID collisions, revision/predecessor binding, canonical timezone normalization, partial/finality semantics, invalid input, writer/append-only controls, current accounting, identity ambiguity and unchanged v1 regressions. Reopening is not a power-loss or process-kill qualification.

Remaining before any stronger successor acceptance: owning-repository contract review; authenticated sources and trusted scope; provider-specific ordering/finality/coverage and evidence commitments; complete admission decision traces and rejection audit; reviewed conflict adjudication; legacy migration; Replay/Evidence Pack mappings; orchestrator integration; process/power-loss and deployment qualification. This draft does not pass the full RR-01 through RR-14 proposal matrix.

C0 check-to-commit race remains; optional C1 remains same-host only. No C2/C3, dispatch, replacement attempt, settlement or release selection is added. [Operational trust issue 30](https://github.com/cogno-us/cognous-stack-orchestrator/issues/30) remains HOLD.
