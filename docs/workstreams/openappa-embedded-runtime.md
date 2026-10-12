# OpenAPPA embedded runtime qualification candidate

Base: Runtime `9d9804131b0029801925ce1a5ca9e7f696a81f76`, after codec PR #48.
Upstream remains `10aa0e09e32d8c6704bdb030dc3c66140bf81915`.

The next candidate uses the actual upstream embedded API: `Config::hosted`,
`Runtime::open_with_store_as`, `AdapterName::Embedded` and `hooks::handle`.
These interfaces were inspected at the exact pin. Unlike the codec fixture,
this does not label Cognous as a served kagent host. The custom adapter exists
only in the test harness; this is not a deployed Python/Rust bridge.

Four tests exercise an unrestricted allowance, a restricted denial, an unknown
tool refusal, and distinct in-memory policy stores using identical synthetic
root/call identifiers. Responses come from the real policy runtime. No remedy
is executed. The paired allow/deny assertions must detect an always-allow or
always-deny substitution. CI compiles the pinned runtime without daemon defaults
and with its unmodified lockfile; no upstream dependency or source is patched.

Local Rust/Cargo is unavailable. Compilation and all four runtime assertions
remain unqualified until the focused hosted job completes and its logs are
inspected. Existing codec tests do not establish these runtime results.

## Trust boundary and next work

An embedded candidate avoids inventing a served host identifier, but does not
solve host authentication. Each test supplies synthetic identity and creates
its own store. Independent-store behavior is not production tenant isolation.
No credentials, external annotators, destination writes or Cognous authority
resolver are used. A flow allowance never represents institutional permission.

The next integration must obtain tenant/principal from a trusted host boundary,
bind operation/attempt/effect and exact request commitments to that context,
call the real institutional resolver, and assert that either independent denial
prevents a synthetic destination write. A boolean stand-in for that resolver
or the previous fixture oracle cannot qualify this join. Durable correlation,
restart, expiry, output admission and the Python/Rust transport remain open.

No default activation, selected lock advancement or production effects. Preserve
C0 check-to-commit limitations, optional same-host C1, no C2/C3, no real settlement
and operational orchestrator #30 HOLD.

Initial hosted job `114927611496` stopped before compilation because the command
used the directory name `appa-runtime` as the Cargo package. The pinned manifest
names the package `appa` and its library `appa_runtime`. The command is corrected
to `-p appa`; no runtime result is inferred from that failed setup run.
