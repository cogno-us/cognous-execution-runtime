# W0 contract addendum: C3 capability profile, C5 observation/recovery, C7 stop

Status: W0 candidate contract. Contract definitions only; no downstream W2/W6 behavior is implemented here.

## Baseline
Inspected runtime main: `489875c98a3592c0ebe0f939b6466cbd2447acd1`.
The selected hub pin remains `c3c3ee7188b9367cf70b08074b9c40a5c70c94ac`; W0 does not advance it.

## C3 capability/profile identity
A protected optional profile declares:
- `profile_id`, `profile_generation`
- implementation/package/image identity and exact revision/digest
- upstream revision when applicable
- configuration/policy digest
- supported protected operations/hooks
- required capabilities
- explicitly unavailable/unsupported mappings.

Configured identity and observed executable identity are distinct evidence fields. Unknown generation, unsupported protected mapping, missing capability or failed initialization blocks that profile. A matching name is insufficient.

Core execution remains runnable without OpenShell, OpenAPPA, Microsoft AGT or Cedar.

## C5 effect / observation / recovery
Retain existing effect and attempt identities. Observation metadata for the selected local SQLite destination includes:
- effect ID
- observation state: `applied|partial|absent|unknown`
- authoritative destination scope
- observed-at timestamp
- freshness policy/evaluation
- finality claim, if any, explicitly scoped
- accepted/rejected status and reasons.

Acknowledgement is not destination proof. Timeout, unavailable observation or fresh absence after an uncertain prior attempt does not authorize retry. Recovery is hold-only until the original effect is reconciled under the accepted destination contract. Restart preserves the original effect and prior attempts.

## C7 stop/intervention
The selected profile records separately:
`stop_requested`, `stop_acknowledged`, `new_dispatch_closed`, `quiescence_observed`, `destination_reconciled`.
The contract names one owner, one stop entry point, the selected worker population, admission boundary and observation window. Restart requires current authority.

A stop request or acknowledgement does not prove cessation, rollback or non-occurrence. Undispatched preventable work stops; admitted or unknown work remains subject to reconciliation. Prior committed effects remain visible.

W2 owns core stop semantics. W6 owns OpenShell environment-specific qualification. No fleet/global cancellation is introduced.
