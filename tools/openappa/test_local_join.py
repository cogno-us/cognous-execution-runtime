"""Kernel peer binding through actual persistent flow and native destination."""
import json
import os
import socket

import pytest

from tools.openappa.local_join_harness import execute_local_joined
from tools.openappa.local_resume import provision
from tools.openappa.test_persistent_join import prepare, run_probe, assert_destination


def enrolled(tmp_path, *, other_uid=False):
    h, resolver, decision, destination, arguments, path = prepare(tmp_path)
    tmp_path.chmod(0o700)
    path.chmod(0o600)
    registry = tmp_path / 'host.sqlite'
    host = arguments['host']
    provision(registry, uid=os.getuid() + int(other_uid), tenant_id=host['tenant_id'],
              principal_id=host['principal_id'], session_id='fixture-session',
              root_id=host['root_id'], store=path)
    arguments.update(registry=registry, session_id='fixture-session')
    return resolver, decision, destination, arguments, path


def execute(arguments, *, status_hook=None):
    seen = []
    def status(binding):
        run_probe(binding.store_path, 'status', json.dumps({'root_id': binding.root_id}).encode())
        seen.append(('status', binding))
        if status_hook:
            status_hook()
    def flow(binding, raw):
        seen.append(('flow', binding))
        assert binding == seen[0][1]
        context = json.loads(raw)['context']
        assert (context['tenant_id'], context['principal_id'], context['root_id']) == (
            binding.tenant_id, binding.principal_id, binding.root_id)
        return run_probe(binding.store_path, 'check', raw)
    left, right = socket.socketpair()
    try:
        result = execute_local_joined(peer=left, **arguments,
                                      read_existing=status, flow_check=flow)
        return result, seen
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize('restricted,revoked,expected', [
    (False, False, 'executed'), (True, False, 'flow-refused'),
    (False, True, 'denied'), (True, True, 'flow-refused'),
])
def test_authenticated_persistent_conjunction(tmp_path, restricted, revoked, expected):
    resolver, decision, destination, arguments, path = enrolled(tmp_path)
    if restricted:
        run_probe(path, 'restrict')
    if revoked:
        resolver.statuses[decision.binding.grant_id].status = 'revoked'
    result, seen = execute(arguments)
    assert result == expected
    assert [stage for stage, _ in seen] == ['status', 'flow']
    assert seen[0][1].uid == os.getuid()
    assert_destination(destination, decision, expected == 'executed')


@pytest.mark.parametrize('field', ['tenant_id', 'principal_id', 'root_id'])
def test_unmatched_host_identity_never_reaches_flow(tmp_path, field):
    _, decision, destination, arguments, _ = enrolled(tmp_path)
    arguments['host'][field] = 'synthetic-other'
    result, seen = execute(arguments)
    assert result == 'host-refused'
    assert [stage for stage, _ in seen] == ['status']
    assert_destination(destination, decision)


@pytest.mark.parametrize('mutation', ['other-uid', 'unknown-session', 'missing-registry',
                                     'missing-store', 'corrupt-store'])
def test_unavailable_host_state_never_reaches_flow(tmp_path, mutation):
    _, decision, destination, arguments, path = enrolled(tmp_path, other_uid=mutation == 'other-uid')
    if mutation == 'unknown-session':
        arguments['session_id'] = 'synthetic-other'
    elif mutation == 'missing-registry':
        arguments['registry'].unlink()
    elif mutation == 'missing-store':
        path.unlink()
    elif mutation == 'corrupt-store':
        path.write_bytes(b'synthetic-invalid-database')
    result, seen = execute(arguments)
    assert result == ('host-unavailable' if mutation == 'corrupt-store' else 'host-refused')
    assert seen == []
    assert_destination(destination, decision)
    if mutation == 'missing-store':
        assert not path.exists()


def test_authority_revoked_during_status_read_is_not_restored(tmp_path):
    resolver, decision, destination, arguments, _ = enrolled(tmp_path)
    def revoke():
        resolver.statuses[decision.binding.grant_id].status = 'revoked'
    result, seen = execute(arguments, status_hook=revoke)
    assert result == 'denied'
    assert [stage for stage, _ in seen] == ['status', 'flow']
    assert_destination(destination, decision)
