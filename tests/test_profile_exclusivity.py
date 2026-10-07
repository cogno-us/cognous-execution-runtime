"""Profile activation must preserve the existing database ownership contract."""
import sqlite3

import pytest

try:
    import agent_control_plane.local_authority_effect  # noqa: F401
except ModuleNotFoundError:
    pytestmark = pytest.mark.skip(reason="requires proposed Control Plane authority-effect contract")

from engine.local_authority_effect import AtomicAuthorityEffectDestination
from engine.refund_intent import RefundIntentRegistry
from engine.safe_executor import DurableRefundDestination
from test_local_authority_effect import BASE, envelope, make_claim
from test_refund_intent import fixture


def retained_rows(path):
    with sqlite3.connect(path) as conn:
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        return {name: conn.execute('SELECT * FROM "' + name + '"').fetchall()
                for name in tables}


@pytest.mark.parametrize("with_claim", [False, True])
def test_intent_database_rejects_atomic_activation_without_changes(tmp_path, with_claim):
    destination, registry, intent, snapshot = fixture(tmp_path)
    if with_claim:
        registry.provision(intent, snapshot)
        registry.reserve(intent, snapshot)
        assert registry.mark_dispatch_started(intent, snapshot)
    before = retained_rows(destination.path)
    with pytest.raises(PermissionError, match="cannot share an intent-profile database"):
        AtomicAuthorityEffectDestination(tmp_path, clock=lambda: BASE)
    assert retained_rows(destination.path) == before
    # The original profile can still reopen its durable state.
    RefundIntentRegistry(destination)
    assert retained_rows(destination.path) == before


@pytest.mark.parametrize("with_claim", [False, True])
def test_atomic_database_rejects_intent_activation_without_changes(tmp_path, with_claim):
    destination = AtomicAuthorityEffectDestination(tmp_path, clock=lambda: BASE)
    if with_claim:
        destination.provision_claim(make_claim(envelope()))
    before = retained_rows(destination.path)
    with pytest.raises(PermissionError, match="cannot share an authority-effect database"):
        RefundIntentRegistry(DurableRefundDestination(tmp_path))
    assert retained_rows(destination.path) == before
    AtomicAuthorityEffectDestination(tmp_path, clock=lambda: BASE)
    assert retained_rows(destination.path) == before
