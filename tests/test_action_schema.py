import pytest
from engine.action_schema import validate_action


def test_valid_action():
    assert validate_action({"type": "echo", "target": "", "params": {}})


def test_missing_type():
    with pytest.raises(ValueError):
        validate_action({"target": "", "params": {}})


def test_ambiguous_action():
    with pytest.raises(ValueError):
        validate_action({"type": "echo", "target": "", "params": {}, "extra": 123})


def test_wrong_field_types():
    with pytest.raises(ValueError):
        validate_action({"type": "echo", "target": 3, "params": {}})
    with pytest.raises(ValueError):
        validate_action({"type": "echo", "target": "", "params": []})
