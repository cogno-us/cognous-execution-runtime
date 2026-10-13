"""Actual Linux peer binding and pinned reply policy; synthetic destination only."""
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import subprocess

import pytest

from tools.openappa.local_reply_harness import ReplyRoute, deliver_local_reply
from tools.openappa.local_resume import provision


def probe(store, mode, raw=b''):
    binary = os.environ['COGNOUS_OPENAPPA_REPLY_PROBE']
    assert Path(binary).is_file()
    result = subprocess.run([binary, mode, str(store)], input=raw,
                            capture_output=True, check=True, timeout=15)
    return json.loads(result.stdout) if mode in ('check', 'audit') else None


@pytest.mark.parametrize('case,expected', [
    ('allow', 'delivered'), ('restricted', 'flow-refused'),
    ('tenant', 'host-refused'), ('principal', 'host-refused'), ('root', 'host-refused'),
    ('unknown-session', 'host-refused'), ('other-uid', 'host-refused'),
    ('missing-store', 'host-refused'), ('oversize', 'output-refused'),
    ('wrong-digest', 'flow-refused'), ('checker-unavailable', 'flow-unavailable'),
    ('receipt-lost', 'delivery-unknown'),
])
def test_kernel_selected_reply_route(tmp_path, case, expected):
    tmp_path.chmod(0o700)
    store = tmp_path / 'trajectory.sqlite'
    probe(store, 'bootstrap')
    store.chmod(0o600)
    registry = tmp_path / 'host.sqlite'
    route = ReplyRoute('synthetic-tenant', 'synthetic-principal',
                       'synthetic-persistent-root', 'synthetic-public')
    provision(registry, uid=os.getuid() + int(case == 'other-uid'),
              tenant_id=route.tenant_id, principal_id=route.principal_id,
              session_id='synthetic-session', root_id=route.root_id, store=store)
    if case in ('tenant', 'principal', 'root'):
        route = replace(route, **{case + '_id': 'synthetic-other'})
    if case == 'restricted':
        probe(store, 'restrict')
    if case == 'missing-store':
        store.unlink()
    checked, delivered = [], []
    def read_existing(binding):
        # Audit reopens the already-enrolled store; this fixture supports one root.
        assert binding.root_id == 'synthetic-persistent-root'
        return probe(binding.store_path, 'audit')
    def check(binding, raw):
        checked.append((binding, json.loads(raw)))
        if case == 'checker-unavailable':
            raise OSError('synthetic policy unavailable')
        response = probe(binding.store_path, 'check', raw)
        if case == 'wrong-digest':
            response['requestSha256'] = '0' * 64
        return response
    def deliver(binding, destination, text):
        delivered.append((binding, destination, text))
        if case == 'receipt-lost':
            raise OSError('synthetic receipt lost after send')
    left, right = socket.socketpair()
    try:
        outcome = deliver_local_reply(peer=left, registry=registry,
            session_id='unknown' if case == 'unknown-session' else 'synthetic-session',
            route=route, text='x' * 16385 if case == 'oversize' else 'synthetic final text',
            read_existing=read_existing, flow_check=check, deliver=deliver)
    finally:
        left.close()
        right.close()
    assert outcome == expected
    assert len(checked) == int(expected not in ('host-refused', 'output-refused'))
    assert len(delivered) == int(case in ('allow', 'receipt-lost'))
    if delivered:
        binding, destination, text = delivered[0]
        assert binding == checked[0][0]
        assert binding.uid == os.getuid()
        assert (binding.tenant_id, binding.principal_id, binding.root_id) == (
            route.tenant_id, route.principal_id, route.root_id)
        assert checked[0][1]['destinationId'] == destination == 'synthetic-public'
        assert checked[0][1]['text'] == text == 'synthetic final text'
    if case == 'missing-store':
        assert not store.exists()
