# Authorization pin: interface freeze (F-A1 implementation, Phase 0)

Normative source: Registry r08-v2 Amendment 4 as adopted at `1a4de32605519c505142b1ba3a57fa5b259b0b2c` (with its implementation proposal). This file freezes the **interfaces** the three workstreams implement against; it adds no contract. If a worker needs a change here, it stops and reports to the lead builder. Baselines: Registry `1a4de326`, Control Plane `1bee3696`, Runtime `ac247daf`. RELEASE / PUBLICATION / PRODUCTION: HOLD. Nothing here is "qualified".

## 1. Component ownership and integration order

| Workstream | Repo / branch | Exclusive file ownership |
|---|---|---|
| Lead (this freeze) | Runtime `f-a1-authorization-pin` | `engine/authorization_pin_format.py`, `tests/test_authorization_pin_format.py`, `tests/fixtures/authorization_pin_vectors.json`, this file. Workers do NOT edit these. |
| A: Control Plane export | `cognous-control-plane`, branch `f-a1-authorization-pin-export` from `1bee3696` | new `src/agent_control_plane/authorization_pin_export.py`, new `tests/test_authorization_pin_export.py`, and at most one line in `__init__.py` if exports are listed there. No edit to issuance code. |
| B: trusted host installer | Runtime, branch `f-a1-pin-installer` from the freeze commit | `host/` (new top-level package `host.authorization_pin`), `tests/host_pin/` |
| C: Runtime guard | Runtime, branch `f-a1-pin-guard` from the freeze commit | `engine/authorization_pin.py` (new), `engine/local_authority_effect.py`, `engine/registry_selection_authority.py`, `engine/atomic_local_control_plane_executor.py`, `tests/test_authorization_pin_guard.py` and `tests/test_authorization_pin_*.py` other than the format test |
| Lead integration | Runtime `f-a1-authorization-pin-impl` | merges B and C, cross-component tests `tests/test_authorization_pin_integration.py`, workflow path filters if needed |

Order: freeze -> A, B, C in parallel (isolated worktrees) -> lead integrates B then C -> lead cross-checks A's output against the format module -> two PRs (Control Plane, consolidated Runtime), both unmerged -> qualification.

## 2. Frozen formats

`engine/authorization_pin_format.py` is the single source: payload members (17), row, intent and anchor schemas, domain-separated SHA-256 digests over canonical JSON (`sort_keys`, separators `(",", ":")`, `ensure_ascii=False`), `chain_defect`, `decision_state`, `anchor_admission` (Amendment 4 section 7 table) and `fence_path`. Both B and C import it; neither re-implements any of it. Pure functions only; it opens no file and holds no lock.

## 3. Control Plane export (Worker A)

```python
# agent_control_plane/authorization_pin_export.py
PIN_EXPORT_PROFILE = "ocs-s4/authorization-pin-export/0.1"

def assess_authorization_pin(workflow, proposal, decision, *, now, host_policy_binding: dict) -> dict:
    """Read-only. Returns {"profile": PIN_EXPORT_PROFILE, "decision_id": str, "state": "active"|"terminal"|"unknown",
                           "payload": dict|None, "terminal_status": "withdrawn"|"revoked"|"superseded"|None, "reason": str}."""
```
- `workflow` is the trusted `BoundedAuthorizationWorkflow` of the installer process (holds `records`, `resolver`, `runtime_authority_profile`). `decision` MUST equal `workflow.records.decision(decision.decision_id)` (persisted, authorized); otherwise `PermissionError`. `host_policy_binding` is the canonical 14-member body from **installer-owned host configuration**, never from a claim. `expected_consumer_id` and `expected_operation` are taken from that body.
- It performs the same trusted-handoff validation sequence as `_materialize_under_handoff` (resolver handoff lock, `workflow._resolve(stage="claim")`, `_binding_issuance_reasons`, `snapshot_reader`, `_validate_issuance_snapshot`) by CALLING the existing helpers; it must not re-implement them and must not change them. It builds no claim and calls no `provision`.
- Additionally it recomputes `catalogue_digest = commitment(profile.model_dump(mode="json"))` and requires equality with the persisted `decision.binding.runtime_authority["catalogue_digest"]`; requires the persisted `approval_commitment` to equal `approval_commitment(profile, context)` recomputed under the handoff; requires `binding_digest(host_policy_binding) == profile.policy_binding.binding_digest`, matching `selection_epoch` and `activation_ref`, and `disposition == "selected"` for `active`.
- `state="active"`: `payload` is exactly the 17 members of `PAYLOAD_MEMBERS` (all derived from persisted decision / snapshot / host binding, never from a claim or caller kwarg); `not_before`/`expires_at` are the grant-validity intersection used for the claim interval. `state="terminal"` only when persisted authority positively shows the decision's authority is no longer supported, with `terminal_status` mapped from existing reasons into the closed set `{withdrawn, revoked, superseded}` (grant or approval revoked or terminal revocation recorded -> `revoked`; stale grant revision/superseded -> `superseded`; binding disposition withdrawn or activation mismatch -> `withdrawn`). Expiry is NOT a terminal transition (the Runtime derives it). Anything unreadable, unresolved, mismatched or ambiguous is `state="unknown"` (no pin change) or raises `PermissionError`; it never guesses a terminal status. `payload` is None unless `active`.
- Output contains no secret and no object aliasing caller memory (deep copies).

## 4. Installer (Worker B), package `host.authorization_pin`

```python
@dataclass(frozen=True)
class InstallerConfig:
    pin_store_path: Path; fence_dir: Path; anchor_path: Path; log_path: Path; pin_store_id: str
    exclusive_deadline_seconds: float = 5.0; clock: Callable[[], datetime] = utc now

class TrustedAuthorizationInstaller:
    def __init__(self, config: InstallerConfig): ...        # creates the store (journal_mode=DELETE) and meta, anchor genesis, fence dir
    def assert_custody(self) -> None                         # best-effort self-check: owned by euid, no group/world write on store, anchor, fence dir, ancestors it controls. NOT the isolation boundary.
    def apply_assessment(self, assessment: dict) -> InstallResult   # the ONLY entry that consumes Control Plane output
    def reconcile(self) -> list[InstallResult]              # mandatory start-up reconcile; also called inside apply_assessment before new work
    def pending(self) -> Optional[dict]                     # the anchor intent, if any
@dataclass(frozen=True) class InstallResult: outcome: str; decision_id: str|None; pin_epoch: int|None; detail: str
```
- `outcome` in `{"installed", "noop", "complete", "pending_blocked", "refused", "reconciled"}`.
- `apply_assessment` accepts only `state in {"active","terminal"}` with the frozen profile string; `active` -> install (write `install` intent in the anchor, create the fence file if absent, append the `active` row, complete the anchor); `terminal` -> Amendment 4 section 6.3 steps 2 to 9 exactly. Idempotent: an identical active payload is `noop`; a different payload for an existing decision, or any activation after a terminal row, is `refused` and logged.
- Terminal: write `terminal` intent first (durable, fsync file and directory), then acquire the exclusive per-decision fence by `flock(LOCK_EX|LOCK_NB)` retry with backoff until `exclusive_deadline_seconds`; on deadline return `pending_blocked`, leave the intent, commit nothing, advance nothing. There is NO code path that commits a terminal row without holding the exclusive fence. Under the fence: verify (committed == head, intent == recorded, chain intact, no terminal row yet), commit the terminal row (rollback-journal, `PRAGMA synchronous=FULL`), append the log, complete the anchor (committed = new head, intent cleared), mark `complete`, release.
- A pending terminal intent is never cleared without verified completion; there is no abandon operation; at most one intent exists, so a second operation while one is pending is `refused` after the pending one is reconciled or reported blocked.
- Evidence: append-only JSON lines at `log_path` (`recorded_at`, `action`, `decision_id`, `pin_epoch`, `pin_digest`, `intent_digest`, `outcome`, `anchor_before`, `anchor_after`). After a crash the log is reconstructed from the pin row, never the reverse.
- Anchor writes: atomic replace (temp file in the same directory, `fsync` file, `os.replace`, `fsync` directory). Reading uses `anchor_decode`. Absent anchor at construction = genesis (epoch 0) only when the pin store is empty and the anchor directory is empty; an absent or inconsistent anchor with a non-empty store raises (operator re-baseline, no automatic recovery, ADR 3.6).
- The installer never opens the effect database and holds at most one fence.

## 5. Runtime guard (Worker C), `engine/authorization_pin.py`

```python
@dataclass(frozen=True)
class AuthorizationPinConfig:
    pin_store_path: Path; fence_dir: Path; anchor_path: Path; pin_store_id: str; fence_deadline_seconds: float = 2.0

AtomicAuthorityEffectDestination(root, *, clock, database_name=..., authorization_pin: AuthorizationPinConfig | None = None)
```
- `authorization_pin` is a **constructor-only** host option (default None = pin not required = byte-identical behavior). There is no public setter or method that creates, changes or removes it. Reading uses SQLite URI `mode=ro` on the pin store; the destination holds no write handle to the pin store, anchor or fence files and implements no pin-write function.
- `PinReader`: `read_anchor()`, `read_head()`, `rows_from(epoch)`, `rows_for(decision_id)`; any `sqlite3.Error`/`OSError`/format error maps to HOLD `store_unknown` with the Amendment 4 cause. `lookup(decision_id)` applies: meta (profile, `pin_store_id` equal to the config and the anchor) -> `anchor_admission` -> `chain_defect` over `rows_from(max(committed,1))` -> `decision_state(rows_for(decision_id))`.
- Executor order inside `execute_claim_atomic`, exactly Amendment 4 section 6.2: step 0 lock-free precheck from the anchor; step 1 shared per-decision fence (`flock(LOCK_SH|LOCK_NB)` on a read-only descriptor of `fence_path`, retry until the deadline; absent file or timeout -> HOLD `verifier_unavailable` / `pin_fence_unavailable`); step 2 re-read the anchor under the fence; then `BEGIN IMMEDIATE`; pin step after `_validate_claim_state` and before `_selection_guard`; fence released in a `finally` after commit or rollback on every path. The fence/precheck key is `snapshot.decision_id`; inside the transaction `claim.decision_id == snapshot.decision_id` is already enforced by `_validate_claim_state`.
- Pin step (Amendment 4 section 5): claim without a binding -> REFUSE `policy_identity_mismatch` / `pin_required_binding_absent`; no pin -> REFUSE `pin_absent`; status not active -> REFUSE `pin_status:<status>`; outside `[not_before, expires_at)` at the lock-time clock -> REFUSE `pin_not_valid_now`; claim `effect_id, action_id, tenant_id, institution_id, authority_domain, grant_id, grant_revision` must equal the pin's -> `pin_lineage_mismatch:<member>`; `binding_digest(claim.policy_binding)` and each of `consumer_id, operation, scope.tenant, scope.institution, scope.authority_domain, selection_epoch, activation_ref` must equal the pin's -> `pin_binding_mismatch:<member>`. Digest: the Control Plane canonical digest function (`agent_control_plane.canonical_policy_binding.binding_digest`) pinned by shared-vector tests in this repo; if the import is unavailable it HOLDs.
- `check_effect` gains an optional keyword `pin_scope=None`; when given, the governed scope is built from the pin (`consumer_id, tenant, institution, authority_domain, operation` from the pin; `family` and `selection_id` from the digest-verified binding) and the unchanged selection checks, epoch/activation/digest/trust/revocation/generation checks and F-01 containment run unchanged. Absent -> byte-identical.
- Receipt: when pin-required, `receipt["authorization_pin"] = {"pin_epoch", "pin_digest", "anchor_committed_epoch", "payload_digest"}` is added inside the same transaction; otherwise the receipt bytes are unchanged.
- Typed refusals reuse `SelectionRefusal(code, refusal_class, cause=...)`: `policy_identity_mismatch` (REFUSE), `store_unknown` (HOLD), `verifier_unavailable` (HOLD). No new reason code.
- `provision_claim` is unchanged in behavior for authority tables and gains no pin write; a test must show it cannot create or modify any pin artifact.

## 6. Test conventions

- Runtime: `PYTHONPATH=".:src" python -m pytest -q tests` using `/home/claude/venv-cp/bin/python` (has pytest, pydantic and the Control Plane importable). Real OS-identity tests are marked `os_isolation`, require root (`os.geteuid()==0`) and `useradd`/`setpriv`, and SKIP otherwise (a SKIP is INCONCLUSIVE, never PASS). Throwaway users are created and deleted by the test itself.
- Every behavior change gets a test. No test may weaken, delete or edit an existing test. Existing suites must stay green.
- CLAUDE.md of the Runtime applies: minimal auditable changes, no implicit/default/wildcard permissions, preserve governance wording (non-authorizing, HOLD, no production); do not claim certification or production readiness.

## 7. What no workstream may do

Invent a trust root, add HMAC/Ed25519, add a second authorization engine or policy evaluator, widen a claim commitment, add a claim member, change the Registry selection machinery's decisions, add a degraded or unfenced path, hold a network call inside a transaction, merge a PR, or touch Registry frozen files.
