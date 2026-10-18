import json
import time

_MAX_FALLBACK_TEXT = 200


def _safe_text(value):
    """Bounded text for a value that could not be serialised; never raises."""
    try:
        text = repr(value)
    except Exception:
        text = "<unprintable " + type(value).__name__ + ">"
    return text[:_MAX_FALLBACK_TEXT]


class AuditLog:
    """Append-only legacy audit log.

    The ``agent_id`` is whatever the caller passed; it is recorded, not authenticated.
    """

    def __init__(self, log_file):
        self.log_file = log_file

    def log(self, agent_id, action, allowed, rejected=False):
        entry = {
            'timestamp': time.time(),
            'agent_id': agent_id,
            'action': action,
            'allowed': allowed
        }
        if rejected:
            entry['rejected'] = True
        # default=repr: a non-serializable payload must never suppress the entry.
        try:
            line = json.dumps(entry, default=repr)
        except (ValueError, TypeError, RecursionError, OverflowError):
            # A cyclic, over-deep or otherwise unserialisable payload still leaves a minimal record.
            line = json.dumps({
                'timestamp': entry['timestamp'],
                'agent_id': _safe_text(agent_id),
                'action': _safe_text(action),
                'allowed': allowed,
                'rejected': bool(rejected),
                'unserialisable': True,
            })
        with open(self.log_file, 'a') as f:
            f.write(line + '\n')
