# SPDX-License-Identifier: Apache-2.0
"""Live access probes. A timeout/DNS failure alone is not network-denial proof."""
import json
import socket
from pathlib import Path

result = {}
p = Path('/var/lib/cognous/permitted-probe')
p.write_text('synthetic')
result['permitted_write'] = p.read_text() == 'synthetic'
p.unlink()
try:
    Path('/tmp/prohibited-probe').write_text('synthetic')
    result['prohibited_write_denied'] = False
except PermissionError:
    result['prohibited_write_denied'] = True
try:
    # IP avoids confusing a DNS failure with egress enforcement.
    with socket.create_connection(('1.1.1.1', 443), timeout=3):
        result['network'] = 'unexpected_connection'
except OSError as exc:
    result['network'] = {'errno': exc.errno, 'type': type(exc).__name__}
print(json.dumps(result))
assert result['permitted_write'] and result['prohibited_write_denied']
assert result['network'] != 'unexpected_connection'
