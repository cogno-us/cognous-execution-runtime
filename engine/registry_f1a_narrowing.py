"""V01 / M1: narrow a caller `LocalExecutionPolicy` with an authenticated Registry `f1ab` selection, then evaluate with the existing evaluator.

BUILDER_TESTED developer demonstration. NOT independently qualified. NOT an authorization mechanism: a result from this module is
non-authorizing, grants nothing, and is not an approval, a grant, an activation or a dispatch. It adds no destination, executor, SQLite,
file, network or subprocess effect, and it does not modify `engine/safe_executor.py`.

Behavior (fail closed at every step):
  1. Inputs are RAW values only (record text, selection text, context, trust, verifier, now). No PolicyDecision, verification report,
     authenticated flag, authorization claim or digest is accepted, so a decision object cannot be forged through this interface.
  2. Registry's one public entry `verify_and_decide_v2` (hardwired `r08-v2`, built-in Ed25519 key admission) is called once.
  3. Only `kind == "applied"` proceeds. `required_policy_unavailable`, `overlay_not_applied` and `applied_deny_all` are refusals; an optional
     participation that Registry did not apply NEVER falls back to the caller policy here.
  4. The effective five sets are re-validated (exact keys, tuples of non-empty `str`, non-empty, subset of the caller's sets).
     An empty set is an explicit deny-all refusal BEFORE any executable policy object is built.
  5. The narrowed policy is `dataclasses.replace(caller_policy, <five frozensets>)`; target prefixes, amount, effect limits and `target_match`
     are preserved by construction.
  6. The Registry canonical effective-policy digest is computed from the exact five sets the narrowed policy carries and from Registry's
     effective sets; a mismatch (or any failure to compute) is a refusal, never a warning. This is an internal consistency check (Registry's
     decision carries no digest of its own), not an independent attestation.
  7. The existing `LocalExecutionPolicy.check(op)` decides the operation.

Trust boundary: `trust` (the trust snapshot) is host-supplied configuration and is taken as given; the verifier must be exactly Registry's
`Ed25519Verifier` (any other object is refused as `verifier_not_admitted`), so a caller cannot inject an always-true verifier. "Authenticated"
therefore means authenticated against the host's trust snapshot with real Ed25519, nothing more. The caller policy is snapshotted before any
Registry code runs, so later mutation of the caller's object cannot change the policy that is narrowed.

Registry is NOT vendored and is NOT a production dependency of this package: it is resolved lazily from the host environment (a pinned
checkout made importable by the host; see `tests/test_registry_f1a_narrowing.py`). If it cannot be imported the result is a refusal (`registry_unavailable`).
Caller policy values must satisfy Registry's token rule, otherwise Registry refuses (`composition_caller_invalid`).
"""
from __future__ import annotations

import dataclasses
import importlib
from typing import Any, Mapping, Optional

from engine.safe_executor import FrozenOperation, LocalExecutionPolicy

CLASSIFICATION = "BUILDER_TESTED developer demonstration; non-authorizing; no operational authorization; HOLD; not production"
FIELDS = ("allowed_actions", "allowed_adapters", "allowed_units", "allowed_institutions", "allowed_authority_domains")
_PARTICIPATION = ("required", "optional")


@dataclasses.dataclass(frozen=True)
class RegistryNarrowingResult:
    allowed: bool
    adapter_refusal: Optional[str] = None      # refusal produced by this adapter (closed vocabulary below)
    evaluator_refusal: Optional[str] = None    # the existing evaluator's PermissionError message
    registry_code: Optional[str] = None        # Registry's typed refusal code when Registry did not apply the selection
    registry_kind: Optional[str] = None        # Registry PolicyDecision.kind
    effective_policy_digest: Optional[str] = None
    non_authorizing: bool = True
    classification: str = CLASSIFICATION


def _refuse(adapter_refusal: Optional[str] = None, *, registry_code: Optional[str] = None, registry_kind: Optional[str] = None) -> RegistryNarrowingResult:
    return RegistryNarrowingResult(False, adapter_refusal, None, registry_code, registry_kind, None)


def _valid_effective(eff: Any) -> bool:
    if not isinstance(eff, Mapping) or set(eff) != set(FIELDS):
        return False
    for name in FIELDS:
        value = eff[name]
        if type(value) is not tuple or not all(type(t) is str and t for t in value):
            return False
    return True


def _build_narrowed_policy(caller_policy: LocalExecutionPolicy, effective: Mapping[str, Any]) -> LocalExecutionPolicy:
    """The only place an executable narrowed policy is constructed. Only the five set fields change."""
    return dataclasses.replace(caller_policy, **{name: frozenset(effective[name]) for name in FIELDS})


def evaluate_with_registry_selection(caller_policy: LocalExecutionPolicy, op: FrozenOperation, *, participation: Any, record_text: Any,
                                     selection_text: Any, context: Any, trust: Any, verifier: Any, now: Any) -> RegistryNarrowingResult:
    if (type(caller_policy) is not LocalExecutionPolicy or type(op) is not FrozenOperation or type(participation) is not str
            or participation not in _PARTICIPATION or type(record_text) is not str or type(selection_text) is not str):
        return _refuse("input_invalid")
    try:
        caller_policy = dataclasses.replace(caller_policy)  # private snapshot of every field, taken before any Registry code runs
        caller = {name: sorted(getattr(caller_policy, name)) for name in FIELDS}
    except Exception:
        return _refuse("input_invalid")
    try:
        decision_mod = importlib.import_module("registry_successor.decision")
        identity_mod = importlib.import_module("registry_successor.v2_policy_identity")
        family_mod = importlib.import_module("registry_successor.v2_f1ab")
        verifier_cls = importlib.import_module("registry_adapters.ed25519_verifier").Ed25519Verifier
    except Exception:
        return _refuse("registry_unavailable")
    if type(verifier) is not verifier_cls:
        return _refuse("verifier_not_admitted")
    try:
        decision = decision_mod.verify_and_decide_v2(participation, caller, record_text, selection_text, context, trust, verifier, now)
    except Exception:
        return _refuse("registry_call_failed")
    if not isinstance(decision, decision_mod.PolicyDecision) or type(decision.kind) is not str or decision.operationally_authorized is not False:
        return _refuse("registry_decision_invalid")
    kind = decision.kind
    if kind in ("required_policy_unavailable", "overlay_not_applied"):
        return _refuse("registry_not_applied", registry_code=decision.code, registry_kind=kind)
    if kind == "applied_deny_all":
        return _refuse("registry_deny_all", registry_kind=kind)
    if kind != "applied" or decision.deny_all is not False:
        return _refuse("registry_decision_invalid", registry_kind=kind)
    effective = decision.effective
    if not _valid_effective(effective):
        return _refuse("effective_policy_invalid", registry_kind=kind)
    if any(not effective[name] for name in FIELDS):
        return _refuse("registry_deny_all", registry_kind=kind)  # explicit deny-all: no executable policy object is built
    if any(not set(effective[name]) <= set(caller[name]) for name in FIELDS):
        return _refuse("effective_policy_widens_caller", registry_kind=kind)
    narrowed = _build_narrowed_policy(caller_policy, effective)
    try:
        family = family_mod.FAMILY_F1AB
        used = identity_mod.effective_policy_digest(family, {name: sorted(getattr(narrowed, name)) for name in FIELDS})
        registry_digest = identity_mod.effective_policy_digest(family, {name: sorted(effective[name]) for name in FIELDS})
    except Exception:
        return _refuse("effective_policy_digest_unavailable", registry_kind=kind)
    if type(used) is not str or used != registry_digest:
        return _refuse("effective_policy_digest_mismatch", registry_kind=kind)
    try:
        narrowed.check(op)
    except PermissionError as exc:
        return RegistryNarrowingResult(False, None, str(exc), None, kind, used)
    except Exception:
        return RegistryNarrowingResult(False, "evaluator_error", None, None, kind, used)
    return RegistryNarrowingResult(True, None, None, None, kind, used)
