import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "os_isolation: real OS-identity test; needs root and useradd/setpriv, otherwise SKIPPED (INCONCLUSIVE, never PASS)")


@pytest.fixture
def fixed_clock():
    from datetime import datetime, timezone
    state = {"n": 0}

    def clock():
        state["n"] += 1
        return datetime(2026, 10, 27, 12, 0, state["n"] % 60, tzinfo=timezone.utc)
    return clock
