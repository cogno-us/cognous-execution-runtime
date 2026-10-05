import os
from .permissions import PermissionSystem
from .action_schema import validate_action
from .audit import AuditLog


class AgentEngine:
    """Legacy compatibility shell.

    This API is not the supported execution path. It performs no side effect and
    can never report successful execution. Use engine.safe_executor.SafeExecutor.
    """

    def __init__(self, sandbox_dir, permissions_file, audit_log_file):
        self.sandbox_dir = sandbox_dir
        self.permissions = PermissionSystem(permissions_file)
        self.audit_log = AuditLog(audit_log_file)
        os.makedirs(sandbox_dir, exist_ok=True)

    def execute_action(self, agent_id, action):
        validate_action(action)
        allowed = self.permissions.is_allowed(agent_id, action["type"])
        self.audit_log.log(agent_id, action, allowed=allowed)
        if not allowed:
            raise PermissionError(f"Action {action['type']} not allowed for agent {agent_id}")
        return {
            "status": "unsupported",
            "executed": False,
            "action": action["type"],
            "error": "legacy AgentEngine has no execution adapter",
        }
