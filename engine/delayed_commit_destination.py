"""Opt-in delayed-commit mode for the CI/local synthetic SQLite destination (delayed-commit/0.1).

Promotes the qualification suite's in-test delayed-commit stand-in into a real, documented mode, so delayed-r1 cases (a stalled
check, a commit inside the destination while the grant is renewed, a lost acknowledgement whose commit lands late) are
expressible without test-only code in the suites.

Nothing changes unless a host chooses one of the classes here: the legacy destinations, their commit path and their schema are
untouched. The mixin adds three controls, all one-shot and in-process:

* ``pause_at("observe" | "commit", callback)``  run ``callback`` at that point of the *next* call, then continue. The point
  ``observe`` is after the control plane's check and before dispatch; ``commit`` is inside the destination, before the commit
  lands. Use it to move a clock or to run a second request while the first is stalled.
* ``defer_next_commit()``  the next commit is accepted into ``in_flight`` and its acknowledgement is lost (``TimeoutError``);
  nothing is durable yet, so an observation says ``absent``. A later ``land()`` makes it durable. ``absent`` is therefore not
  proof that the effect never happens, which is what the scenario exists to show.
* ``land()`` / ``land_all()``  the deferred commit finally lands (the provider processed it late), through the real commit path
  and so under every guard the base destination enforces (operation dedupe, effective grant, cumulative cap).

There is deliberately no committed-but-invisible state: a commit is either not durable (in flight) or durable and observable.
This is a CI/local aid with no authority of its own; it is not a model of any provider.
"""
from __future__ import annotations

from typing import Callable

from .effective_grant import EffectiveGrantDestination
from .operation_identity_destination import OperationIdentityDestination, OperationIdentityEffectiveGrantDestination
from .safe_executor import DurableRefundDestination

CAPABILITY = "delayed-commit/0.1"
POINTS = ("observe", "commit")


class DelayedCommitMixin:
    """Mix in ahead of a ``DurableRefundDestination`` subclass (see the concrete classes below)."""

    delayed_commit_capability = CAPABILITY

    def _delayed_init(self) -> None:
        self._pauses: dict[str, Callable[[], None]] = {}
        self._defer_next = False
        self.in_flight: list[tuple] = []

    def pause_at(self, point: str, callback: Callable[[], None]) -> None:
        if point not in POINTS:
            raise ValueError(f"unknown pause point {point!r}; expected one of {POINTS}")
        self._pauses[point] = callback

    def defer_next_commit(self) -> None:
        self._defer_next = True

    def _fire(self, point: str) -> None:
        callback = self._pauses.pop(point, None)
        if callback is not None:
            callback()

    def observe_bound(self, snapshot):
        self._fire("observe")
        return super().observe_bound(snapshot)

    def commit(self, snapshot, **kwargs):
        self._fire("commit")
        if self._defer_next:
            self._defer_next = False
            self.in_flight.append((snapshot, kwargs))
            raise TimeoutError("synthetic acknowledgement lost; commit has not landed yet")
        return super().commit(snapshot, **kwargs)

    def land(self):
        """Land the oldest deferred commit. Raises ``LookupError`` when nothing is in flight."""
        if not self.in_flight:
            raise LookupError("no commit in flight")
        snapshot, kwargs = self.in_flight.pop(0)
        return super().commit(snapshot, **kwargs)

    def land_all(self) -> list:
        return [self.land() for _ in range(len(self.in_flight))]


class DelayedCommitDestination(DelayedCommitMixin, DurableRefundDestination):
    def __init__(self, root, database_name: str = "refunds.sqlite3"):
        DurableRefundDestination.__init__(self, root, database_name)
        self._delayed_init()


class DelayedOperationIdentityDestination(DelayedCommitMixin, OperationIdentityDestination):
    def __init__(self, root, database_name: str = "refunds.sqlite3"):
        OperationIdentityDestination.__init__(self, root, database_name)
        self._delayed_init()


class DelayedEffectiveGrantDestination(DelayedCommitMixin, EffectiveGrantDestination):
    def __init__(self, root, database_name: str = "refunds.sqlite3"):
        EffectiveGrantDestination.__init__(self, root, database_name)
        self._delayed_init()


class DelayedOperationIdentityEffectiveGrantDestination(DelayedCommitMixin, OperationIdentityEffectiveGrantDestination):
    def __init__(self, root, database_name: str = "refunds.sqlite3"):
        OperationIdentityEffectiveGrantDestination.__init__(self, root, database_name)
        self._delayed_init()


def delayed(operation_identity: bool = False, effective_grant: bool = False):
    """The delayed-commit class for a profile combination (the suites choose by flags)."""
    return {
        (False, False): DelayedCommitDestination,
        (True, False): DelayedOperationIdentityDestination,
        (False, True): DelayedEffectiveGrantDestination,
        (True, True): DelayedOperationIdentityEffectiveGrantDestination,
    }[(operation_identity, effective_grant)]
