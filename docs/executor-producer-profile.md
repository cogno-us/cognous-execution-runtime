# Executor producer profile 1.0.0

The supported Python execution boundary exports a versioned producer contract for
consumers such as Agent Replay Bundle and the GAX/IMX reference integration.

Profile ID: `urn:cognous:profiles:moltbot-safe-executor-producer`  
Profile version: `1.0.0`  
Execution Envelope version: `0.2.0`

The contract contains the exact execution envelope/result plus bounded destination
effects, attempts, append-only attempt events and producer observation records.

The profile version is an interface version. It is not a repository revision.
A repository revision can be emitted separately as source-asserted provenance.
Consumers must not treat that assertion as independently established provenance.

The producer export does not create authority, approvals, grants, policies or
resolvers. Those remain caller-supplied inputs to the Control Plane/executor path.

Legacy unversioned exports remain historical artifacts. They are not relabeled as
profile 1.0.0; consumers that continue to accept them must do so through an
explicit legacy compatibility path with revision-pinned handling.
