<!-- cognous-banner:start -->
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

**A bounded synthetic destination for effects, reached only through a Control Plane decision.**

**Status:** group 04 · Constrained Execution; lock status **selected** (Execution Envelope 0.2.0, executor producer profile 2.0.0, OpenShell adapter 0.1.0); the optional profiles listed below are **merged** or **proposed** as stated per row and are not selected by themselves; contract drafts stay `0.1.0-draft`. Nothing here is a release; operational trust remains [HOLD under #30](https://github.com/cogno-us/cognous-stack-orchestrator/issues/30). The retained Python package and producer identities keep their Moltbot Safe names for compatibility.

## What it is

Cognous Execution Runtime provides the **single-agent constrained execution boundary** in the Cognous Open Control Stack: a Python package (`engine/`, no third-party dependency of its own) that takes a frozen execution envelope, lets the Control Plane revalidate current authority, applies a strict local policy, writes a synthetic SQLite refund with a durable attempt record and exports retained artifacts for Replay and the exchange reference.

**Who it is for.** Engineers who want to inspect the execution-side contracts and run them locally, and architecture, security and governance reviewers who want to see what an execution boundary leaves behind as evidence. Evaluate it for that named responsibility, not as a payment system.

**What it does not do.** It is not OS confinement, it does not touch real accounts or payment services, it does not cover the quarantined upstream application and it does not establish exactly-once delivery or external settlement. A persisted `authorized` decision is not an execution credential, an allow decision is not an executed effect, and an execution acknowledgement is not independently verified delivery. The limits are listed under [what it does not do](#what-it-does-not-do-known-limits).

**Worked recovery example.** A synthetic refund effect can commit while its acknowledgement is lost. Preserve the original effect and attempt identity, record the outcome as `unknown` and reconcile that attempt. Unknown or absent observations do not authorize a replacement. A supplied observation does not by itself prove source authenticity or external settlement.

**Upstream application quarantined.** The upstream Moltbot TypeScript/native application formerly retained in this repository is **quarantined**. It is preserved only at the tag [`upstream-moltbot-quarantine-20261016`](https://github.com/cogno-us/cognous-execution-runtime/tree/upstream-moltbot-quarantine-20261016) (commit `fd6b76fa0e85feb5b67324114bf790d3a12fdaf1`) and is **not shipped on `main`**. It is not maintained, not reviewed and not part of this execution boundary, and it carries 18 known unrepaired security findings (6 high). Its earlier commits remain in public Git history. See the [quarantine record](docs/quarantine-upstream-moltbot-20261016.md).

## Where it fits

<a id="cognous-stack-components"></a>

<!-- stack-map:start -->
<!-- Canonical block: identical text in every component README. Source: docs/stack-map.md in the hub. Refresh at the pin round. -->
The Open Control Stack groups its repositories by responsibility. The groups are a navigation map, not an execution sequence and not a requirement to install every component. The hub [component lock](https://github.com/cogno-us/cognous-open-control-stack/blob/main/component-lock.json) is the only source of selected revisions; [release status](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/release-status.md) states their evidence and limits. Last checked against `component-lock.json` at hub commit `dcc9bd7533ef`.

| Group | Members |
| --- | --- |
| **01 Knowledge & Provenance** | [Evidence Registry (The Index)](https://github.com/cogno-us/cognous-evidence-registry) · [Evidence Attestation (BitRep)](https://github.com/cogno-us/cognous-evidence-attestation) |
| **02 Institutional Governance** | [Institutional Governance (Alvorada)](https://github.com/cogno-us/cognous-institutional-governance) |
| **03 Declaration & Policy Control** | [Action Manifest](https://github.com/cogno-us/cognous-action-manifest) · [Control Plane](https://github.com/cogno-us/cognous-control-plane) |
| **04 Constrained Execution** | [Execution Runtime](https://github.com/cogno-us/cognous-execution-runtime) |
| **05 Replay & Evidence** | [Replay Bundle](https://github.com/cogno-us/cognous-replay-bundle) · [Governance Evidence Pack](https://github.com/cogno-us/cognous-governance-evidence-pack) · [ODES](https://github.com/cogno-us/open-decision-evidence-standard) |
| **Cross-cutting exchange** | [Governed Exchange (GAX/IMX)](https://github.com/cogno-us/cognous-governed-exchange) |
| **Optional reasoning protocols** (side group) | [Portable Reasoning Protocol (PRP)](https://github.com/cogno-us/portable-reasoning-protocol) · [Research Intelligence Protocol](https://github.com/cogno-us/research-intelligence-protocol) · [TFA Protocol](https://github.com/cogno-us/truth-freedom-agency-protocol) |
| **Tooling and qualification** (side group) | [Ergonomics (`cognous-ocs`)](https://github.com/cogno-us/cognous-open-control-stack-ergonomics) · [Verifier](https://github.com/cogno-us/cognous-open-control-stack-verifier) · [Orchestrator](https://github.com/cogno-us/cognous-stack-orchestrator) · [Qualification](https://github.com/cogno-us/cognous-stack-qualification) |

Status labels used in this README: **merged** (reviewed and merged in the owning repository; not by itself selected), **selected** (pinned in the hub lock), **known limit** (a limitation that is stated and kept visible) and **proposed** (draft or planned work, not a supported interface). Full map, names and commands: [stack map](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/stack-map.md), [naming](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/naming.md), [profiles and tools](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/profiles-and-tools.md). A signature, registry entry, message receipt or evidence summary does not grant execution authority.
<!-- stack-map:end -->

This component sits in group 04. Upstream of it are [Action Manifest](https://github.com/cogno-us/cognous-action-manifest) (declares the action) and [Control Plane](https://github.com/cogno-us/cognous-control-plane) (decides and revalidates; the runtime adapter calls its `BoundedAuthorizationWorkflow.execute()`). Downstream, [Replay Bundle](https://github.com/cogno-us/cognous-replay-bundle), [Governance Evidence Pack](https://github.com/cogno-us/cognous-governance-evidence-pack) and [ODES](https://github.com/cogno-us/open-decision-evidence-standard) consume what it exports, and the [exchange reference](https://github.com/cogno-us/cognous-governed-exchange) runs it as a destination. Execution Runtime and Moltbot Safe name the same repository. Revision-specific qualification commentary is in the [central test hub](https://github.com/cogno-us/cognous-stack-qualification/blob/main/results/readme-commentary/index.md); for audience navigation use the hub [start guide](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/start-here.md).

## Quick start

Prerequisites: Linux and Python 3 (CI uses 3.12; 3.13 was also run). Nothing is installed for the demo and nothing contacts a network. There is no package to install; run from the repository root of a fresh checkout.

The demo builds one synthetic refund operation, executes it twice against a temporary SQLite destination and exports the retained artifacts. It stands in for the Control Plane decision, so it shows the destination and producer behaviour, not authorization:

```bash
PYTHONPATH=. python - <<'PY'
import tempfile
from engine.producer_contract import (
    DurableRefundDestination, ExecutionEnvelope, ExecutionOperation, LocalExecutionPolicy,
    commitment, export_execution_artifacts)
from engine.safe_executor import EXECUTION_ENVELOPE_VERSION, LocalDestinationExecutor, snapshot_envelope

payload = {"refund_reason": "demo"}
op = ExecutionOperation(
    actor="urn:cognous:identity:agent-1", principal="urn:cognous:principal:service",
    institution_id="urn:cognous:institution:demo", authority_domain="customer-refunds",
    manifest_id="refund-integration-v1-1", manifest_version="1.1", manifest_digest="sha256:" + "a" * 64,
    proposal_commitment="sha256:" + "b" * 64, action_id="refund.issue",
    adapter_id="urn:cognous:adapter:synthetic-refund", target="urn:cognous:synthetic-account:customer-1",
    payload=payload, payload_commitment=commitment(payload), requested_permissions=("refund.issue",),
    amount=50.0, unit="USD", effects=1, authority_context_id="urn:cognous:authority-context:refund-demo",
    requirement_id="urn:cognous:requirement:refund-t1", grant_id="urn:cognous:grant:refund-1",
    grant_revision="1", effective_max_effects=1)
policy = LocalExecutionPolicy(
    allowed_institutions=frozenset({op.institution_id}), allowed_authority_domains=frozenset({op.authority_domain}),
    allowed_adapters=frozenset({op.adapter_id}), allowed_actions=frozenset({op.action_id}),
    allowed_target_prefixes=("urn:cognous:synthetic-account:",), allowed_units=frozenset({"USD"}),
    max_amount=1000.0, max_effects=1)
envelope = ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION, "decision-1", "effect-1", op)
with tempfile.TemporaryDirectory() as state:
    destination = DurableRefundDestination(state)
    executor = LocalDestinationExecutor(destination, policy)
    first = executor.execute_snapshot(snapshot_envelope(envelope))
    again = executor.execute_snapshot(snapshot_envelope(envelope))
    exported = export_execution_artifacts(envelope, first, destination)
print("first :", first.status, "newly_executed =", first.newly_executed, "observed_state =", first.observed_state)
print("again :", again.status, "newly_executed =", again.newly_executed)
print("export:", exported["producer_profile"]["profile_version"], "attempts =", len(exported["attempts"]), "effects =", len(exported["effects"]))
PY
```

Expected output (about one second; exit code 0):

```text
first : executed newly_executed = True observed_state = applied
again : reconciled newly_executed = False
export: 2.0.0 attempts = 2 effects = 1
```

What this shows: the effect is applied once; a duplicate delivery of the same operation is observed and reconciled rather than applied again; the export carries producer profile 2.0.0 with one effect. What it does not show: authorization (no Control Plane is involved), a real destination or exactly-once delivery.

To run the test suite, see [Run the tests](#run-the-tests). The supported end-to-end path (Control Plane decision, then this boundary) is exercised by the cross-component run described there and by the hub [quickstart](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/quickstart.md).

## What ships

The supported runtime is the Python package under `engine/`. The default path is:

```text
Pinned Control Plane BoundedAuthorizationWorkflow.execute()
        -> current grant / approval / policy / evidence revalidation
        -> Moltbot Safe ControlPlaneRefundDestinationAdapter
        -> strict local execution policy
        -> durable synthetic SQLite refund
        -> destination observation / reconciliation
```

| Item | Where | Default or optional | Label | Page |
| --- | --- | --- | --- | --- |
| Default path (route A): `PinnedControlPlaneExecutor`, `ControlPlaneRefundDestinationAdapter` | `engine/control_plane_adapter.py` | default | selected | [execution boundary](docs/execution-boundary.md) |
| Local destination and policy: `LocalDestinationExecutor`, `DurableRefundDestination`, `LocalExecutionPolicy`, envelope 0.2.0 | `engine/safe_executor.py` | default | selected | [execution boundary](docs/execution-boundary.md) |
| Producer export: executor producer profile 2.0.0 | `engine/producer_contract.py` | default | selected | [producer profile](docs/executor-producer-profile.md) |
| Atomic local authority/effect profile (route B): `AtomicLocalControlPlaneExecutor`, `AtomicAuthorityEffectDestination` | `engine/atomic_local_control_plane_executor.py`, `engine/local_authority_effect.py`, `engine/local_authority_effect_recovery.py` | optional, opt-in | merged; exposed by the hub as an optional profile | [profile](docs/local-authority-effect-profile.md) |
| Effective grant precondition: `EffectiveGrantDestination` | `engine/effective_grant.py` | optional, opt-in | merged | [execution boundary](docs/execution-boundary.md#optional-local-effective-grant-precondition) |
| Refund-intent ownership: `RefundIntentRegistry`, `IntentRefundDestination` | `engine/refund_intent.py` | optional, opt-in | merged; included in the selected revision | [checkpoint](docs/workstreams/refund-intent-registry-checkpoint.md) |
| OpenShell adapter 0.1.0 (dedicated local Docker sandbox destination) | `engine/openshell_environment.py`, `engine/openshell_worker.py`, `examples/openshell/` | optional, opt-in | selected interface; live enforcement is a known limit | [adapter](docs/openshell-adapter.md) |
| Operation admission | `engine/operation_admission.py` | optional, opt-in | merged; bounded qualification profile | [page](docs/o6-p1a-operation-admission.md) |
| Revocation closeout | `engine/revocation_closeout.py` | optional, opt-in | merged; bounded evidence profile | [page](docs/o6-p1d-revocation-closeout.md) |
| Recovery resolution (synthetic ledger) | `engine/recovery_resolution.py` | optional, opt-in | merged; experimental draft | [page](docs/recovery-resolution-synthetic.md) |
| Operation identity, dedupe-ttl and delayed-commit destinations | `engine/operation_identity_destination.py`, `engine/operation_identity_atomic_destination.py`, `engine/dedupe_destination.py`, `engine/delayed_commit_destination.py` | optional, opt-in (CI and local synthetic destinations) | merged | no page; see the module docstrings and the [changelog](CHANGELOG.md) |
| Local stop journal | `engine/c8_source_evidence.py` | optional | merged | [W2 qualification](docs/workstreams/w2-stop-recovery-qualification.md) |
| Legacy shell: `AgentEngine`, `PermissionSystem`, `AuditLog`, action schema | `engine/engine.py`, `engine/permissions.py`, `engine/audit.py`, `engine/action_schema.py` | legacy | compatibility only; validates, permission-checks and logs, performs no effect | [API](docs/api.md), [internals](docs/internals.md) |
| Legacy CLI | `cli/moltbot_safe_cli.py` | legacy | known limit: not supported; importing it raises `ImportError` (it imports a name that `engine.permissions` does not define) | none |
| Examples | `examples/denied_action_demo.py`, `echo_agent.py`, `file_writer_agent.py`, `permissions_demo.py` (legacy shell demos); `examples/openshell/` (OpenShell helpers) | legacy | known limit: the four shell demos are not supported and fail with `ImportError` for the same reason as the legacy CLI; the OpenShell helpers belong to the optional adapter | none |
| Vendored contract copy | `tests/contracts/execution-attempt-lifecycle/` | contract draft | `0.1.0-draft`; copied from ODES, not edited here | [conformance](docs/contract-conformance-execution-attempt-lifecycle.md) |

Profiles are never combined: the atomic profile and the effective-grant profile refuse to share a database, and the refund-intent registry and the atomic profile are separate per database (mixed activation is rejected). Combined enforcement is not claimed. See the hub [optional-profile guide](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/optional-execution-profiles.md).

## Documentation

The [documentation index](docs/README.md) lists every page in this repository by purpose and marks historical records. Start with the [execution boundary](docs/execution-boundary.md), the [architecture](docs/architecture.md) and the [threat model](docs/threat-model.md). For a non-technical introduction, read the [business overview](collateral/business-collateral.md) and [one-page overview](collateral/one-page-overview.md); both describe this component's role and evidence limits, not additional features. Policy files: [contributing](CONTRIBUTING.md), [security policy](SECURITY.md), [code of conduct](CODE_OF_CONDUCT.md), [changelog](CHANGELOG.md).

## Compatibility and selection

Use the [current hub component lock](https://github.com/cogno-us/cognous-open-control-stack/blob/main/component-lock.json) to identify the selected revision; it pins this repository at `c3c3ee7188b9` (Execution Envelope 0.2.0; executor producer profile 2.0.0; OpenShell adapter 0.1.0), and `main` is ahead of it. <!-- pin-round: refresh the lock revision here -->  Optional profiles in this repository are not automatically selected or enabled by the hub. Source acceptance, selection and deployment approval remain separate decisions.

The [current component lock](https://github.com/cogno-us/cognous-open-control-stack/blob/main/component-lock.json) defines the selected integration. The public `engine.producer_contract` exports executor producer profile **2.0.0** and Execution Envelope **0.2.0**, retaining results, effects, attempts, events and observations. Exports do not grant authority. See [the producer contract](docs/executor-producer-profile.md).

This repository's Python CI pins a Control Plane revision (`2ea9528eeb87e14ff10f05de06473122b9df540f`, also named in the [execution boundary](docs/execution-boundary.md)) that is different from the one the lock selects. Neither pin should be silently substituted for the other. <!-- pin-round: refresh the Control Plane revision here -->

### Execution Envelope 0.2.0

At API entry the runtime deep-snapshots the complete operation, including nested payload values, before any trusted resolver or Control Plane callback can run. Validation and destination execution use only this frozen snapshot. For the single-refund adapter:

- `effects` must be the integer `1` exactly; booleans, zero, negatives, fractions and other counts are rejected;
- amount must be a finite non-negative exact `int` (absolute value at most 2**53) or `float`, excluding booleans and int subclasses;
- the payload commitment must match the frozen payload;
- effect identity is bound to a digest of the exact frozen operation.

### Trusted institution and domain binding

The pinned Control Plane checks institution and authority domain but does not export them in `AuthorizationBinding`. Until the upstream contract is extended, the runtime derives both values from the **trusted Authority Context resolver used by the revalidating Control Plane workflow** and compares them exactly with the execution snapshot. A caller-supplied institution or domain label alone cannot satisfy the boundary. The requested extension is documented in [docs/control-plane-interface-gap.md](docs/control-plane-interface-gap.md).

### Destination, attempts and recovery

The synthetic destination is SQLite only. Each submission gets a durable attempt identity: attempt rows are immutable and state changes are append-only attempt events. Reusing an attempt ID is rejected and recorded under a fresh denied attempt; it never overwrites an earlier success. SQLite `BEGIN IMMEDIATE` serializes effect deduplication, content binding and cumulative local effect-count checks for processes sharing one database file; the test suite exercises separate processes. A duplicate same-operation delivery is observed and reconciled and reports `newly_executed=false`. A conflicting operation under the same effect ID is rejected. Partial delivery remains partial or held and is never blindly re-applied. Historical observation is available without renewing authority, and observation alone cannot authorize a new execution.

Lost acknowledgement after a durable commit stays `unknown` until observation establishes destination state: on route A (`LocalDestinationExecutor`, the default path) a `TimeoutError` reports `unknown`, and any other exception from the commit call is followed by one observation and reports `unknown` unless a validated observation shows the effect absent, in which case it reports `failed`. On the atomic route (route B) an error after the commit that reconciliation cannot show to be pre-commit reports `unknown`, never `denied`. Details and the route names are in the [execution boundary](docs/execution-boundary.md).

A holder process that dies before its commit never leaves a success behind: no effect row exists afterwards, the crashed attempt has no terminal success event, observation and reconciliation report no effect, and a repeated attempt id is refused. On the atomic route the unconsumed claim stays usable once. This is pinned by `tests/test_crashed_holder.py`; the atomic-route cases in it run only when a Control Plane revision that contains the local-authority-effect contract is on the path (see [Run the tests](#run-the-tests)). It covers a hard exit of one local process, not a failing host or disk.

## What it does not do (known limits)

Each item is stated as a labelled fact and kept visible.

| Label | Limit |
| --- | --- |
| known limit | Application-level restrictions, not OS confinement: no subprocess execution, no network calls from the destination adapter and no production credentials on the default path, but filesystem checks keep a check/use race without OS-level `dirfd`/`openat` confinement. A separate host process with sufficient permissions can bypass this Python package. No container, VM, seccomp/AppArmor, network namespace or whole-upstream bypass-resistance claim is made. |
| known limit | The local restrictions cover institution, authority domain, adapter, action, target, unit, amount and effect count. Targets match at a segment boundary by default (`LocalExecutionPolicy.target_match="segment"`: the target equals a listed entry or extends it at a `/` or `:` boundary; an entry ending in `/` or `:` is already a boundary). `"exact"` requires the target to equal a listed entry and `"legacy-prefix"` keeps the earlier plain prefix comparison as an explicit opt-in. In every mode a target containing NUL, control or Unicode format characters, or a `.`/`..` segment, is refused. |
| known limit | Attempt events have no transition table: `transition_attempt` accepts any status for a retained attempt in any order, and the producer export checks only that an event belongs to a retained attempt. The executors emit only sequences inside the ODES executor transition table (`tests/test_attempt_transition_characterisation.py`). A table in the export is a contract-freeze item. |
| known limit | Live OpenShell enforcement is unverified in the development environment. The fixed-worker confinement profile does not qualify arbitrary agents, the quarantined upstream application or live OpenShell. |
| known limit | Local profiles do not establish distributed execution, remote revocation, external-destination atomicity, hostile-host resistance, production identity or production readiness. The default path revalidates before dispatch and is not atomic with the destination commit. |
| known limit | Evidence status is keyed by obligation id only, not tenant; use one engine database per tenant. Pinned by `tests/test_evidence_tenant_known_limit.py`. |
| known limit | The legacy `AgentEngine` is compatibility-only and the legacy CLI does not import (see What ships). The legacy `AuditLog` is append-only by file mode and is not tamper evident. |
| known limit | A tracked generated log file, `engine/audit.log`, sits in the package directory; it is not documentation and is not evidence. |
| known limit | The upstream TypeScript Moltbot test suite and its CI were quarantined with the application; they are not run on `main` and are not Python execution-boundary evidence. |

This Python layer does not cover the quarantined upstream application, real payment processors or independently authenticated deployment. Use the [hub quickstart](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/quickstart.md) for the selected reference and the [central test hub](https://github.com/cogno-us/cognous-stack-qualification/blob/main/results/readme-commentary/index.md) for qualification records. Review original artifacts and their exact source revisions before extending a claim to a new environment.

## Run the tests

Cross-component suites and workflows moved to the [qualification repository](https://github.com/cogno-us/cognous-stack-qualification/tree/665cfb68e263ccb060b4ac378b6969eb77bd84d5/suites/cognous-execution-runtime); see [the path migration map](CROSS_COMPONENT_TESTS.md) for current commands and source identity rules. The component-local tests stay here, in three steps of increasing scope. Install the test tools first:

```bash
python -m pip install pytest==8.3.5 pydantic==2.11.9
```

1. **Component-local tests only** (about one minute): no sibling checkout is needed, and tests that need the Control Plane skip with a stated reason.

```bash
PYTHONPATH=. python -m pytest -q tests
```

   Expected: `476 passed, 33 skipped`.

2. **With the pinned siblings** (the assembly the repository's cross-component CI job uses; about two minutes). `pinned/` is not part of the repository: these commands create it, git does not ignore it, and you can delete it when done:

```bash
mkdir -p pinned
git clone https://github.com/cogno-us/cognous-control-plane pinned/control-plane
git -C pinned/control-plane checkout 2ea9528eeb87e14ff10f05de06473122b9df540f
git clone https://github.com/cogno-us/cognous-action-manifest pinned/action-manifest
git -C pinned/action-manifest checkout 46c950bed37fe3812000895430bc0312d29e37ce
PYTHONPATH=".:pinned/control-plane/src" \
MOLTBOT_SAFE_CONTROL_PLANE_ROOT="pinned/control-plane" \
MOLTBOT_SAFE_MANIFEST_FIXTURE="pinned/action-manifest/examples/refund_integration_v1_1.manifest.json" \
python -m pytest -q tests
```

   Expected: `476 passed, 33 skipped`. At this Control Plane revision the 33 skips remain, because it predates the local-authority-effect contract. <!-- pin-round: refresh the two pinned revisions and the counts here -->

3. **Atomic-route tests included**: use a Control Plane revision that contains the local-authority-effect contract, for example `6093aca7`, instead of the checkout line above for the Control Plane (`git -C pinned/control-plane checkout 6093aca7`) and run the same pytest command. Expected: `509 passed, 0 skipped` (65 deprecation warnings from a third-party model library are normal). The repository's CI does not currently run this variant, so these tests are covered locally only. <!-- pin-round: refresh this revision and count here -->

The counts above were measured on Linux with Python 3.13 at the time of writing; they change when tests are added. Under heavy machine load two mutation tests in `tests/test_recovery_resolution_mutations.py` have been seen to fail on a first run and pass when rerun alone.

Syntax check of the optional OpenShell helpers: `python -m compileall -q engine examples/openshell`.

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

<!-- Retained anchors for existing documentation links. -->
<a id="accepted-integration-and-versioned-evidence"></a>
