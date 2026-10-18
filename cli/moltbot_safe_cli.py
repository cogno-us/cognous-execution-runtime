# Legacy script, not supported: it imports `Permissions` from `engine.permissions` (the module defines `PermissionSystem`),
# so importing it raises ImportError, and it calls `AgentEngine(perms)` and `engine.execute`, which do not exist.
import argparse
import json
import sys
from engine.engine import AgentEngine
from engine.permissions import Permissions
from engine.action_schema import validate_action


def validate_permissions():
    try:
        with open('engine/permissions.json') as f:
            Permissions(json.load(f))
        print("permissions.json is valid.")
    except Exception as e:
        print(f"Invalid permissions.json: {e}")
        sys.exit(1)

def run_action(action_path=None):
    with open('engine/permissions.json') as f:
        perms = Permissions(json.load(f))
    engine = AgentEngine(perms)
    if action_path:
        with open(action_path) as f:
            action = json.load(f)
    else:
        action = json.load(sys.stdin)
    validate_action(action)
    result = engine.execute(action)
    print(json.dumps(result, indent=2))

def print_audit_log(n=10):
    with open('engine/audit.log') as f:
        lines = f.readlines()
    for line in lines[-n:]:
        print(line.strip())

def main():
    parser = argparse.ArgumentParser(description="moltbot-safe CLI")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("validate-permissions", help="Validate permissions.json")

    run_parser = subparsers.add_parser("run-action", help="Run an action from JSON file or stdin")
    run_parser.add_argument("--file", type=str, help="Path to action JSON file")

    log_parser = subparsers.add_parser("print-audit-log", help="Print recent audit log entries")
    log_parser.add_argument("-n", type=int, default=10, help="Number of entries to print")

    args = parser.parse_args()
    if args.command == "validate-permissions":
        validate_permissions()
    elif args.command == "run-action":
        run_action(args.file)
    elif args.command == "print-audit-log":
        print_audit_log(args.n)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
