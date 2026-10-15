# OpenAPPA pinned codec fixture gate

> Historical workstream note. See the [current OpenAPPA status](https://github.com/cogno-us/cognous-stack-qualification/blob/43fb81dc3ec0da17402bef274dcec1831cc5e160/suites/cognous-execution-runtime/tools/openappa/README.md)
> for consolidated evidence, current scope and remaining work.

Scope: synthetic protocol-1 fixture integrity and actual upstream codec parsing.
This is not an OpenAPPA policy/runtime adapter and cannot dispatch or authorize.

Inputs: hub profile PR #44 (merged 80022672e1fc773aad4ae1a491f9e7e3c1f910a4),
Manifest declaration PR #10 (merged 75694fa74b57d9f9f4ad9572c14c47a347f02651),
Runtime baseline 31beca6e90dfd775bb177115ebec481ede3abd95, upstream OpenAPPA
10aa0e09e32d8c6704bdb030dc3c66140bf81915. Selected hub lock is unchanged.

## Fixture boundaries

`tests/fixtures/openappa/protocol1.json` contains exact request bytes plus their
SHA-256, four distinct synthetic response examples, and separate host context.
The host context is a test oracle; it is not a trusted authentication source.
Changing both the fixture and oracle is outside these fixture-integrity checks.
It must never be reused as a runtime authorization validator.

The six Python tests reuse actual native OperationDiscriminator/OperationAttempt
constructors and reject single-sided identity substitutions (ten subcases),
request changes, missing tenant context, source changes and authority claims.
No sidecar field is injected into the upstream wire envelope. The request uses
an existing kagent wire host and a synthetic refund tool mapping only for codec
validation. Upstream has no served `cognous` host identifier; deciding between
an embedded integration and an upstream-supported host remains a separate gate.

The Rust integration harness is copied into a temporary exact-pinned upstream
checkout. It calls actual WireEvent and WireDecision parsers, checking call/root
identity, canonical tool mapping, argument values, distinct allow/deny/control/
block outcomes, wrong protocol, missing root and malformed deny response.
No upstream runtime, policy engine, destination or institutional resolver runs.

## Validation and next gate

Local Python 3.12: six tests passed, including ten identity subcases. Local Rust
compiler/Cargo was unavailable; Rust test execution is delegated to the new
focused CI and must be inspected before codec conformance is claimed. Synthetic
responses are test inputs, never evidence of a real APPA allowance or denial.

Next: choose the host integration, then bind authenticated tenant/principal,
operation/attempt/effect and request commitments to a real decision exchange.
Two-sided policy/authority denial and actual destination-state assertions are
not implemented here. Preserve C0 check-to-commit limits, optional same-host C1,
no C2/C3, no real settlement and orchestrator #30 HOLD. No credentials, grants,
production effects, automatic retries or release-selection changes.
