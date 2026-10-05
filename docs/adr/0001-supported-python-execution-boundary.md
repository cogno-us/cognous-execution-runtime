# ADR 0001: Supported Python execution boundary

## Decision

Support the Python `SafeExecutor` as the sole Moltbot Safe execution boundary for the current Cognous synthetic pilot.

Retain the TypeScript Moltbot application as upstream history/code, but do not claim it is governed by this boundary.

Use the pinned Control Plane issued decision through an in-process trusted lookup, apply stricter local policy, and perform only a local SQLite synthetic refund effect.

## Rationale

The previous Python engine was logging-only while reporting execution, and the repository's tests targeted a different API. The TypeScript application is too broad to designate as the reviewed safety boundary without a separate audit and integration program.

SQLite supplies a concrete durable local effect, transactionally serialized effect identity and local cumulative limits without introducing network credentials or a production integration.

## Consequences

- Legacy `AgentEngine` cannot report execution.
- The safety pilot has one narrow, testable supported path.
- Full-upstream bypass resistance is not claimed.
- Transport authentication remains external.
- The Control Plane institution/domain binding gap is returned to the Governor as an interface proposal.
