# Security Cloud runtime remediation proposal — 2026-10-16

Superseded in part by the quarantine of 2026-10-16 (see quarantine-upstream-moltbot-20261016.md): the TypeScript and native items below describe a proposal for code that is not on `main`; only the `engine/` admission items apply to this repository.

Scan: https://chatgpt.com/codex/cloud/security/runs/wfr_f7488f2ada916df23f35315d28b004ba541f6b01e2d0bb9afc4cfd1d79c8a8e3
Scanned/base revision: `da89cb59c6f2848c88c522fd76bcccb2c744e4f2`.

This proposal covers admission, authentication, plugin execution, attachment handling, browser control, native Canvas entry points and audit evidence. It does not change the selected hub component pin or establish production authorization.

Browser HTTP/extension relay now requires `CLAWDBOT_BROWSER_TOKEN` (at least 32 characters). An operator must provision it through their secret handling procedure and enter the same value in extension options. The agent created no credential. Local in-process browser dispatch remains available. Never place the token in URLs, logs or committed files.

Android Canvas is display-only: the origin-unaware JavaScript interfaces were removed. iOS Canvas web navigation cannot launch agent deep links; normal external application intake is unchanged. macOS A2UI actions require the local custom scheme and main frame. Native toolchains/device validation were unavailable in this Linux environment; these changes require platform CI/review before merge.

Host commands must be explicitly configured, and deny applies before every backend. A missing sandbox cannot fall through to host execution. Sandboxed messaging permits bounded inline payloads but cannot read host paths or trigger host-mediated remote attachment fetches. Remote media fetches validate DNS and every redirect, with pinned connections, 30-second deadline and a separate 20 MiB download limit before upload-size processing.

Execution input is admitted before copying; non-builtin objects, string subclasses, excessive depth/nodes/bytes and nonfinite numbers are refused. Malformed admission stores a payload-free rejection record. Early policy and authority denials produce durable records. OpenShell subprocess output has a combined 1 MiB streaming bound and process-group timeout cleanup; JSON syntax depth is checked before parsing.

## Finding coverage

- `csf_15b0f7ef1abf5c79d6f56f3e` (high): Android exposes an agent-request JavaScript interface to every Canvas origin. Proposed correction; not closed pending review and final evidence reconciliation.
- `csf_64fedb06b2642025d1c5b1f9` (high): Browser control HTTP service is unauthenticated and trusts loopback as authority. Proposed correction; not closed pending review and final evidence reconciliation.
- `csf_6282e4d25e8c32fbbe578327` (high): Compatibility HTTP APIs allow unpaired Tailscale peers to select agents and sessions. Proposed correction; not closed pending review and final evidence reconciliation.
- `csf_915281ffc267dee9c153be8b` (high): Any page loaded in the iOS Canvas can launch an unprompted agent request. Proposed correction; not closed pending review and final evidence reconciliation.
- `csf_c29625e5c5c2497a5f028e71` (high): HTTP tool invocation grants privileged tools to unpaired Tailscale identities. Proposed correction; not closed pending review and final evidence reconciliation.
- `csf_20a15fe7a90652c499d7d213` (high): Workspace-local plugins are implicitly enabled and executed in the gateway host. Proposed correction; not closed pending review and final evidence reconciliation.
- `csf_86be93d706aeb4cb1e0d0b54` (high): Message attachments bypass sandbox network policy with SSRF and unbounded fetching. Proposed correction; not closed pending review and final evidence reconciliation.
- `csf_30207e2ef65f0b500c1f7e4e` (high): Chrome extension CDP relay accepts unauthenticated loopback WebSockets. Proposed correction; not closed pending review and final evidence reconciliation.
- `csf_64d85f36fffe8db43a501bad` (high): Workspace identity metadata can escape the Control UI bootstrap script. Proposed correction; not closed pending review and final evidence reconciliation.
- `csf_c7468a217b627ccc46811359` (high): Host exec falls through the default deny policy when sandboxing is off. Proposed correction; not closed pending review and final evidence reconciliation.
- `csf_c8743b0cc2f8091213d2972c` (high): Sandboxed agents can exfiltrate arbitrary host files through message attachments. Proposed correction; not closed pending review and final evidence reconciliation.
- `csf_d696d92aca41989ef9306344` (high): Tailscale WebSocket clients can bypass device pairing with any nonempty token field. Proposed correction; not closed pending review and final evidence reconciliation.
- `csf_dd5db1c79b75464a8fd66868` (medium): OpenShell buffers and parses unbounded subprocess output. Proposed correction; not closed pending review and final evidence reconciliation.
- `csf_9eef851bd992aca756cf7a46` (medium): Execution envelopes are copied and serialized without size or depth bounds. Proposed correction; not closed pending review and final evidence reconciliation.
- `csf_54e2da0a06f661b64495dc90` (medium): String subclasses can bypass the local target-prefix authorization check. Proposed correction; not closed pending review and final evidence reconciliation.
- `csf_31d8050510db7e409ef5b506` (medium): macOS trusts broad local-network hostnames as native A2UI origins. Proposed correction; not closed pending review and final evidence reconciliation.
- `csf_61d39b78ddc9fb9c2b9f0f17` (medium): Supported execution paths omit durable audit records for malformed requests and early denials. Proposed correction; not closed pending review and final evidence reconciliation.

## Validation and limitations

Pinned pnpm 10.23.0 frozen installation succeeded. Lint, TypeScript checking and build passed during implementation; final-head checks are recorded in the PR. Python and targeted JavaScript regressions include executable denial, private redirect rejection, relay authentication, malicious input admission and subprocess bounds.

The initial broad JavaScript run failed (110 tests plus one suite error). Repeating its 32 failed files on the untouched scanned revision found 39 failures plus one suite error involving unavailable network interfaces/DNS and existing test/environment behavior. Newly affected authentication, process and network fixtures were corrected and tested separately. Do not represent this as a full passing platform suite. No live or paid model tests ran.

No release/tag, credential issuance, production probing, announcement, repository-setting change or publication authorization occurred. Operational HOLD / NOT ESTABLISHED remains. Exact selected-source and cross-component qualification are separate pending gates.
