# F-A1 authorization pin: integration record (candidate, non-authorizing)

Status: BUILDER_TESTED. Not independently qualified. No security clearance, certification or production readiness is claimed.
Governance: non-authorizing; HOLD for release, publication and production. Destination remains synthetic SQLite only.

This record accompanies the consolidated Runtime change that merges the trusted installer (`host/authorization_pin/`) and the Runtime guard
(`engine/authorization_pin.py`, `engine/local_authority_effect.py`, `engine/registry_selection_authority.py`) under the Phase 0 freeze
(`docs/authorization-pin-interface-freeze.md`, `engine/authorization_pin_format.py`). Normative contract: Registry r08-v2 Amendment 4
(Registry `1a4de32605519c505142b1ba3a57fa5b259b0b2c`). Nothing here reopens that amendment.

## Integration decisions

1. **Store layout.** The freeze did not fix columns. The installer writes `authorization_pin_v1(pin_epoch, decision_id, status, row_json,
   pin_digest)` and `authorization_pin_meta_v1(key, value)` (`profile`, `pin_store_id`). The guard reader selects only `pin_epoch`, `decision_id`
   and `row_json` and re-derives every digest from the row, so the extra columns are redundant and are not trusted. Verified by
   `tests/test_authorization_pin_integration.py`.
2. **Crash state E (row committed, anchor completion pending).** Amendment 4 section 6.2 steps 0 and 2 HOLD (`pin_transition_pending`) on any
   terminal intent that covers the decision, while the section 7 table says "accept; the terminal row refuses". The guard follows 6.2
   literally: it HOLDs. Both outcomes fail closed and neither executes an effect. The stricter reading is kept; the amendment text is not
   edited. The reader's `lookup` still follows the section 7 table (tested).
3. **Executor re-reads the anchor inside the transaction.** An executor parked after `BEGIN IMMEDIATE` and before the pin step HOLDs when a
   terminal intent appears; one parked after the pin step commits (it was admitted before the revocation became effective). Both are
   exercised in `test_terminal_cannot_commit_while_an_executor_holds_the_shared_fence...`.
4. **Installer `reconcile()` takes no assessment.** Completion of a recorded terminal intent after a crash is derived from the intent and the
   decision's active row; it is not re-validated against the Control Plane. This is weaker than Amendment 4 section 6.3 step 1 for the recovery
   path. It can only remove authority (a terminal transition), never add it. Re-validation happens at apply time only. Disclosed limit.
5. **Installer is stricter than the Runtime table** on an anchor that is behind the store with no intent (it refuses to append). Fail closed.
6. **Single installer process** is assumed (an in-process thread lock only). Concurrent installers are not supported.
7. **Profile rotation (Control Plane export defect B).** A rotation changes `catalogue_digest`, so the export raises for old decisions and never
   reports `superseded`. The old pin therefore stays `active`. It does not keep authority alive: the existing Registry selection guard refuses
   the old activation as stale (`test_stale_old_pin_is_fenced_by_the_registry_selection_guard_after_reactivation`). Residual: the pin store is
   not told, so the pin remains `active` until an operator or a future export marks it terminal.
8. **Withdrawn policy (export defect A).** The provisional withdrawn representation lacks `binding_digest` and changes the profile dump, so the
   export treats withdrawn as terminal only when the host binding body names the activation the decision committed to. This can only deny.
9. **Defective binding** on a pin-required destination yields the existing `field_invalid`, not a pin cause. Binding member comparison runs
   member by member before `binding_digest` (so the operation substitution reports `pin_binding_mismatch:operation`).
10. `OperationIdentityAtomicAuthorityEffectDestination.__init__` has no `authorization_pin` parameter; its execute path is covered by the base
    class wrapper, but the option is not constructible there. Not widened in this change.

## What is and is not demonstrated

- Same-identity, in-process tests prove format agreement, ordering and fail-closed behaviour of the two halves.
- Two-identity OS isolation is exercised by `tests/host_pin/test_installer_os_isolation.py` and requires root with `useradd`/`setpriv`; where it
  cannot run it SKIPS and the result is INCONCLUSIVE, never PASS.
- **External anchor custody cannot be proven by code.** A consistent restore of the store and the anchor together is undetectable. Anti-rollback
  is qualified only for a deployment whose anchor custody is demonstrated outside the pin store's restore domain (Amendment 4 section 7).
- CP-to-Runtime propagation delay before the installer starts, a stuck shared-fence holder keeping a transition pending (availability only), and
  supplier-seeded first-writer rows remain disclosed limits of Amendment 4 section 12.
- The Registry-dependent suites (guard, integration) need `OCS_REGISTRY_PIN_DIR`, PyNaCl, the Control Plane and pydantic >= 2.12. The component
  `native` CI job installs none of these, so they report NOT RUN there; they are run through the qualification repository.
