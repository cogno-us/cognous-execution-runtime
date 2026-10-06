# Executor producer evidence profile 1.0.0

The supported Python execution boundary exports a versioned producer record through
`engine.producer_contract.export_executor_evidence`.

The profile identifier is
`urn:cognous:profiles:moltbot-safe-executor-evidence:1.0.0`.

It covers the execution envelope, exact destination effect rows, attempt identities,
append-only attempt events, execution result and observation. It does not construct
authority, authenticate repository provenance, or claim independent verification.

The record distinguishes:

- producer profile/schema version;
- execution-envelope version;
- source-asserted repository revision;
- independently established provenance.

Legacy unversioned producer exports remain historical inputs for consumers that
explicitly support them. They are not relabeled as profile 1.0.0.

OpenShell remains an optional execution environment. Producer-profile compatibility
does not establish live OpenShell confinement.
