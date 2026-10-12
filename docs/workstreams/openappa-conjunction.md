# Synthetic OpenAPPA and authority conjunction candidate

Runtime base: `383fbd972220992ce60e1662053720f2076485f3` (embedded gate #49).
Upstream OpenAPPA: `10aa0e09e32d8c6704bdb030dc3c66140bf81915`.
Control Plane: `2ea9528eeb87e14ff10f05de06473122b9df540f`.
Action Manifest fixture: `46c950bed37fe3812000895430bc0312d29e37ce`.
The latter pins reuse the accepted Python safety workflow, not current component
main. No selected hub lock is advanced.

## Executable scope

The test runner launches a compiled actual OpenAPPA embedded runtime through
private stdin/stdout pipes. The host selects a fixed synthetic allow or deny
policy; neither the model nor the request selects policy. A SHA-256 commitment
covers exact request bytes, including the host context, native operation fields,
decision, effect, operation and attempt identifiers. The OpenAPPA tool receives
the canonical operation as arguments. The response carries only the commitment
and flow outcome; a digest is correlation, not an identity signature.

Only a correlated allowance reaches the existing `PinnedControlPlaneExecutor`.
It executes the real pinned Control Plane workflow and its current authority
revalidation. The actual SQLite `DurableRefundDestination` records the synthetic
effect. No boolean substitute for institutional permission is used. The helper
reuses the existing native integration fixture and snapshots the request before
invoking the flow process. Exceptions, unknown outcomes and mismatched responses
do not reach the executor.

Twenty proposed cases check all four allow/deny and valid/revoked combinations;
wrong digest, unknown outcome, malformed response and unavailable process;
revocation during the flow call; real allowances for a different target or tenant/operation/attempt/root/call;
and host/native principal/domain/institution/attempt/effect mismatches.
Every case asserts actual destination state and effect count. CI requires twenty
executed cases and no skips. At publication these integration cases are pending
hosted compilation and execution; source inspection is not a passing result.

## Trust assumptions and exclusions

This is test-only code under `tools/openappa`, with no default runtime hook.
The trusted synthetic test runner owns the executable path, process, pipes,
fixture policy, tenant label, operation identity and authority fixture resolver.
Principal, institution and domain are matched to the native operation. There is
no external user/session authentication, process attestation or production tenant
directory; these fixture identities do not qualify authenticated deployment.

Each probe invocation opens a fresh in-memory trajectory. This deliberately
cannot qualify protected multi-call sessions, accumulated restrictions, durable
correlation, crash recovery or replay prevention. It must not be installed as a
production adapter. Cross-process response signatures and malicious same-user
processes are outside the fixture trust boundary. The policy examples establish
conjunction behavior, not a comprehensive refund information-flow policy.

Output admission, expiry/policy-generation handling and complete sink coverage
remain unimplemented. Next work must replace fixture host identity with an
accepted authenticated host contract and retain one durable policy trajectory
across calls before proposing an opt-in runtime adapter.

Preserve C0 check-to-commit limitations, optional same-host C1, no C2/C3, no real
settlement, no credentials/grants or production effects, and operational
orchestrator #30 HOLD. A passing synthetic conjunction is not release acceptance.
