# Executor producer profile 1.0.0

The supported executor evidence export is defined by
`engine.producer_profile`.

Profile identifier: `cognous.moltbot-safe.executor`  
Profile version: `1.0.0`

The profile covers:

- Execution Envelope 0.2.0;
- execution result;
- effect-scoped destination rows;
- effect-scoped attempts;
- append-only attempt events;
- producer-reported observation.

The profile version describes record semantics. It is not a repository revision.
Every export also carries the exact `cogno-us/moltbot-safe` repository revision
supplied by the integrating application.

`provenance.source_asserted=true` means the exporting runtime asserts that the
records came from its configured destination. It does not establish independent
verification. `provenance.independently_established` must remain false unless a
separate verifier actually established provenance.

The export function does not construct authority, grant permission, make an
authorization decision, or execute an effect. Callers must supply an already
authorized ExecutionEnvelope/result and an existing destination.

Legacy integrations that consumed unversioned executor dictionaries remain
revision-pinned. Historical records must not be relabeled as profile 1.0.0.
