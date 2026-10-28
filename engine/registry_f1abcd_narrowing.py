"""M3: compose a caller `LocalExecutionPolicy` with an authenticated Registry `f1abcd` (r08-v4) selection, then evaluate with the existing evaluator.

BUILDER_TESTED developer implementation. NOT independently qualified. NOT an authorization mechanism: a result is non-authorizing and grants nothing. It
adds no destination, SQLite, file, network or subprocess effect and does not modify `engine/safe_executor.py` (the target evaluator is reused unchanged).

Same fail-closed shape as `registry_f1abc_narrowing`, extended to the two target members:
  * five allow-sets and two ceilings exactly as in f1abc (subset / `min`, re-checked against the caller);
  * `allowed_target_prefixes` + `target_match`: Registry composes with the sound containment rule (mode = stricter; prefixes = entries contained in the
    caller's accepted set and in the Registry's). The adapter re-checks that EVERY effective prefix, under the effective mode, is contained in some
    caller prefix under the caller mode (`effective_policy_widens_caller` otherwise), so the accepted target set never exceeds the caller's;
  * an empty effective prefix list, any empty set or a Registry `max_effects` of 0 is an explicit deny-all refusal BEFORE any policy object is built;
  * the narrowed policy is `dataclasses.replace(caller, <five frozensets>, max_amount, max_effects, allowed_target_prefixes, target_match)`;
  * the nine-member Registry digest of the narrowed policy must equal the digest of Registry's effective policy;
  * the existing `LocalExecutionPolicy.check(op)` then decides.
The caller must carry an explicit `target_match` of a Registry mode (a `LocalExecutionPolicy` always has one; its default `segment` is the caller's own value).
Registry is resolved lazily from the host environment (a pinned checkout), never vendored.
"""
from __future__ import annotations

import dataclasses
import importlib
from typing import Any, Mapping

from engine.registry_f1a_narrowing import FIELDS, RegistryNarrowingResult, _PARTICIPATION, _refuse
from engine.registry_f1abc_narrowing import AMOUNT_MAX, CEILINGS, EFFECTS_MAX, _caller_ceilings_defect
from engine.safe_executor import FrozenOperation, LocalExecutionPolicy

TARGETS = ("allowed_target_prefixes", "target_match")


def _valid_effective(eff: Any) -> bool:
    if not isinstance(eff, Mapping) or set(eff) != set(FIELDS) | set(CEILINGS) | set(TARGETS):
        return False
    if any(type(eff[n]) is not tuple or not all(type(t) is str and t for t in eff[n]) for n in FIELDS):
        return False
    if type(eff["allowed_target_prefixes"]) is not tuple or not all(type(t) is str and t for t in eff["allowed_target_prefixes"]):
        return False
    return all(type(eff[n]) is int for n in CEILINGS) and type(eff["target_match"]) is str


def build_narrowed_policy(caller_policy: LocalExecutionPolicy, effective: Mapping[str, Any]) -> LocalExecutionPolicy:
    """The only place an executable composite policy is constructed. Five sets, two ceilings, prefixes and mode change; nothing else."""
    return dataclasses.replace(caller_policy, **{n: frozenset(effective[n]) for n in FIELDS}, max_amount=effective["max_amount"],
                               max_effects=effective["max_effects"], allowed_target_prefixes=tuple(effective["allowed_target_prefixes"]),
                               target_match=effective["target_match"])


def evaluate_with_registry_selection_f1abcd(caller_policy: LocalExecutionPolicy, op: FrozenOperation, *, participation: Any, record_text: Any,
                                            selection_text: Any, context: Any, trust: Any, verifier: Any, now: Any) -> RegistryNarrowingResult:
    if (type(caller_policy) is not LocalExecutionPolicy or type(op) is not FrozenOperation or type(participation) is not str
            or participation not in _PARTICIPATION or type(record_text) is not str or type(selection_text) is not str):
        return _refuse("input_invalid")
    try:
        caller_policy = dataclasses.replace(caller_policy)  # private snapshot of every field, taken before any Registry code runs
        defect = _caller_ceilings_defect(caller_policy)
        caller = {n: sorted(getattr(caller_policy, n)) for n in FIELDS}
        caller.update(max_amount=caller_policy.max_amount, max_effects=caller_policy.max_effects,
                      allowed_target_prefixes=sorted(set(caller_policy.allowed_target_prefixes)), target_match=caller_policy.target_match)
    except Exception:
        return _refuse("input_invalid")
    if defect is not None:
        return _refuse(defect)
    try:
        decision_mod = importlib.import_module("registry_successor.v4_decision")
        base_mod = importlib.import_module("registry_successor.decision")
        identity_mod = importlib.import_module("registry_successor.v4_policy_identity")
        family_mod = importlib.import_module("registry_successor.v4_f1abcd")
        verifier_cls = importlib.import_module("registry_adapters.ed25519_verifier").Ed25519Verifier
    except Exception:
        return _refuse("registry_unavailable")
    if type(verifier) is not verifier_cls:
        return _refuse("verifier_not_admitted")
    try:
        decision = decision_mod.verify_and_decide_v4(participation, caller, record_text, selection_text, context, trust, verifier, now)
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
    if (any(not effective[n] for n in FIELDS) or effective["max_effects"] < 1 or not effective["allowed_target_prefixes"]):
        return _refuse("registry_deny_all", registry_kind=kind)  # explicit deny-all: no executable policy object is built
    contained = family_mod.contained
    if (any(not set(effective[n]) <= set(caller[n]) for n in FIELDS) or effective["max_amount"] > caller["max_amount"]
            or effective["max_effects"] > caller["max_effects"] or not 0 <= effective["max_amount"] <= AMOUNT_MAX
            or effective["max_effects"] > EFFECTS_MAX or effective["target_match"] not in family_mod.MODES
            or caller["target_match"] not in family_mod.MODES
            or any(not any(contained(effective["target_match"], e, caller["target_match"], c) for c in caller["allowed_target_prefixes"])
                   for e in effective["allowed_target_prefixes"])):
        return _refuse("effective_policy_widens_caller", registry_kind=kind)
    narrowed = build_narrowed_policy(caller_policy, effective)
    try:
        family = family_mod.FAMILY_F1ABCD

        def members(sets, amount, effects, prefixes, mode):
            return {**{n: sorted(sets[n]) for n in FIELDS}, "max_amount": amount, "max_effects": effects,
                    "allowed_target_prefixes": sorted(prefixes), "target_match": mode}

        used = identity_mod.effective_policy_digest(family, members({n: getattr(narrowed, n) for n in FIELDS}, narrowed.max_amount, narrowed.max_effects,
                                                                    narrowed.allowed_target_prefixes, narrowed.target_match))
        registry_digest = identity_mod.effective_policy_digest(family, members(effective, effective["max_amount"], effective["max_effects"],
                                                                               effective["allowed_target_prefixes"], effective["target_match"]))
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
