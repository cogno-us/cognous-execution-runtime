<!-- cognous-banner:start -->

Cross-component test commands below now run in a disposable checkout assembled by the [qualification workflows](CROSS_COMPONENT_TESTS.md). Plain local test discovery covers the component-local tests retained here. Historical reports and dependency pins keep their original scope.
```text
──────────────────────────────────────────────────
   __________  _______   ______  __  _______
  / ____/ __ \/ ____/ | / / __ \/ / / / ___/
 / /   / / / / / __/  |/ / / / / / / /\__ \
/ /___/ /_/ / /_/ / /|  / /_/ / /_/ /___/ /
\____/\____/\____/_/ |_/\____/\____//____/
            COGNOUS EXECUTION RUNTIME
       g o v e r n e d   b y   d e s i g n
  github.com/cogno-us/cognous-open-control-stack
──────────────────────────────────────────────────
```
<!-- cognous-banner:end -->

# Cognous Execution Runtime

Cognous Execution Runtime provides the **single-agent constrained execution boundary** in the Cognous Open Control Stack. Its retained Python package and producer identities continue to use Moltbot Safe names for compatibility.

**Stack group: 04 · Constrained Execution.** See the [six-group component map](#cognous-stack-components).

## Supported runtime

The supported safety-layer runtime is the Python package under `engine/`.

**Upstream application quarantined.** The upstream Moltbot TypeScript/native application formerly retained in this repository is **quarantined**. It is preserved only at the tag [`upstream-moltbot-quarantine-20261016`](https://github.com/cogno-us/cognous-execution-runtime/tree/upstream-moltbot-quarantine-20261016) (commit `fd6b76fa0e85feb5b67324114bf790d3a12fdaf1`) and is **not shipped on `main`**. It is not maintained, not reviewed and not part of this execution boundary, and it carries 18 known unrepaired security findings (6 high). Its earlier commits remain in public Git history. See the [quarantine record](docs/quarantine-upstream-moltbot-20261016.md).

The legacy `engine.AgentEngine` is compatibility-only. It validates, permission-checks and logs, but performs no effect and therefore cannot report successful execution.

The supported path is:

```text
Pinned Control Plane BoundedAuthorizationWorkflow.execute()
        -> current grant / approval / policy / evidence revalidation
        -> Moltbot Safe ControlPlaneRefundDestinationAdapter
        -> strict local execution policy
        -> durable synthetic SQLite refund
        -> destination observation / reconciliation
```

A persisted historical `authorized` decision is not an execution credential. An allow decision is not an executed effect. An execution acknowledgement is not independently verified delivery.

## Which Runtime revision is supported?

Use the [current hub component lock](https://github.com/cogno-us/cognous-open-control-stack/blob/main/component-lock.json) to identify the selected revision. Optional profiles in this repository are not automatically selected or enabled by the hub.

**Worked recovery example:** a synthetic refund effect can commit while its acknowledgement is lost. Preserve the original effect and attempt identity, record the outcome as unknown and reconcile that attempt. Unknown or absent observations do not authorize a replacement. A supplied observation does not by itself prove source authenticity or external settlement.

This Python layer does not cover the quarantined upstream application, real payment processors or independently authenticated deployment. Operational trust remains [HOLD](https://github.com/cogno-us/cognous-stack-orchestrator/issues/30).

## Integration and producer contract

The [current component lock](https://github.com/cogno-us/cognous-open-control-stack/blob/main/component-lock.json) defines the selected integration. The public `engine.producer_contract` exports executor producer profile **2.0.0** and Execution Envelope **0.2.0**, retaining results, effects, attempts, events and observations. Exports do not grant authority. See [the producer contract](docs/executor-producer-profile.md).

Revision-specific test results and workflow commentary are maintained in the [central test hub](https://github.com/cogno-us/cognous-stack-qualification/blob/main/results/readme-commentary/index.md).

## Execution Envelope 0.2.0

At API entry Moltbot Safe deep-snapshots the complete operation, including nested payload values, before any trusted resolver or Control Plane callback can run. Validation and destination execution use only this frozen snapshot.

For the single-refund adapter:

- `effects` must be the integer `1` exactly; booleans, zero, negatives, fractions and other counts are rejected;
- amount must be a finite non-negative `int` or `float`, excluding booleans;
- the payload commitment must match the frozen payload;
- effect identity is bound to a digest of the exact frozen operation.

## Trusted institution/domain binding

The pinned Control Plane checks institution and authority domain but does not export them in `AuthorizationBinding`.

Until the upstream contract is extended, Moltbot Safe derives both values from the **trusted Authority Context resolver used by the revalidating Control Plane workflow** and compares them exactly with the execution snapshot. A caller-supplied institution/domain label alone cannot satisfy the boundary.

The exact requested upstream extension is documented in [docs/control-plane-interface-gap.md](docs/control-plane-interface-gap.md).

## Destination, attempts and recovery

The synthetic destination is SQLite only. It does not touch real accounts, payment services, public chains or production credentials.

Each destination submission gets a durable attempt identity. Attempt rows are immutable identities and state changes are append-only attempt events. Reusing an attempt ID is rejected and recorded under a fresh denied attempt; it never overwrites earlier success.

SQLite `BEGIN IMMEDIATE` serializes effect deduplication, content binding and cumulative local effect-count checks for processes sharing one database file. The test suite exercises **separate processes**, not only threads.

A duplicate same-operation delivery is observed/reconciled and reports `newly_executed=false`. A conflicting operation under the same effect ID is rejected. Lost acknowledgement after durable commit remains `unknown` until observation establishes destination state. Partial delivery remains partial/held rather than being blindly re-applied.

Historical observation is available without renewing authority, but observation alone cannot authorize a new execution.

## Opt-in atomic local authority/effect profile

Merged executor PR #25 adds an **opt-in local profile** for synthetic SQLite effects. It is separate from the default `PinnedControlPlaneExecutor` path.

The enforceable path begins with the Control Plane's trusted authority handoff:
final resolution, coherent active/current snapshot validation and exact claim
provisioning occur while the source excludes its invalidating writers. After
that provisioning commits, one SQLite file becomes authoritative for the local
profile's mutable grant, approval, policy and evidence state, exact execution
claims, shared effect budgets and protected effects. `BEGIN IMMEDIATE` orders
invalidating writes against claim consumption and effect insertion. Trusted time
is evaluated after the write transaction is acquired.

The profile refuses activation over prior legacy effects or attempts, and the legacy destination write path refuses to write into an opted-in database. Lost acknowledgement and later recovery reconcile only when the caller supplies
the exact original execution envelope: retained claim decision/effect IDs,
canonical operation commitment, and transaction-retained destination operation
digest must all match. Substituted decision IDs, target, amount or payload remain
hold/unknown and are never attributed the historical effect. A consumed claim is
never reopened automatically.

See [docs/local-authority-effect-profile.md](docs/local-authority-effect-profile.md). The implementation is merged and selected by the hub. Hub PR #25 exposes it as an explicit optional profile; it does not replace the ordinary revalidation path.

## Opt-in refund-intent ownership

[Executor PR #14](https://github.com/cogno-us/cognous-execution-runtime/pull/14) is merged and included in the selected executor revision. The hub exposes `refund-intent` separately from `atomic-authority-effect`. Refund-intent ownership prevents repeated protected effects under its declared stable intent identity, including across distinct authorized operation identities. It is not a general inference engine for recognizing equivalent business meaning.

The intent registry and atomic authority/effect profile remain separate per database; mixed-profile activation is rejected. Combined enforcement is not claimed. The ordinary path's effect-ID deduplication alone does not prevent equivalent business intent under different identities. See [the hub optional-profile guide](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/optional-execution-profiles.md).

## Optional OpenShell environment

[OpenShell adapter 0.1.0](docs/openshell-adapter.md) adds an opt-in dedicated local
Docker sandbox destination beneath the same Python Control Plane integration.
It pins OpenShell v0.1.2, exact operation/configuration identity, and conservative
recovery. Live enforcement remains unverified in the development environment.
The existing host-local SQLite path remains the default.

## Isolation boundary

Implemented for the default host-local path:

- no subprocess execution;
- no network calls from the destination adapter;
- no production credentials;
- dedicated operator-selected SQLite state root;
- path traversal and existing symlink-component rejection before path resolution;
- exact local restrictions on institution, authority domain, adapter, action, target prefix, unit, amount and effect count.

These are application-level restrictions, **not OS confinement**. Filesystem checks still have residual check/use races without an OS-level dirfd/openat-style confinement strategy. A separate host process with sufficient permissions can bypass this Python package. No container, VM, seccomp/AppArmor, network namespace or whole-upstream bypass-resistance claim is made.

## Evidence and deployment scope

Use the [hub quickstart](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/quickstart.md) for the selected reference and the [central test hub](https://github.com/cogno-us/cognous-stack-qualification/blob/main/results/readme-commentary/index.md) for qualification records. Local profiles do not establish distributed execution, remote revocation, external-destination atomicity, hostile-host resistance, production identity or production readiness. The fixed-worker confinement profile does not qualify arbitrary agents, the quarantined upstream application or live OpenShell.

For stakeholder orientation, see the [business overview](collateral/business-collateral.md) and [one-page overview](collateral/one-page-overview.md).

## Tests

Focused and pinned integration tests run with:

```bash
PYTHONPATH=".:pinned/control-plane/src" \
MOLTBOT_SAFE_CONTROL_PLANE_ROOT="pinned/control-plane" \
MOLTBOT_SAFE_MANIFEST_FIXTURE="pinned/action-manifest/examples/refund_integration_v1_1.manifest.json" \
pytest -q tests
```

The upstream TypeScript Moltbot test suite and its CI were quarantined with the application at the tag above; they are not run on `main` and are not Python execution-boundary evidence.

## License and attribution

The Cognous Python execution layer is licensed under [Apache 2.0](LICENSE-APACHE-2.0); see [NOTICE](NOTICE) for scope. The quarantined upstream Moltbot application remains [MIT-licensed](LICENSE) at the quarantine tag, with all third-party notices preserved there; the MIT license file is kept on `main` for upstream-derived retained material and the tag history. Licensing does not change the reviewed execution boundary or establish an audit of the upstream application.

## Repository locations

See the [repository rename map and compatibility notes](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/repository-renames.md) for current component URLs. Existing package names, schema identifiers and retained producer identities are unchanged.

## Bibliography

Selected external sources from the October 2026 research review. These inform evaluation questions; they do not establish Cognous implementation, adoption, conformance or production qualification.

- [OWASP GenAI Security Project. *State of Agentic AI Security and Governance*, version 2.01 (June 2026)](https://genai.owasp.org/resource/state-of-agentic-ai-security-and-governance/). Security synthesis covering agent identity, delegated permissions, tool access and containment.
- Jonathan Chadbourne / JCEE Labs. *When a Timeout Is Not a Failure: Authority, Evidence, and Recovery in Consequential AI Execution*. Technical Note 001, public release v0.1.1 (6 October 2026). Technical note on uncertain outcomes and recovery. An original public URL has not been verified; no substitute or private copy is linked.
- [Chip Huyen. *Designing Machine Learning Systems*. O’Reilly (2022)](https://www.oreilly.com/library/view/designing-machine-learning/9781098107956/). Engineering reference for monitoring and deployment evaluation; the research review used only the supplied second part.

See the [research bibliography](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/research-bibliography.md) for review scope and source-verification limits.

## Cognous stack components

The six groups organize component responsibilities. They are not an execution
sequence or a requirement to install every component. The [hub lock](https://github.com/cogno-us/cognous-open-control-stack/blob/main/component-lock.json)
defines the selected integration; [release status](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/release-status.md)
defines its evidence and limits.

| Group | Components |
| --- | --- |
| **01 · Knowledge & Provenance** | [The Index (Evidence Registry)](https://github.com/cogno-us/cognous-evidence-registry) · [BitRep (Evidence Attestation)](https://github.com/cogno-us/cognous-evidence-attestation) |
| **02 · Institutional Governance** | [Alvorada (Institutional Governance)](https://github.com/cogno-us/cognous-institutional-governance) · authority contracts |
| **03 · Declaration & Policy Control** | [Action Manifest](https://github.com/cogno-us/cognous-action-manifest) · [Control Plane](https://github.com/cogno-us/cognous-control-plane) |
| **04 · Constrained Execution** | [Execution Runtime (Moltbot Safe)](https://github.com/cogno-us/cognous-execution-runtime) |
| **05 · Replay & Evidence** | [Replay Bundle](https://github.com/cogno-us/cognous-replay-bundle) · [Governance Evidence Pack](https://github.com/cogno-us/cognous-governance-evidence-pack) · [ODES](https://github.com/cogno-us/open-decision-evidence-standard) |
| **06 · Reasoning & Human Oversight** | [PRP](https://github.com/cogno-us/portable-reasoning-protocol) · [Research Intelligence](https://github.com/cogno-us/research-intelligence-protocol) · [TFA](https://github.com/cogno-us/truth-freedom-agency-protocol) |

**Cross-cutting exchange:** [Governed Exchange (GAX/IMX)](https://github.com/cogno-us/cognous-governed-exchange) connects
governed messages, recipient assessment and continuity across the groups. It is
separate from Alvorada's institutional authority framework.

Execution Runtime and Moltbot Safe name the same repository here. The supported
Cognous Python execution boundary remains distinct from the quarantined upstream
application. PRP, Research Intelligence and TFA are optional behavioral
instructions; they do not enforce authorization or substitute for human oversight.
A signature, registry entry, message receipt or evidence summary does not grant
execution authority.

## Cross component qualification

Executable integration suites and harnesses moved to the [qualification repository](https://github.com/cogno-us/cognous-stack-qualification/tree/665cfb68e263ccb060b4ac378b6969eb77bd84d5/suites/cognous-execution-runtime). See [the path migration map](CROSS_COMPONENT_TESTS.md) for current commands and source identity rules. Component-local tests remain here; historical qualification results retain their original scope.

<!-- Retained anchors for existing documentation links. -->
<a id="accepted-integration-and-versioned-evidence"></a>
