# Recovery resolution prototype changelog

## synthetic-0.1 draft

- Add an explicitly opted-in synthetic append-only observation ledger, durable current projection and recovery accounting for Runtime issue [46](https://github.com/cogno-us/cognous-execution-runtime/issues/46). Thanks @titanicprime.
- Add focused qualification and v1 regression CI. Contract review, production admission, consumer integration and release adoption remain pending.

- Harden current-projection reads against changed or ambiguous original identity in one read snapshot; add three failing-before-fix regressions and three abrupt-process-exit transaction tests for [PR 47](https://github.com/cogno-us/cognous-execution-runtime/pull/47). Thanks @titanicprime.

- Add a read-only inspection export with one SQLite snapshot, retained original uncertainty and event history, explicit legacy provenance gaps and synthetic limitations; seven focused cases for [PR 47](https://github.com/cogno-us/cognous-execution-runtime/pull/47). Thanks @titanicprime.
