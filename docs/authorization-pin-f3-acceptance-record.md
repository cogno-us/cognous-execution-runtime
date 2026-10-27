# Authorization pin: F3 requalification and refreeze record

Status: F3 REQUALIFICATION AND REFREEZE RECORDED. F3 RE-REVIEW: PENDING (Architect; not waived, not self-claimed). Non-authorizing record. Release: HOLD. Publication: HOLD. Production: HOLD. This record claims no security clearance, certification or production readiness.

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
