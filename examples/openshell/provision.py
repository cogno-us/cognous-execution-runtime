# SPDX-License-Identifier: Apache-2.0
"""Provision one synthetic sandbox on an existing, dedicated local gateway.

No gateway installation, host security changes, mounts, providers or credentials.
Run from repository root: python examples/openshell/provision.py --help.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from engine.openshell_environment import OpenShellCLI, OpenShellConfig, POLICY

p = argparse.ArgumentParser()
p.add_argument('--binary', required=True)
p.add_argument('--home', required=True, help='dedicated synthetic gateway client HOME')
p.add_argument('--gateway', default='local')
p.add_argument('--workspace', default='default')
p.add_argument('--name', required=True, help='new dedicated sandbox; never reuse a lost destination')
p.add_argument('--image', required=True, help='reviewed worker image at immutable OCI digest')
p.add_argument('--output', required=True, help='new config JSON; refused if it exists')
args = p.parse_args()
for name in (args.gateway, args.workspace, args.name):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}', name):
        p.error('invalid selector')
if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', args.image):
    p.error('immutable OCI digest required')
if Path(args.output).exists():
    p.error('output exists; automatic replacement is forbidden')
cli = OpenShellCLI(args.binary, args.home)
seed = SimpleNamespace(gateway=args.gateway, workspace=args.workspace,
    sandbox=args.name, timeout_seconds=30,
    cli_sha256=hashlib.sha256(Path(cli.binary).read_bytes()).hexdigest())
# Inspect validates the gateway before sandbox access; the get below does not
# exist yet, so check the gateway explicitly rather than ignoring an exception.
runtime = json.loads(cli.run(seed, ['gateway', 'info', '--output', 'json']))
from urllib.parse import urlparse
if (runtime.get('version') != '0.1.2' or runtime.get('status') != 'healthy'
    or urlparse(runtime.get('server', '')).hostname not in {'localhost','127.0.0.1','::1'}
    or [x.get('capabilities',{}).get('driver_name') for x in runtime.get('compute_drivers',[])] != ['docker']):
    p.error('requires healthy OpenShell 0.1.2 local Docker gateway')
with tempfile.TemporaryDirectory() as directory:
    policy = Path(directory)/'policy.json'
    policy.write_text(json.dumps(POLICY))
    create = ['sandbox','create','--name',args.name,'--from',args.image,
              '--policy',str(policy),'--cpu','1','--memory','256Mi','--detach',
              '--env','BASH_ENV=/dev/null','--output','json','--','/bin/sleep','infinity']
    # Failure/timeout requires inspection. Never retry creation automatically.
    creation_output = cli.run(seed, create)
info = cli.inspect(seed)
admission = info['configuration_admission']
config = OpenShellConfig(gateway=args.gateway, workspace=args.workspace, sandbox=args.name,
    sandbox_id=info['id'], image=args.image, cli_sha256=seed.cli_sha256,
    policy_json=json.dumps(info['policy'], sort_keys=True),
    policy_version=info['current_policy_version'], policy_hash=admission['policy_hash'],
    config_revision=admission['config_revision'], provider_env_revision=admission['provider_env_revision'])
with Path(args.output).open('x') as f:
    json.dump(asdict(config), f, indent=2)
with Path(args.output + '.creation.json').open('x') as f:
    json.dump({'creation_argv':create,'creation_output':creation_output,
               'inspection':info,'runtime':runtime,'config_digest':config.digest}, f, indent=2)
print(args.output)
