# O6-P1D durable revocation disposition and original-attempt closeout

Status: bounded opt-in runtime evidence profile. Source baseline: `cd95f8cb0591ce2db08a995e1166f0773bc3922e`.

## Scope

`engine.revocation_closeout` is a sidecar durability profile. It does not replace or alter the default executor, operation admission, refund-intent selection, or the optional same-host C1 authority/effect profile. It issues no grants and performs no external effects.

Each retained runtime item is bound to an exact `grant_id` and `grant_revision`, plus decision/effect identity and an authoritative ordering reference. Revocation sweep is exact on grant ID and revision.

Disposition is item-specific:

- `refused_before_execution`: queued work whose recorded ordering precedes or equals revocation.
- `completed_before_revocation`: completion has authoritative ordering strictly before revocation.
- `in_doubt`: executing work, ambiguous ordering, or any completion not proven to precede revocation.

Every `in_doubt` row requires a named owner, deadline, and reconciliation reference. The overdue report exposes count plus oldest/youngest age; it does not manufacture resolution.

## Original-attempt closeout

Lost acknowledgement or process restart does not create a replacement effect. The original retained `attempt_id` is closed only from an authoritative `applied` or `partial` destination observation carrying an observation reference. `absent` and `unknown` remain unresolved and always return `retry_eligible=False`.

This is closeout evidence, not retry authorization. No API in this profile mints a new effect or attempt.

## Ordering and C0 limitation

The profile records ordering evidence; it does not create destination commit atomicity. A C0 post-check/pre-commit race is intentionally represented as `in_doubt` when revocation can interleave with an already-executing item. C1 remains optional, same-host, and unchanged. No C2/C3 guarantee is claimed.

## Test scope

`tests/test_o6_p1d_revocation_closeout.py` covers queued/executing/completed sweep, simultaneous distinct-grant revocation attribution, C0 post-check/pre-commit ambiguity, crash/restart durability, absent/unknown observations, mandatory in-doubt accountability fields, overdue reporting, and mutation controls that disable each evidence writer.

The mutation-control tests are designed to fail if evidence persistence is bypassed: disabling a writer raises before a qualifying record can be produced.

## Data minimization

The profile stores identifiers, grant ID/revision, statuses, ordering references, owner/deadline/reconciliation references, and observation references. It stores no raw declined payload or payment material.

## Limitations

- Ordering evidence is a trusted runtime input; this module does not authenticate an external authority log or observer.
- The sidecar is SQLite-local durability, not a distributed revocation service.
- `completed_before_revocation` proves only the supplied authoritative ordering relationship.
- `in_doubt` requires reconciliation; the profile does not resolve a destination outcome itself.
- No external payment processor, production credentials, grant issuance, C2/C3 atomicity, or global/fleet revocation is implemented.
