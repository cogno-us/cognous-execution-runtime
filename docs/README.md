# Execution Runtime documentation index

Every page in this repository is listed here once, grouped by what a reader needs. Labels: **current** (describes the code on `main`), **profile** (an optional capability, described as merged or proposed on its own page), **historical** (a dated record kept as written; it can describe revisions and code that are no longer on `main`), **legacy** (compatibility material for the retained `AgentEngine` shell). Back to the [repository README](../README.md). Stack-level scope and limits are stated once in the hub [guarantees and limits](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/guarantees-and-limits.md) page; names are defined in the hub [naming table](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/naming.md).

## Start here

| Page | Label | What it is |
| --- | --- | --- |
| [Supported constrained execution boundary](execution-boundary.md) | current | The reference page: supported path, envelope 0.2.0, attempts and recovery, route names (A, B, C), consistency boundary, isolation limits and the known limits list. |
| [Architecture](architecture.md) | current | One-page picture of the supported path and where each module sits. |
| [Threat model](threat-model.md) | current | Assets, assumptions and limits for the supported synthetic refund path. |
| [Executor producer profile 2.0.0](executor-producer-profile.md) | current | What `engine.producer_contract` exports for Replay Bundle and the exchange reference, with the observation and status rules. |
| [ADR 0001: supported Python execution boundary](adr/0001-supported-python-execution-boundary.md) | current | Why the Python path under `engine/` is the only supported boundary. |

## Integration with the Control Plane

| Page | Label | What it is |
| --- | --- | --- |
| [Control Plane interface gap](control-plane-interface-gap.md) | proposed | The requested Control Plane extension for exporting institution and domain in the authorization binding, and the current workaround. |
| [Durable effect-time refusal capture](control-plane-refusal-capture.md) | current | How the adapter retains a refusal only after the Control Plane append returns. Its test counts are historical. |
| [Contract conformance: execution-attempt lifecycle](contract-conformance-execution-attempt-lifecycle.md) | current | How the producer meets the vendored lifecycle contract (`0.1.0-draft`). The vendored contract copy is under [tests/contracts/execution-attempt-lifecycle](../tests/contracts/execution-attempt-lifecycle/README.md) and is not edited here. |
| [W0 contract addendum: C3, C5, C7](w0-c3-c5-c7-contracts.md) | historical | Candidate contract definitions for capability profile, observation/recovery and stop. |

## Optional profiles (not part of the default path)

| Page | Label | What it is |
| --- | --- | --- |
| [Atomic local authority/effect profile](local-authority-effect-profile.md) | profile, merged | One SQLite file holds claim, grant, budget and effect; route B. |
| [Local effective grant precondition](execution-boundary.md#optional-local-effective-grant-precondition) | profile, merged | Grant check inside the destination commit transaction; route A. |
| [OpenShell adapter 0.1.0](openshell-adapter.md) | profile | Dedicated local Docker sandbox destination. Live enforcement is a known limit. |
| [Operation admission](o6-p1a-operation-admission.md) | profile | Stable operation identity and adapter conformance checks. |
| [Revocation closeout](o6-p1d-revocation-closeout.md) | profile | Durable revocation disposition and closeout of the original attempt. |
| [Recovery resolution (synthetic)](recovery-resolution-synthetic.md) | profile, experimental | Append-only evidence ledger for unresolved attempts; no execution or retry authority. Its [changelog](../RECOVERY_RESOLUTION_CHANGELOG.md) is separate. |
| [Refund-intent registry checkpoint](workstreams/refund-intent-registry-checkpoint.md) | profile, historical | Record of the refund-intent ownership work (PR 14). |

Operation identity, dedupe-ttl and delayed-commit destinations have no page of their own: they are described in the module docstrings under `engine/` and in the [changelog](../CHANGELOG.md).

## Qualification and checkpoint records

| Page | Label | What it is |
| --- | --- | --- |
| [Cross component test ownership](../CROSS_COMPONENT_TESTS.md) | current | Where the relocated suites and workflows live and how the overlay is assembled. |
| [Executor observation checkpoint](workstreams/executor-observation-checkpoint.md) | historical | Batch 4C-A observation compatibility record. |
| [Local authority/effect checkpoint](workstreams/local-authority-effect-checkpoint.md) | historical | Worker 21 implementation record. |
| [W2 local stop and recovery qualification](workstreams/w2-stop-recovery-qualification.md) | historical | Scope of the local stop and reopen qualification; it does not define fleet cancellation. |
| [Live OpenShell qualification checkpoint](workstreams/live-openshell-qualification-checkpoint.md) | historical | Readiness package and the blocked live prerequisites. |
| [OpenShell image qualification checkpoint](workstreams/openshell-image-qualification-checkpoint.md) | historical | Worker image qualification record. |
| [External installer smoke closeout](workstreams/external-installer-smoke-checkpoint.md) | historical | Installer smoke run that hit the time limit. |

## Quarantined upstream application

The upstream TypeScript/native application is preserved at the tag `upstream-moltbot-quarantine-20261016` and is not on `main`. These pages describe it or its handling and are kept as records.

| Page | Label | What it is |
| --- | --- | --- |
| [Quarantine record](quarantine-upstream-moltbot-20261016.md) | current | The scope decision, the tag and what is and is not claimed. |
| [Security remediation proposal, 2026-10-16](security-cloud-remediation-20261016.md) | historical | Superseded in part by the quarantine; only the `engine/` admission items apply. |
| [OpenAPPA host and output-admission contract proposal](workstreams/openappa-host-contract.md) | historical | Design proposal against the upstream application; source links point at the tag. |
| [OpenAPPA reply admission](workstreams/openappa-reply-admission.md) | historical | Optional final-text dispatcher admission candidate. |
| [OpenAPPA conjunction](workstreams/openappa-conjunction.md) | historical | Synthetic conjunction candidate. |
| [OpenAPPA embedded runtime](workstreams/openappa-embedded-runtime.md) | historical | Embedded runtime qualification candidate. |
| [OpenAPPA feedback](workstreams/openappa-feedback.md) | historical | Synthetic execution outcome feedback. |
| [OpenAPPA feedback journal](workstreams/openappa-feedback-journal.md) | historical | Durable local feedback status. |
| [OpenAPPA local conjunction](workstreams/openappa-local-conjunction.md) | historical | Local host binding through persistent flow and authority. |
| [OpenAPPA local host](workstreams/openappa-local-host.md) | historical | Opt-in Linux local-host resume candidate. |
| [OpenAPPA persistent conjunction](workstreams/openappa-persistent-conjunction.md) | historical | Persistent flow and authority qualification. |
| [OpenAPPA resume boundary](workstreams/openappa-resume-boundary.md) | historical | Existing-trajectory lookup boundary. |
| [OpenAPPA trajectory](workstreams/openappa-trajectory.md) | historical | Persistent trajectory qualification candidate. |
| [OpenAPPA wire fixtures](workstreams/openappa-wire-fixtures.md) | historical | Pinned codec fixture gate. |
| [dompurify dependency restart](workstreams/dependency-restart-dompurify.md) | historical | Dependency restart note for the upstream UI. |
| [Focused dompurify restart](workstreams/focused-dompurify-restart.md) | historical | Dependency restart note. |
| [sharp dependency restart](workstreams/dependency-restart-sharp.md) | historical | Dependency restart note. |
| [Focused sharp restart](workstreams/focused-sharp-restart.md) | historical | Dependency restart note. |
| [music-metadata lock update](workstreams/dependency-restart-music-metadata.md) | historical | Dependency lock note. |
| [Vitest restart](workstreams/dependency-restart-vitest.md) | historical | Dependency guard note. |

## Legacy compatibility shell

`engine.AgentEngine` validates, permission-checks and logs a request and never executes it. These pages describe it.

| Page | Label | What it is |
| --- | --- | --- |
| [API reference](api.md) | legacy | `AgentEngine` interface. |
| [Internal module guide](internals.md) | legacy | Responsibilities of the compatibility modules. |
| [Sandbox directory note](../sandbox/README.md) | legacy | Placeholder directory kept for the legacy shell. |
| [Design philosophy](design-philosophy.md) | legacy | Early statement of design principles for the compatibility shell. |
| [Roadmap](roadmap.md) | legacy, proposed | Early plan for the shell. It lists sandbox, network and subprocess extensions that are proposals only; none of them is implemented or promised. |

## Repository policy files

| Page | What it is |
| --- | --- |
| [Contributing](../CONTRIBUTING.md) | Contribution expectations. |
| [Security policy](../SECURITY.md) | Scope of the policy (the `engine/` boundary only) and how to report. |
| [Code of conduct](../CODE_OF_CONDUCT.md) | Community expectations. |
| [Changelog](../CHANGELOG.md) | Dated change list; the upstream-era entries are historical. |
| [Repository guidelines for contributors and agents](../AGENTS.md) | Working instructions. [CLAUDE.md](../CLAUDE.md) is a symlink to this file. |
| [Business overview](../collateral/business-collateral.md) and [one-page overview](../collateral/one-page-overview.md) | Non-technical introductions. They describe this component's role and limits, not extra features. |
