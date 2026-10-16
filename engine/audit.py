import json
import time

class AuditLog:
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
        line = json.dumps(entry, default=repr)
        with open(self.log_file, 'a') as f:
            f.write(line + '\n')
