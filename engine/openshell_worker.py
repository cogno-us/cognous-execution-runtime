# SPDX-License-Identifier: Apache-2.0
"""Fixed synthetic worker baked into the reviewed image; no authority issuer."""
import json
import sys

# Image-owned path, never taken from operation data.
sys.path.insert(0, "/opt/cognous")
from engine.safe_executor import DurableRefundDestination, FrozenEnvelope, FrozenOperation, validate_snapshot


def handle(request, root="/var/lib/cognous"):
    if set(request) != {"version", "mode", "snapshot"} or request["version"] != "0.1.0":
        raise ValueError("unsupported worker request")
    value = dict(request["snapshot"])
    op = dict(value.pop("operation"))
    op["requested_permissions"] = tuple(op["requested_permissions"])
    snapshot = FrozenEnvelope(operation=FrozenOperation(**op), **value)
    validate_snapshot(snapshot)
    destination = DurableRefundDestination(root)
    if request["mode"] == "observe":
        return destination.observe_bound(snapshot)
    if request["mode"] == "commit":
        return destination.commit(snapshot)
    raise ValueError("unsupported operation")


if __name__ == "__main__":
    raw = sys.stdin.buffer.read(65537)
    if len(raw) > 65536:
        raise ValueError("input too large")
    print(json.dumps(handle(json.loads(raw)), allow_nan=False))
