# Cognous Execution Runtime — One-Page Overview

## Purpose

The Python execution layer under engine/ provides the stack's bounded synthetic destination and adapter boundary. The repository also retains the upstream TypeScript Moltbot application; that application is outside the reviewed Cognous Python execution boundary.

## Problem

An authorization record is not proof that an effect was performed, and a timeout is not proof that it was absent. The executor needs exact operation binding, bounded destination behavior and observable recovery state that cannot be replaced by a generated success message.

## What It Provides

- **Frozen operation:** Snapshot nested request content before callbacks and bind the effect to that exact operation.
- **Strict local policy:** Constrain institution, authority domain, action, adapter, target, amount, unit and effect count.
- **SQLite destination:** Serialize same-effect suppression, conflicting-content rejection and cumulative local effect counting.
- **Versioned evidence:** Export Execution Envelope 0.2.0 and executor producer profile 2.0.0 with separate attempt/observation history.

## Where It Fits

The supported path uses the pinned Control Plane workflow to revalidate authority before ControlPlaneRefundDestinationAdapter reaches the SQLite destination. Duplicate delivery of the same operation is reconciled without a second effect. Conflicting content under the same effect ID fails. Unknown acknowledgement, partial delivery and accepted observation remain distinct facts; observed absence never grants retry permission.

A valid signature, chain inclusion, message receipt, reasoning instruction or evidence-package digest does not authorize execution. Institutional authority must be supplied and evaluated through the appropriate trusted boundary.

## Evidence and Limits

The [accepted hub lock](https://github.com/cogno-us/cognous-open-control-stack/blob/649df22a1392af2c4fa77e4c71749c482f82649c/component-lock.json) selects this component at `c3c3ee7188b9367cf70b08074b9c40a5c70c94ac`. Read the component's [README](../README.md) for version-specific acceptance and the [hub support ledger](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/release-status.md) for the executed scope. Component acceptance is not automatic adoption of newer revisions or production qualification.

Application restrictions do not establish OS confinement of the whole repository. A sufficiently privileged host process can bypass the Python layer. The separately accepted hub bubblewrap campaign qualifies only its fixed worker and recorded environment, not this application generally. Live OpenShell confinement is not established. Atomic local authority/effect and refund-intent ownership are separately selected optional hub profiles, not combined enforcement or production guarantees.

## Practical Next Step

Choose one bounded example and follow the [README](../README.md). Compare expected and observed results and retain uncertainty. The [business collateral](business-collateral.md) supplies evaluation questions and the component's wider context.

[Cognous](https://cogno.us) · [Source](https://github.com/cogno-us/cognous-execution-runtime) · [All stack components](https://github.com/cogno-us/cognous-open-control-stack). Existing licenses and notices apply.
