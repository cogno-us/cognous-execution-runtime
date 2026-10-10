# O6-P1A operation admission and adapter conformance profile

Status: bounded opt-in qualification profile. It does not replace the default executor, activate refund-intent ownership, issue grants, or perform a destination effect.

Source baseline: `da9f52900ee2596dab087ec6c249ba84915d2b13` (`worker/o6-p1a-operation-admission` was created from this revision).

## Stable operation identity

`engine.operation_admission` defines `cognous-operation-admission/1` around a stable `OperationDiscriminator` issued by a responsible principal within an authority domain. The tuple is:

- `principal_id`
- `authority_domain`
- `operation_id`

A planner cannot create an executable attempt merely by inventing an operation ID. An operation must first be admitted by trusted host/domain code with a fixed operation commitment. Re-admitting the same discriminator with different content is a collision and fails closed.

Attempts are subordinate to the admitted operation and use their own `attempt_id`. Attempt IDs are never operation IDs. Reuse under a different operation is rejected.

## Replan and supersession

A changed plan does not mutate an existing stable discriminator. A replan requires a newly domain-issued discriminator and may explicitly name the prior operation in `supersedes`.

Supersession:

- stays inside the same principal and authority domain;
- requires the predecessor to already exist;
- is one-successor-only in this bounded profile;
- closes the predecessor to new attempts.

The registry does not infer semantic equivalence or business intent. Two independent valid operations remain distinct regardless of admission order.

## Adapter conformance declaration

The profile records, without granting authority:

- adapter identity;
- provider ID-reuse semantics;
- client-reference searchability;
- dedupe mode;
- deadline semantics;
- observation coverage across `applied`, `partial`, `absent`, and `unknown`;
- provider observation watermark;
- provider-acceptance closure.

Qualification fails closed when any required declaration is absent or insufficient. This is a conformance inventory, not proof of a live provider implementation.

## Retry and observation rule

The accepted C5 contract remains controlling: acknowledgement is not destination proof, and absence after an uncertain attempt is not retry permission.

Accordingly, this profile never treats `absent` as positive retry evidence. `evaluate_retry()` is conservative for every provider-acceptance state:

- accepted -> reconcile the existing attempt;
- unknown -> hold and reconcile;
- rejected -> closes that provider submission, but still does not auto-authorize a new attempt;
- absent observation -> never permits retry by itself.

Any later retry policy must preserve the original operation identity, create a distinct attempt, and be separately authorized by the responsible principal/domain policy. No such automatic retry policy is implemented here.

## Test scope

`tests/test_o6_p1a_operation_admission.py` qualifies:

- domain-issued stable operation admission;
- unadmitted/planner-minted operation refusal;
- operation discriminator collision;
- replan via explicit new discriminator;
- cross-principal/domain and forked supersession refusal;
- attempt-ID collision across operations;
- two independent valid operation orders;
- fail-closed adapter declaration checks;
- provider acceptance closure requirement;
- absence never authorizing retry.

Scoped CI runs only this test module plus Python compile checks for the new operation-admission module.

## Limitations

- Registry state is in-memory qualification state, not a durable or distributed operation ledger.
- Principal/domain issuance is a trust input; this module does not authenticate an external issuer.
- No provider SDK or live provider is called; capability declarations are synthetic conformance fixtures.
- Watermark syntax and provider-specific finality are intentionally opaque strings in this bounded layer.
- The profile does not change `PinnedControlPlaneExecutor`, `DurableRefundDestination`, refund-intent selection, atomic local authority/effect behavior, or default dispatch paths.
- Provider rejection is not itself new authority. Automatic retry remains unimplemented.
