<!-- cognous-banner:start -->
```text
──────────────────────────────────────────────────
   __________  _______   ______  __  _______
  / ____/ __ \/ ____/ | / / __ \/ / / / ___/
 / /   / / / / / __/  |/ / / / / / / /\__ \
/ /___/ /_/ / /_/ / /|  / /_/ / /_/ /___/ /
\____/\____/\____/_/ |_/\____/\____//____/
           CONSTRAINED AGENT EXECUTION
       g o v e r n e d   b y   d e s i g n
  github.com/cogno-us/cognous-open-control-stack
──────────────────────────────────────────────────
```
<!-- cognous-banner:end -->

# Moltbot Safe

**Constrained execution beneath independent current authorization.**

## Overview

The Python execution layer under engine/ provides the stack's bounded synthetic destination and adapter boundary. The repository also retains the upstream TypeScript Moltbot application; that application is outside the reviewed Cognous Python execution boundary.

**Implementation status:** this README describes merged public reference work. Component acceptance, selection in the hub and execution of a qualification are separate facts. The selected revision for this component is `177354e959cc78c59c1a776f018cfbfbf28c927b`; the [hub lock](https://github.com/cogno-us/cognous-open-control-stack/blob/5737267d94d2b445735c95e8480a31de73a2abe8/component-lock.json) is the source of that integration choice.

## Purpose and intended users

An authorization record is not proof that an effect was performed, and a timeout is not proof that it was absent. The executor needs exact operation binding, bounded destination behavior and observable recovery state that cannot be replaced by a generated success message.

Engineers can inspect the reference contracts and examples; enterprise architecture, security and governance reviewers can examine the boundary and evidence. Evaluate this component for its named responsibility rather than as a complete governance platform.

## Key features

| Capability | Implemented or specified responsibility |
|---|---|
| **Frozen operation** | Snapshot nested request content before callbacks and bind the effect to that exact operation. |
| **Strict local policy** | Constrain institution, authority domain, action, adapter, target, amount, unit and effect count. |
| **SQLite destination** | Serialize same-effect suppression, conflicting-content rejection and cumulative local effect counting. |
| **Versioned evidence** | Export Execution Envelope 0.2.0 and executor producer profile 2.0.0 with separate attempt/observation history. |
| **Optional OpenShell path** | Provide a pinned adapter, packaged-worker qualification and readiness tooling, separately from the default host-local destination. |

## How it works

The supported path uses the pinned Control Plane workflow to revalidate authority before ControlPlaneRefundDestinationAdapter reaches the SQLite destination. Duplicate delivery of the same operation is reconciled without a second effect. Conflicting content under the same effect ID fails. Unknown acknowledgement, partial delivery and accepted observation remain distinct facts; observed absence never grants retry permission.

A valid signature, chain inclusion, message receipt, reasoning instruction or evidence-package digest does not authorize execution. Institutional authority must be supplied and evaluated through the appropriate trusted boundary.

## Getting started

Use the [hub quickstart](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/quickstart.md) for the selected integrated reference. Component test setup is defined in the [Python safety workflow](https://github.com/cogno-us/moltbot-safe/blob/31cd5dc5bc5cc4bf8d3c62e69737ec7a74e1f28d/.github/workflows/python-safety.yml), including exact producer checkouts and environment variables. The [OpenShell adapter guide](https://github.com/cogno-us/moltbot-safe/blob/31cd5dc5bc5cc4bf8d3c62e69737ec7a74e1f28d/docs/openshell-adapter.md) describes the opt-in path; do not provision or infer live confinement from mock tests.

## Evidence and supported scope

The hub selects executor `177354e959cc78c59c1a776f018cfbfbf28c927b` with producer profile **2.0.0** and Execution Envelope **0.2.0**. This repository's Python CI separately pins Control Plane `2ea9528eeb87e14ff10f05de06473122b9df540f`; the accepted hub tests the repaired persistence generation. Neither pin should be silently substituted for the other. Packaged-image qualification and the readiness package are separately accepted; [the readiness checkpoint](https://github.com/cogno-us/moltbot-safe/blob/31cd5dc5bc5cc4bf8d3c62e69737ec7a74e1f28d/docs/workstreams/live-openshell-qualification-checkpoint.md) records the actual Docker evidence and blocked live prerequisites.

The accepted [hub persistence-generation evidence](https://github.com/cogno-us/cognous-open-control-stack/blob/5737267d94d2b445735c95e8480a31de73a2abe8/examples/control-plane-store-adoption/qualification-summary.json) records 915 Python tests in each of two repetitions, 35 matrix entries satisfying their gates and 120 separate mocked OpenShell tests. Those are aggregate hub results, not a per-component test count or a claim of production readiness. Optional behavioral layers receive static checks only. The [support ledger](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/release-status.md) separates implementation, execution and adoption.


### Protected worker and pending intent work

The [accepted hub campaign](https://github.com/cogno-us/cognous-open-control-stack/blob/5737267d94d2b445735c95e8480a31de73a2abe8/docs/workstreams/protected-qualification-checkpoint.md#completed-compatible-host-review) passed twelve isolated cases (six scenarios, two repetitions) and 17 verifier tests on Ubuntu 22.04.5, Linux 6.8.0-1064-azure, bubblewrap 0.6.1 and Python 3.11.16. It is a separate fixed Linux worker fixture with host-owned authority and destination inspection. It does not qualify OpenShell, the TypeScript application, arbitrary agents or real credential isolation. The earlier Ubuntu 24.04 campaign remains blocked in its own evidence.

[Executor PR #14](https://github.com/cogno-us/moltbot-safe/pull/14) is pending acceptance at this documentation snapshot. Logical-intent prevention is not selected or qualified by the hub. Equivalent intent under different valid proposals can still create multiple effects in the selected reference; effect-ID deduplication is not business-intent deduplication.

## Limitations and deployment decisions

Application restrictions do not establish OS confinement of the whole repository. A sufficiently privileged host process can bypass the Python layer. The separately accepted hub bubblewrap campaign qualifies only its fixed worker and recorded environment, not this application generally. Live OpenShell confinement and logical-intent prevention are not hub-supported.

Review original artifacts and their exact source revisions before extending a claim to a new environment. New dependencies, authority sources, destinations or enforcement mechanisms need their own compatibility and qualification. A passing reference case is not a certification of an enterprise deployment.

## Repository guide

Use these sources for details; their historical checkpoints retain the status and scope of the work they recorded:

- [docs/executor-producer-profile.md](https://github.com/cogno-us/moltbot-safe/blob/31cd5dc5bc5cc4bf8d3c62e69737ec7a74e1f28d/docs/executor-producer-profile.md)
- [docs/openshell-adapter.md](https://github.com/cogno-us/moltbot-safe/blob/31cd5dc5bc5cc4bf8d3c62e69737ec7a74e1f28d/docs/openshell-adapter.md)
- [docs/workstreams/live-openshell-qualification-checkpoint.md](https://github.com/cogno-us/moltbot-safe/blob/31cd5dc5bc5cc4bf8d3c62e69737ec7a74e1f28d/docs/workstreams/live-openshell-qualification-checkpoint.md)
- [docs/workstreams/executor-observation-checkpoint.md](https://github.com/cogno-us/moltbot-safe/blob/31cd5dc5bc5cc4bf8d3c62e69737ec7a74e1f28d/docs/workstreams/executor-observation-checkpoint.md)

For a nontechnical introduction, read the [business overview](collateral/business-collateral.md) and [one-page overview](collateral/one-page-overview.md). Both describe this component's role and evidence limits, not additional runtime features.

## Contributing and attribution

[Contribution guidance](CONTRIBUTING.md) describes review and validation expectations. Keep evidence-linked claims, preserve historical records and separate proposed features from accepted implementation.

The Cognous Python execution layer uses [Apache 2.0](LICENSE-APACHE-2.0); the retained upstream application remains [MIT](LICENSE). See [NOTICE](NOTICE) for scope and third-party attribution. No license terms change here.

---

## Bibliography

Selected external sources from the October 2026 research review. These inform evaluation questions; they do not establish Cognous implementation, adoption, conformance or production qualification.

- [OWASP GenAI Security Project. *State of Agentic AI Security and Governance*, version 2.01 (June 2026)](https://genai.owasp.org/resource/state-of-agentic-ai-security-and-governance/). Security synthesis covering agent identity, delegated permissions, tool access and containment.
- Jonathan Chadbourne / JCEE Labs. *When a Timeout Is Not a Failure: Authority, Evidence, and Recovery in Consequential AI Execution*. Technical Note 001, public release v0.1.1 (6 October 2026). Technical note on uncertain outcomes and recovery. An original public URL has not been verified; no substitute or private copy is linked.
- [Chip Huyen. *Designing Machine Learning Systems*. O’Reilly (2022)](https://www.oreilly.com/library/view/designing-machine-learning/9781098107956/). Engineering reference for monitoring and deployment evaluation; the research review used only the supplied second part.

See the [research bibliography](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/research-bibliography.md) for review scope and source-verification limits.

## Cognous stack components

[Stack hub](https://github.com/cogno-us/cognous-open-control-stack) · [Selected pins](https://github.com/cogno-us/cognous-open-control-stack/blob/main/component-lock.json) · [Evidence and limits](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/release-status.md)

Component links are navigation, not a requirement to install every component. The hub lock determines its supported integration.

| Component | Responsibility |
|---|---|
| [Agent Action Manifest](https://github.com/cogno-us/cognous-agent-action-manifest) | Declare the action before evaluating permission |
| [Agent Control Plane](https://github.com/cogno-us/cognous-agent-control-plane) | Evaluate proposals against authority and preserve the decision record |
| [Agent Replay Bundle](https://github.com/cogno-us/cognous-agent-replay-bundle) | Reconstruct what the retained records support |
| [Agent Governance Evidence Pack](https://github.com/cogno-us/cognous-agent-governance-evidence-pack) | Turn traceable runtime records into reviewable governance evidence |
| [Open Decision Evidence Standard](https://github.com/cogno-us/open-decision-evidence-standard) | Portable decision evidence across system and organizational boundaries |
| [Alvorada Experimental Workbench](https://github.com/cogno-us/alvorada) | Governed exchange and continuity for a bounded synthetic workflow |
| [BitRep](https://github.com/cogno-us/bitrep) | Verify issuer signatures under explicit trust assumptions |
| [The Index](https://github.com/cogno-us/the-index) | A local blockchain reference for claims, evidence commitments and lifecycle history |
| [Portable Reasoning Protocol v1.0](https://github.com/cogno-us/portable-reasoning-protocol) | Portable instructions for evidence-bounded reasoning |
| [Research Intelligence Protocol v1.0](https://github.com/cogno-us/research-intelligence-protocol) | Disciplined discovery and cross-domain abstraction, kept separate |
| [TFA Protocol (S43)](https://github.com/cogno-us/truth-freedom-agency-protocol) | Truth · Freedom · Agency |
| [Constitutional Governance for Institutions](https://github.com/cogno-us/constitutional-governance-for-institutions) | Alvorada: authority, challenge and correction for institutions |
