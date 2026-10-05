_ALLOWED_KEYS = {"type", "target", "params"}


def validate_action(action):
    if not isinstance(action, dict):
        raise ValueError("Action must be a dict")
    if set(action) - _ALLOWED_KEYS:
        raise ValueError("Action contains unsupported fields")
    if not isinstance(action.get("type"), str) or not action["type"]:
        raise ValueError("Action must have a non-empty type")
    if "target" in action and not isinstance(action["target"], str):
        raise ValueError("Action target must be a string")
    if "params" in action and not isinstance(action["params"], dict):
        raise ValueError("Action params must be a dict")
    return True
