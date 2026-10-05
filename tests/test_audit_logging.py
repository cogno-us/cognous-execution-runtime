import json
import pytest
from engine.engine import AgentEngine


def make_engine(tmp_path):
    permissions = tmp_path / "permissions.json"
    permissions.write_text(json.dumps({"agent1": ["write"]}), encoding="utf-8")
    return (
        AgentEngine(tmp_path / "sandbox", permissions, tmp_path / "audit.log"),
        tmp_path / "audit.log",
    )


def test_allowed_legacy_attempt_is_logged_but_not_executed(tmp_path):
    engine, log = make_engine(tmp_path)
    result = engine.execute_action(
        "agent1", {"type": "write", "target": "x", "params": {"v": 1}}
    )
    assert result["executed"] is False
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["allowed"] is True
    assert entry["action"]["params"] == {"v": 1}


def test_denied_legacy_attempt_is_logged(tmp_path):
    engine, log = make_engine(tmp_path)
    with pytest.raises(PermissionError):
        engine.execute_action(
            "agent1", {"type": "delete", "target": "x", "params": {}}
        )
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["allowed"] is False
