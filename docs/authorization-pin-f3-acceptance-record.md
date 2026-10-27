# Authorization pin: F3 requalification and refreeze record

Status as written at `3873a61b` (SUPERSEDED for the re-review item by the appended section below; original text kept): F3 REQUALIFICATION AND REFREEZE RECORDED. F3 RE-REVIEW: PENDING (Architect; not waived, not self-claimed). Non-authorizing record. Release: HOLD. Publication: HOLD. Production: HOLD. This record claims no security clearance, certification or production readiness.

Runtime CLAUDE.md requires F3 requalification, refreeze and re-review before acceptance of changes to this candidate component. This record covers the first two and states the third as open.

## Frozen candidate

- Base: `main` `ac247dafbce3aacde37695575d457cf958b22560` (F-01 five-set containment).
- Implementation head qualified: `ad1805e22b1dee4f3201a228f85e1c4599bbebe5` (tree `3c44391138f662d902c65431464af1078dd8edc8`).
- The commit that adds this file changes exactly one path (this file). All code, tests, fixtures and workflows are byte-identical to `ad1805e2`; the digests below are therefore the digests of the qualified code.
- Registry contract: Amendment 4 at `1a4de32605519c505142b1ba3a57fa5b259b0b2c`. Control Plane export: merged as `d66ae07fa240510efdcc11ed4b3e1189dcefc822` (merge commit; its tree equals that of PR head `0fd44d03d69aa49f8022472aedd0f8187a8e8ea9`, which is an ancestor). The Runtime does not import the Control Plane; the format is frozen in `docs/authorization-pin-interface-freeze.md`.
- Merge method for this PR must preserve commit ancestry (merge commit, not squash), because the qualification pins reference these commits.

Frozen file digests (sha256, as of `ad1805e2`):

    ca1fdc1ecc7ab1631422ceae2e1b93b0b3ff1433920d5d51fe835ed77d6e3196  engine/authorization_pin.py
    2fc671e6ee9eacaecd7d1809538e31e8f78a25ab6519c2605f2d955197628bb2  engine/authorization_pin_format.py
    afbb28423d28146956c2c71ea75c3086807018d1f282426c97c6c72f8912d1dc  engine/local_authority_effect.py
    eddc41a61be641c88d412545f3f92158e7c586a5a2107364a1c68eb22002a67b  engine/registry_selection_authority.py
    5f6147c84777f9f4029d86b852d201c26cb9e1443648e35f5e1a3bfc043b421e  host/authorization_pin/installer.py
    0135f85d97751cdf96bcf825bbbc416695bdaf8b6a4e5e9ec6afb7b5489035fd  host/authorization_pin/__init__.py
    ac67e0c0d3d6c079021ab81074c692d4f5273f419a10f0a84cea8d6a60d5f9ff  tests/fixtures/authorization_pin_vectors.json

## Requalification evidence (reused, not re-run as a campaign)

| Requirement | Evidence | Result |
|---|---|---|
| F-A1 operation substitution refused | FA1-01, FA1-03, FA1-04 (historical breach reproduced on an unpinned destination; refused with `pin_binding_mismatch:operation`, 0 effects, with the pin; recomputed commitments do not bypass) | HELD |
| F-01 five-set containment beneath a valid pin | FA1-23, FA1-24 (`policy_not_subset:allowed_actions` / `allowed_units`), FA1-25; the Runtime F-01 suites in this PR's CI | HELD |
| Strict revocation ordering | FA1-10, FA1-11, FA1-14; integration tests in `tests/test_authorization_pin_integration.py` | HELD |
| Revocation propagation before the installer's intent | FA1-12 | CHARACTERIZED (documented limit) |
| Pending intent and recovery | FA1-13, FA1-19 to FA1-22 (real SIGKILL), mandatory reconcile | HELD |
| OS isolation (two real non-root identities) | FA1-05, FA1-06a; `tests/host_pin/test_installer_os_isolation.py` (runs only as root; skipped otherwise, which is INCONCLUSIVE, never PASS) | HELD where executed |
| Directly affected tests, this record | `tests/test_authorization_pin_format.py`, `tests/test_authorization_pin_guard.py`, `tests/test_authorization_pin_integration.py`, `tests/host_pin` run as root with Registry pin: 133 passed, 0 skipped | PASS |

Qualification run: cognous-stack-qualification run 40537952077 (all 30 F-A1 cases, gate green) at Runtime `ad1805e2`. The original local record (28 HELD, 1 CHARACTERIZED, 1 INCONCLUSIVE) is preserved there and is not rewritten. The qualification pins are advanced to this record's head in the successor qualification PR, whose CI is the revalidation of the final heads.

## Disclosed limits (unchanged, see the integration record)

Same-organization evidence on ephemeral hosts; anchor custody on an independent medium and a production-grade OS boundary are not demonstrated; a consistent restore of both store and anchor is undetected (analysis only); revocation propagation delay (FA1-12). Independent external Tier 1: NOT STARTED.

## Open

F3 re-review by the Architect. No requirement above was waived.

---

## Architect acceptance record (appended 2026-10-27; documentation only)

Appended text. Nothing above was rewritten except the one status line, which now points here. No Runtime code, fixture, test, workflow or security contract is changed by this addition.

### Exact revisions
| Item | Revision |
|---|---|
| Base | `main` `ac247dafbce3aacde37695575d457cf958b22560` |
| Implementation head qualified | `ad1805e22b1dee4f3201a228f85e1c4599bbebe5` (tree `3c44391138f662d902c65431464af1078dd8edc8`) |
| PR #149 head, tested by the F-A1 qualification (docs-only F3 record added) | `3873a61b5dd1ca2f8102d355888e59b5cd5a5456` (tree `e0d7003e279cddad50062cfb111d498cb4e714de`) |
| PR #149 merge commit (merge, not squash; second parent is the PR head) | `b5605f525ae73cbb7adcfef4716c67cdd33a5f7d` (tree `e0d7003e279cddad50062cfb111d498cb4e714de`, equal to the PR head) |
| Qualification | cognous-stack-qualification #237, merge `bb9b08904192b5f50a49e00ed8de0cab80fd7c19`, F-A1 job run 40555193111 at pins Registry `1a4de326`, Control Plane `d66ae07f`, Runtime `3873a61b` |

The qualification ran at `3873a61b`, not at the merge SHA `b5605f52`; the two have identical trees, so the evidence applies to the merged content. It was not produced at the merge SHA.

### What each F3 element is now
| F3 element | State | Basis |
|---|---|---|
| Requalification | **Completed** (same-organization) | The 30 F-A1 cases and directly affected tests listed above; local record 28 HELD, 1 CHARACTERIZED, 1 INCONCLUSIVE (FA1-13, corrected case passed in CI); CI gate green. |
| Refreeze | **Completed** | Digests and revisions recorded above in this file at `3873a61b`. |
| Architect re-review | **Accepted for the bounded candidate** | Recorded on the Architect's statement in the 2026-10-27 post-merge assignment that the F3 re-review was completed and PR #149 accepted. No separate review artifact was located in the PR #149 comments or reviews when this was written; this entry records the Architect's decision and does not create it. |
| Independent external security qualification | **Not completed** | Independent V07 security review: OPEN. Independent V08 Tier 1: NOT STARTED. Ed25519 #26/#34: SECURITY-HELD. |
| Production clearance | **Not granted** | Release, publication and production: HOLD. The destination remains synthetic SQLite only. |

"Accepted for the bounded candidate" means the Architect accepted the merge of #149 as non-qualified engineering work for the synthetic vertical slice. It is not a security clearance, certification or production-readiness claim, and it does not replace independent qualification.

### Disclosed limits (unchanged)
Same-organization evidence on ephemeral hosts; anchor custody on an independent medium and a production-grade OS boundary not demonstrated; consistent restore of both store and anchor undetected (analysis only); revocation propagation delay (FA1-12). See `docs/authorization-pin-integration-record.md` and the V09 register in the Registry repository (`docs/v09/limitations-and-exclusions.md`).
