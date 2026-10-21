import json
from engine.permissions import PermissionSystem


def test_explicit_permission_only(tmp_path):
    path = tmp_path / "permissions.json"
    path.write_text(json.dumps({"agent1": ["read"]}), encoding="utf-8")
    permissions = PermissionSystem(path)
    assert permissions.is_allowed("agent1", "read")
    assert not permissions.is_allowed("agent1", "write")
    assert not permissions.is_allowed("unknown", "read")


def test_string_entry_is_not_a_substring_grant(tmp_path):
    """B-F8: a bare string entry grants nothing; only a list/tuple/set of strings is a grant list."""
    path = tmp_path / "permissions.json"
    path.write_text(json.dumps({"agentS": "read-only", "agentL": ["read"]}), encoding="utf-8")
    permissions = PermissionSystem(path)
    for action in ("read", "read-only", "ad", "only", ""):
        assert not permissions.is_allowed("agentS", action)
    assert permissions.is_allowed("agentL", "read")
    assert not permissions.is_allowed("agentL", "rea")


def test_non_string_action_and_non_string_entries_are_not_allowed(tmp_path):
    path = tmp_path / "permissions.json"
    path.write_text(json.dumps({"a": ["read", 1, None], "b": {"read": True}}), encoding="utf-8")
    permissions = PermissionSystem(path)
    assert permissions.is_allowed("a", "read")
    assert not permissions.is_allowed("a", 1)
    assert not permissions.is_allowed("a", None)
    assert not permissions.is_allowed("b", "read")
