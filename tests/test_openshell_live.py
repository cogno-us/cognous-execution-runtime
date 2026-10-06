"""Opt-in live qualification: never run against a shared/production gateway."""
import errno
import json
import os
from pathlib import Path

import pytest

from engine.openshell_environment import OpenShellCLI, OpenShellConfig, OpenShellRefundDestination
from engine.control_plane_adapter import PinnedControlPlaneExecutor
from engine.safe_executor import snapshot_envelope
from test_safe_executor import _integrated, policy

pytestmark = pytest.mark.skipif(not os.environ.get('MOLTBOT_SAFE_OPENSHELL_CONFIG'),
                               reason='live OpenShell gateway and qualified worker image not configured')


def live():
    value = json.loads(Path(os.environ['MOLTBOT_SAFE_OPENSHELL_CONFIG']).read_text())
    value['executable'] = tuple(value['executable'])
    c = OpenShellConfig(**value)
    return c, OpenShellCLI(os.environ['MOLTBOT_SAFE_OPENSHELL_BINARY'],
                          os.environ['MOLTBOT_SAFE_OPENSHELL_HOME'])


def test_live_authorized_effect_and_restart_observation(tmp_path):
    h,p,resolver,workflow,decision,_,_,req = _integrated(tmp_path)
    c, cli = live()
    destination = OpenShellRefundDestination(tmp_path/'journal', c, cli)
    snap = snapshot_envelope(req)
    destination.bind(snap)
    executor = PinnedControlPlaneExecutor(workflow=workflow, destination=destination, policy=policy(req.operation))
    first = executor.execute(envelope=req, proposal=p, decision=decision, now=h.NOW)
    assert first.status == 'executed' and first.newly_executed
    assert first.observed_state == 'applied'
    assert first.observation['destination_state']['payload'] == req.operation.payload
    assert first.observation['destination_state']['amount'] == req.operation.amount
    restarted = OpenShellRefundDestination(tmp_path/'journal', c, cli)
    executor = PinnedControlPlaneExecutor(workflow=workflow, destination=restarted, policy=policy(req.operation))
    second = executor.execute(envelope=req, proposal=p, decision=decision, now=h.NOW)
    assert second.status == 'reconciled' and not second.newly_executed
    print(json.dumps({'first':first.__dict__,'second':second.__dict__}, sort_keys=True))


def test_live_filesystem_and_network_denial():
    c, cli = live()
    cli.inspect(c)
    result = json.loads(cli.run(c, ['sandbox','exec','--name',c.sandbox,
        '--no-tty','--no-login-shell','--timeout','10','--env','BASH_ENV=/dev/null',
        '--workdir',c.workdir,'--','/usr/bin/env','-i','PATH=/usr/local/bin:/usr/bin:/bin',
        '/usr/local/bin/python3','-I','/opt/cognous/probe.py']))
    print(json.dumps(result, sort_keys=True))
    assert result['permitted_write'] and result['prohibited_write_denied']
    # A timeout or general connection failure is NOT accepted as enforcement.
    assert isinstance(result['network'], dict)
    assert result['network']['errno'] in {errno.EACCES, errno.EPERM}
