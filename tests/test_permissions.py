import json
from engine.permissions import PermissionSystem


def test_explicit_permission_only(tmp_path):
    path = tmp_path / "permissions.json"
    path.write_text(json.dumps({"agent1": ["read"]}), encoding="utf-8")
    permissions = PermissionSystem(path)
    assert permissions.is_allowed("agent1", "read")
    assert not permissions.is_allowed("agent1", "write")
    assert not permissions.is_allowed("unknown", "read")
