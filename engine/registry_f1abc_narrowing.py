"""M2 PR-3: compose a caller `LocalExecutionPolicy` with an authenticated Registry `f1abc` (r08-v3) selection, then evaluate with the existing evaluator.

BUILDER_TESTED developer implementation. NOT independently qualified. NOT an authorization mechanism: a result is non-authorizing and grants nothing. It
adds no destination, SQLite, file, network or subprocess effect and does not modify `engine/safe_executor.py`.

Same fail-closed shape as `registry_f1a_narrowing` (raw inputs only; Registry's one public r08-v3 entry `verify_and_decide_v3`; only `kind == "applied"`
proceeds), extended to the two numeric ceilings:
  * five allow-sets: intersection (Registry composes; the result is re-checked to be a subset of the caller's sets);
  * `max_amount = min(caller, Registry)` and `max_effects = min(caller, Registry)`; the result is re-checked to be `<=` the caller's, never above;
  * the caller ceilings must be EXACT ints (a float, bool or other type is refused before Registry runs: `caller_float_unsupported` /
    `caller_ceiling_invalid`); `max_amount` 0..2**53, `max_effects` >= 1 (the existing policy rule);
  * a Registry `max_effects` of 0 (or any empty set) is an explicit deny-all refusal BEFORE any executable policy object is built;
  * the narrowed policy is `dataclasses.replace(caller, <five frozensets>, max_amount, max_effects)`; target prefixes and `target_match` are preserved;
  * the seven-member Registry digest of the narrowed policy must equal the digest of Registry's effective policy;
  * the existing `LocalExecutionPolicy.check(op)` then decides (binary64 amount comparison and effect-count rules are unchanged).
Registry is resolved lazily from the host environment (a pinned checkout), never vendored.
"""
from __future__ import annotations

import dataclasses
import importlib
from typing import Any, Mapping

from engine.registry_f1a_narrowing import CLASSIFICATION, FIELDS, RegistryNarrowingResult, _PARTICIPATION, _refuse
from engine.safe_executor import FrozenOperation, LocalExecutionPolicy

CEILINGS = ("max_amount", "max_effects")
AMOUNT_MAX = 2 ** 53
EFFECTS_MAX = 2 ** 53 - 1


def _caller_ceilings_defect(policy: LocalExecutionPolicy):
    amount, effects = policy.max_amount, policy.max_effects
    if type(amount) is float or type(effects) is float:
        return "caller_float_unsupported"
    if type(amount) is not int or type(effects) is not int or not 0 <= amount <= AMOUNT_MAX or not 1 <= effects:
        return "caller_ceiling_invalid"
    return None


def _valid_effective(eff: Any) -> bool:
    if not isinstance(eff, Mapping) or set(eff) != set(FIELDS) | set(CEILINGS):
        return False
    if any(type(eff[n]) is not tuple or not all(type(t) is str and t for t in eff[n]) for n in FIELDS):
        return False
    return all(type(eff[n]) is int for n in CEILINGS)


def build_narrowed_policy(caller_policy: LocalExecutionPolicy, effective: Mapping[str, Any]) -> LocalExecutionPolicy:
    """The only place an executable composite policy is constructed. Five sets and two ceilings change; nothing else."""
    return dataclasses.replace(caller_policy, **{n: frozenset(effective[n]) for n in FIELDS}, max_amount=effective["max_amount"],
                               max_effects=effective["max_effects"])


def evaluate_with_registry_selection_f1abc(caller_policy: LocalExecutionPolicy, op: FrozenOperation, *, participation: Any, record_text: Any,
                                           selection_text: Any, context: Any, trust: Any, verifier: Any, now: Any) -> RegistryNarrowingResult:
    if (type(caller_policy) is not LocalExecutionPolicy or type(op) is not FrozenOperation or type(participation) is not str
            or participation not in _PARTICIPATION or type(record_text) is not str or type(selection_text) is not str):
        return _refuse("input_invalid")
    try:
        caller_policy = dataclasses.replace(caller_policy)  # private snapshot of every field, taken before any Registry code runs
        defect = _caller_ceilings_defect(caller_policy)
        caller = {n: sorted(getattr(caller_policy, n)) for n in FIELDS}
        caller.update(max_amount=caller_policy.max_amount, max_effects=caller_policy.max_effects)
    except Exception:
        return _refuse("input_invalid")
    if defect is not None:
        return _refuse(defect)
    try:
        decision_mod = importlib.import_module("registry_successor.v3_decision")
        base_mod = importlib.import_module("registry_successor.decision")
        identity_mod = importlib.import_module("registry_successor.v3_policy_identity")
        family_mod = importlib.import_module("registry_successor.v3_f1abc")
        verifier_cls = importlib.import_module("registry_adapters.ed25519_verifier").Ed25519Verifier
    except Exception:
        return _refuse("registry_unavailable")
    if type(verifier) is not verifier_cls:
        return _refuse("verifier_not_admitted")
    try:
        decision = decision_mod.verify_and_decide_v3(participation, caller, record_text, selection_text, context, trust, verifier, now)
    except Exception:
        return _refuse("registry_call_failed")
    if not isinstance(decision, base_mod.PolicyDecision) or type(decision.kind) is not str or decision.operationally_authorized is not False:
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
    if any(not effective[n] for n in FIELDS) or effective["max_effects"] < 1:
        return _refuse("registry_deny_all", registry_kind=kind)  # explicit deny-all: no executable policy object is built
    if (any(not set(effective[n]) <= set(caller[n]) for n in FIELDS) or effective["max_amount"] > caller["max_amount"]
            or effective["max_effects"] > caller["max_effects"] or not 0 <= effective["max_amount"] <= AMOUNT_MAX
            or effective["max_effects"] > EFFECTS_MAX):
        return _refuse("effective_policy_widens_caller", registry_kind=kind)
    narrowed = build_narrowed_policy(caller_policy, effective)
    try:
        family = family_mod.FAMILY_F1ABC
        used = identity_mod.effective_policy_digest(family, {**{n: sorted(getattr(narrowed, n)) for n in FIELDS},
                                                            "max_amount": narrowed.max_amount, "max_effects": narrowed.max_effects})
        registry_digest = identity_mod.effective_policy_digest(family, {**{n: sorted(effective[n]) for n in FIELDS},
                                                                       "max_amount": effective["max_amount"], "max_effects": effective["max_effects"]})
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
