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


@pytest.mark.parametrize(
    "action",
    [
        {"type": "write", "target": "x", "params": {"v": 1}, "extra": True},
        {"type": "", "target": "x"},
        {"type": "write", "target": 7},
        {"type": "write", "params": {"v": object()}, "extra": object()},
        "not-a-dict",
    ],
)
def test_malformed_legacy_attempt_is_logged_as_rejected(tmp_path, action):
    engine, log = make_engine(tmp_path)
    with pytest.raises(ValueError):
        engine.execute_action("agent1", action)
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["allowed"] is False
    assert entry["rejected"] is True
    assert entry["agent_id"] == "agent1"


def test_non_serializable_params_still_produce_audit_entry(tmp_path):
    engine, log = make_engine(tmp_path)
    with pytest.raises(PermissionError):
        engine.execute_action(
            "agent1", {"type": "delete", "target": "x", "params": {"v": object()}}
        )
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["allowed"] is False
    assert "object" in entry["action"]["params"]["v"]


def test_cyclic_params_still_produce_audit_entry(tmp_path):
    engine, log = make_engine(tmp_path)
    params = {}
    params["self"] = params
    with pytest.raises(PermissionError):
        engine.execute_action("agent1", {"type": "delete", "target": "x", "params": params})
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["allowed"] is False
    assert entry["unserialisable"] is True
    assert entry["agent_id"] == "'agent1'"


def test_unhashable_agent_identity_is_refused_and_logged(tmp_path):
    engine, log = make_engine(tmp_path)
    with pytest.raises(PermissionError):
        engine.execute_action(["agent1"], {"type": "write", "target": "x", "params": {}})
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["allowed"] is False
