import json
import pytest
from engine.engine import AgentEngine


def make_engine(tmp_path):
    permissions = tmp_path / "permissions.json"
    permissions.write_text(json.dumps({"agent1": ["write"]}), encoding="utf-8")
    return AgentEngine(tmp_path / "sandbox", permissions, tmp_path / "audit.log")


def test_legacy_allowed_path_cannot_report_success(tmp_path):
    result = make_engine(tmp_path).execute_action(
        "agent1", {"type": "write", "target": "x", "params": {}}
    )
    assert result == {
        "status": "unsupported",
        "executed": False,
        "action": "write",
        "error": "legacy AgentEngine has no execution adapter",
    }


def test_legacy_denied_path_raises(tmp_path):
    with pytest.raises(PermissionError):
        make_engine(tmp_path).execute_action(
            "agent1", {"type": "delete", "target": "x", "params": {}}
        )
