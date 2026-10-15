# Durable effect-time refusal capture

The optional bounded Control Plane adapter retains a refusal only after the
current call's `append_refusal` returns successfully. A fresh workflow view and
capture wrapper are created per execution; the caller's workflow/destination is
not rebound and no shared-store last-record scan is performed. A deep copy of
that call's actual typed record is returned under
`ExecutionResult.control_plane_evidence.refusal` and preserved by the existing
producer 2.0.0 export. Existing reconciliation evidence remains separate.

The record preserves decision/effect identity, typed reasons, actual resolution
inputs, changed-input versions, original/current operation horizons, time source
and recording time. No reason or authorization binding is synthesized. A failed
required store write cannot create refusal evidence: I/O failure propagates;
a PermissionError produces denial without a purported durable refusal. Neither
path dispatches a destination effect.

Qualification pins CP `c7f122ba3a6e39f9268a7163c99be3ee514cd97e` and Manifest
`46c950bed37fe3812000895430bc0312d29e37ce`. Seven new cases cover grant revocation,
approval withdrawal, policy change, stale evidence, two write failures and two
concurrent calls on one executor/store. A barrier forces both durable writes
before either result is returned, exposing cross-call last-record attribution.
The fixture service clock is explicitly set to the dated synthetic fixture.
`durable-refusal.yml` runs the seven cases on Python 3.11 and 3.12. Historical
`python-safety.yml` retains its old CP pin and explicitly excludes this separate
new-interface suite; it does not claim to qualify the new CP interface.

Local Python qualification: seven new cases passed; historical Python suite
359 passed, 57 existing skips and nine separate subtests passed. The full Node
lint command was attempted but dependency bootstrap failed under the installed
pnpm supply-chain policy (`ERR_PNPM_EXOTIC_SUBDEP` for upstream libsignal); no
Node lint/build/test success is asserted. No dependency policy is weakened.

This repairs evidence propagation only. Consumer migration and actual merged-pin
candidate qualification remain necessary. The hub's selected profiles/lock are
unchanged; the candidate stays nonselected. C0 retains its check-to-commit race;
optional C1 is cooperative same-host only, no C2/C3 or real settlement is claimed.
Publication/production authorization and operational trust remain HOLD.
