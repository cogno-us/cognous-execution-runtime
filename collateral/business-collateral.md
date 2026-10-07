# Moltbot Safe — Business Collateral

## 1. Executive Summary

The Python execution layer under engine/ provides the stack's bounded synthetic destination and adapter boundary. The repository also retains the upstream TypeScript Moltbot application; that application is outside the reviewed Cognous Python execution boundary.

## 2. The Business Problem

An authorization record is not proof that an effect was performed, and a timeout is not proof that it was absent. The executor needs exact operation binding, bounded destination behavior and observable recovery state that cannot be replaced by a generated success message.

## 3. The Component in One View

| Capability | Practical role |
|---|---|
| Frozen operation | Snapshot nested request content before callbacks and bind the effect to that exact operation. |
| Strict local policy | Constrain institution, authority domain, action, adapter, target, amount, unit and effect count. |
| SQLite destination | Serialize same-effect suppression, conflicting-content rejection and cumulative local effect counting. |
| Versioned evidence | Export Execution Envelope 0.2.0 and executor producer profile 2.0.0 with separate attempt/observation history. |
| Optional OpenShell path | Provide a pinned adapter, packaged-worker qualification and readiness tooling, separately from the default host-local destination. |

## 4. Who Should Evaluate It

Engineers can inspect the reference contracts and examples; enterprise architecture, security and governance reviewers can examine the boundary and evidence. Evaluate this component for its named responsibility rather than as a complete governance platform.

## 5. A Bounded Workflow

The supported path uses the pinned Control Plane workflow to revalidate authority before ControlPlaneRefundDestinationAdapter reaches the SQLite destination. Duplicate delivery of the same operation is reconciled without a second effect. Conflicting content under the same effect ID fails. Unknown acknowledgement, partial delivery and accepted observation remain distinct facts; observed absence never grants retry permission.

This is a reference use case. Adopting the format or running the example does not establish a production deployment, institutional acceptance or measured business benefit.

## 6. Relationship to the Stack

This component contributes **constrained execution beneath independent current authorization**. The [Cognous Open Control Stack](https://github.com/cogno-us/cognous-open-control-stack) connects declared proposals, independent authority, constrained execution and retained review evidence. Components remain separately owned and versioned; the [selected lock](https://github.com/cogno-us/cognous-open-control-stack/blob/5737267d94d2b445735c95e8480a31de73a2abe8/component-lock.json) determines which revisions participate in the supported integration.

A valid signature, chain inclusion, message receipt, reasoning instruction or evidence-package digest does not authorize execution. Institutional authority must be supplied and evaluated through the appropriate trusted boundary.

## 7. What the Evidence Supports

The hub selects executor `177354e959cc78c59c1a776f018cfbfbf28c927b` with producer profile **2.0.0** and Execution Envelope **0.2.0**. This repository's Python CI separately pins Control Plane `2ea9528eeb87e14ff10f05de06473122b9df540f`; the accepted hub tests the repaired persistence generation. Neither pin should be silently substituted for the other. Packaged-image qualification and the readiness package are separately accepted; [the readiness checkpoint](../docs/workstreams/live-openshell-qualification-checkpoint.md) records the actual Docker evidence and blocked live prerequisites.

The [accepted hub evidence](https://github.com/cogno-us/cognous-open-control-stack/blob/5737267d94d2b445735c95e8480a31de73a2abe8/examples/control-plane-store-adoption/qualification-summary.json) supports bounded synthetic integration at its exact pins. Aggregate test totals do not establish deployment benefit, compliance or independent real-world verification. The [support ledger](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/release-status.md) distinguishes the standard reference, separate protected-worker campaign and unqualified production work.

## 8. What It Does Not Establish

Application restrictions do not establish OS confinement of the whole repository. A sufficiently privileged host process can bypass the Python layer. The separately accepted hub bubblewrap campaign qualifies only its fixed worker and recorded environment, not this application generally. Live OpenShell confinement and logical-intent prevention are not hub-supported.

## 9. Evaluation Questions

- Which exact input, output and source revision will the receiving system consume?
- Who supplies trusted authority or evidence, and which assumptions remain outside this component?
- Can a reviewer trace the result to retained sources, including rejected or missing information?
- Which documented checks were actually executed in the intended environment?
- What deployment-specific work is required before relying on the result?

## 10. Why Open Reference Material Matters

Public formats, source, examples and evidence allow reviewers to inspect the claimed boundary and reproduce its checks. They also expose what has not been tested. Openness supports review; it does not substitute for independent assurance or operating responsibility.

## 11. Practical Next Step

Follow the [README](../README.md) and select one bounded use case. Inspect its inputs and expected outputs, reproduce the documented checks where prerequisites are available, and record failures and unresolved assumptions alongside passes. Use the [one-page overview](one-page-overview.md) for initial stakeholder orientation.

## 12. Status and Attribution

This collateral summarizes merged public material at repository `31cd5dc5bc5cc4bf8d3c62e69737ec7a74e1f28d` and the accepted hub baseline `5737267d94d2b445735c95e8480a31de73a2abe8`. It does not anticipate pending branches. The protected-worker result applies only to its recorded Linux/bubblewrap fixture; live OpenShell and logical-intent prevention are not hub-supported at this snapshot.

[Cognous](https://cogno.us) · [Source repository](https://github.com/cogno-us/moltbot-safe) · [Stack responsibilities](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/architecture.md). Existing licenses and third-party notices remain controlling.
