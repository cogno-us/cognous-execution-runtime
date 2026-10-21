import json

class PermissionSystem:
    def __init__(self, permissions_file):
        self.permissions_file = permissions_file
        self._load()

    def _load(self):
        try:
            with open(self.permissions_file, 'r') as f:
                self.permissions = json.load(f)
        except FileNotFoundError:
            self.permissions = {}

    def is_allowed(self, agent_id, action_type):
        # Explicit grants only: the entry must be a list, tuple or set, and the action must be one of its
        # string members exactly. A bare string entry grants nothing (no substring matching).
        if not isinstance(action_type, str):
            return False
        allowed = self.permissions.get(agent_id) if isinstance(self.permissions, dict) else None
        if not isinstance(allowed, (list, tuple, set)):
            return False
        return any(isinstance(entry, str) and entry == action_type for entry in allowed)

