# Supported constrained execution boundary

Status: synthetic local pilot. Not a production payment, identity, authority or distributed execution system.

## Runtime selection

The supported Moltbot Safe boundary is Python `engine.safe_executor.SafeExecutor`. The retained TypeScript Moltbot application is upstream code outside this boundary. The older Python `AgentEngine` previously reported `executed` after permission-checking and logging only; it is now explicitly compatibility-only and cannot report a successful effect.

## Execution envelope 0.1.0

The envelope carries:

- `decision_id` and stable `effect_id`;
- optional caller attempt ID, otherwise generated locally;
- actor and principal;
- institution ID;
- manifest ID/version/digest and proposal commitment;
- action and adapter IDs;
- target;
- exact payload plus payload commitment;
- requested permissions;
- amount and unit;
- requested effect count and effective maximum effect count;
- Authority Context ID and requirement ID;
- grant ID and revision.

The executor validates all fields available in the pinned Control Plane `AuthorizationBinding` against the canonical issued decision obtained from the trusted in-process decision source. The exact payload is additionally checked against its commitment.

## Trusted boundary

`InProcessDecisionSource` is a narrow adapter around a trusted callable that retrieves the canonical issued Control Plane decision by ID. The execution caller does not supply the authoritative decision body.

This is an in-process trust boundary only. It does not authenticate network peers and must not be described as transport security.

## Local restrictions

`LocalExecutionPolicy` can only narrow execution. It checks institution, adapter, action, target prefix, unit, amount and effect count. It cannot convert a non-authorized Control Plane decision into permission.

## Destination semantics

`DurableRefundDestination` is SQLite-backed synthetic state. A transaction holds a write lock while it checks stable effect identity, prior effect content and cumulative grant effect count, then commits the synthetic refund row.

Observation reads the destination database independently of the execution acknowledgement. State is `absent`, `applied`, or `partial`.

- same effect + same operation: reconcile; do not reapply;
- same effect + different operation: deny;
- lost acknowledgement after commit: observe before any retry;
- partial delivery: hold; no blind retry;
- absent: remains non-delivered; a subsequent attempt must still pass authorization;
- restart: state survives because the destination database is durable.

SQLite coordination applies only to processes sharing one database file. No remote/distributed exactly-once property is claimed.

## Filesystem, process, network and credential boundary

The supported path opens only its configured SQLite destination and audit/test files. It invokes no shell, subprocess or network API and contains no production credentials. Database path traversal and symlinked database paths are rejected.

This is application-level capability minimization, not OS isolation. A separate process with host filesystem access can bypass this Python module. Container, VM, seccomp/AppArmor, network namespace and host credential controls remain deployment requirements if stronger isolation is required.
