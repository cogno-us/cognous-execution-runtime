import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from tools.openappa.local_resume import ResumeRefused, provision, resume

ROOT = 'synthetic-persistent-root'


def enroll(tmp_path, uid=None):
    tmp_path.chmod(0o700)
    store = tmp_path / 'trajectory.sqlite'
    store.write_bytes(b'unit-test-state')
    store.chmod(0o600)
    registry = tmp_path / 'host.sqlite'
    provision(registry, uid=os.getuid() if uid is None else uid, tenant_id='tenant-fixture',
              principal_id='principal-fixture', session_id='session-fixture', root_id=ROOT, store=store)
    return registry, store


def read_binding(registry, reader, session='session-fixture'):
    left, right = socket.socketpair()
    try:
        return resume(left, registry, session, read_existing=reader)
    finally:
        left.close()
        right.close()


def unreachable(binding):
    pytest.fail('refused request reached store reader')


def test_kernel_uid_selects_persisted_identity_after_registry_reopen(tmp_path):
    registry, _ = enroll(tmp_path)
    binding, result = read_binding(registry, lambda binding: 'unit-reader')
    assert (binding.uid, binding.tenant_id, binding.principal_id) == (
        os.getuid(), 'tenant-fixture', 'principal-fixture')
    assert result == 'unit-reader'


@pytest.mark.parametrize('mutation', ['unknown-session', 'other-uid', 'missing-registry',
                                     'missing-store', 'replaced-store', 'symlink-store',
                                     'public-store', 'public-directory', 'corrupt-registry'])
def test_invalid_binding_never_reaches_reader(tmp_path, mutation):
    registry, store = enroll(tmp_path, os.getuid() + 1 if mutation == 'other-uid' else None)
    session = 'other-session' if mutation == 'unknown-session' else 'session-fixture'
    if mutation == 'missing-registry':
        registry.unlink()
    elif mutation == 'missing-store':
        store.unlink()
    elif mutation in ('replaced-store', 'symlink-store'):
        old = tmp_path / 'old.sqlite'
        store.rename(old)
        if mutation == 'symlink-store':
            store.symlink_to(old)
        else:
            store.write_bytes(b'replacement')
            store.chmod(0o600)
    elif mutation == 'public-store':
        store.chmod(0o644)
    elif mutation == 'public-directory':
        tmp_path.chmod(0o755)
    elif mutation == 'corrupt-registry':
        registry.write_bytes(b'invalid sqlite')
    with pytest.raises(ResumeRefused):
        read_binding(registry, unreachable, session)
    if mutation == 'missing-store':
        assert not store.exists()
    if mutation == 'missing-registry':
        assert not registry.exists()


def test_uid_cannot_be_enrolled_under_second_tenant(tmp_path):
    registry, store = enroll(tmp_path)
    with pytest.raises(ResumeRefused, match='one uid'):
        provision(registry, uid=os.getuid(), tenant_id='other-tenant', principal_id='principal-fixture',
                  session_id='other-session', root_id=ROOT, store=store)


def test_existing_session_cannot_be_silently_rebound(tmp_path):
    registry, store = enroll(tmp_path)
    with pytest.raises(ResumeRefused, match='already enrolled'):
        provision(registry, uid=os.getuid(), tenant_id='tenant-fixture', principal_id='principal-fixture',
                  session_id='session-fixture', root_id='other-root', store=store)


def test_store_replacement_during_callback_is_refused(tmp_path):
    registry, store = enroll(tmp_path)
    def replace(binding):
        store.rename(tmp_path / 'old.sqlite')
        store.write_bytes(b'replacement')
        return 'must not be accepted'
    with pytest.raises(ResumeRefused, match='identity changed'):
        read_binding(registry, replace)


@pytest.mark.parametrize('connected', [False, True])
def test_non_unix_or_unconnected_socket_is_refused(tmp_path, connected):
    registry, _ = enroll(tmp_path)
    # No TCP connection is needed: AF_INET is refused before peer lookup.
    sock = socket.socket(socket.AF_INET if connected else socket.AF_UNIX)
    try:
        with pytest.raises(ResumeRefused):
            resume(sock, registry, 'session-fixture', read_existing=unreachable)
    finally:
        sock.close()


def run_probe(mode, store, root=ROOT):
    binary = os.environ.get('COGNOUS_OPENAPPA_RESUME_PROBE')
    assert binary and Path(binary).is_file(), 'actual compiled probe required; no skips'
    return subprocess.run([str(Path(binary).resolve()), mode, str(store.resolve()), root],
                          check=True, capture_output=True, text=True, timeout=15)


def test_actual_runtime_resume_preserves_restriction_for_kernel_peer(tmp_path):
    tmp_path.chmod(0o700)
    store = tmp_path / 'trajectory.sqlite'
    run_probe('bootstrap', store)
    store.chmod(0o600)
    registry = tmp_path / 'host.sqlite'
    provision(registry, uid=os.getuid(), tenant_id='tenant-fixture', principal_id='principal-fixture',
              session_id='session-fixture', root_id=ROOT, store=store)
    def actual_reader(binding):
        return run_probe('resume', Path(binding.store_path), binding.root_id).stdout.strip()
    for _ in range(2):
        binding, result = read_binding(registry, actual_reader)
        assert binding.root_id == ROOT
        assert result == 'existing-restricted-root'


def test_actual_existing_only_open_does_not_create_missing_store(tmp_path):
    missing = tmp_path / 'missing.sqlite'
    with pytest.raises(subprocess.CalledProcessError):
        run_probe('resume', missing)
    assert not missing.exists()


def test_actual_corrupt_store_is_not_reset(tmp_path):
    store = tmp_path / 'corrupt.sqlite'
    store.write_bytes(b'invalid sqlite')
    with pytest.raises(subprocess.CalledProcessError):
        run_probe('resume', store)
    assert store.read_bytes() == b'invalid sqlite'


@pytest.mark.parametrize('same_identity', [False, True])
def test_concurrent_enrollment_preserves_one_identity_per_uid(tmp_path, monkeypatch, same_identity):
    import concurrent.futures
    import sqlite3
    import threading
    import time

    registry, store = enroll(tmp_path, uid=os.getuid() + 1)
    original_connect = sqlite3.connect

    class DelayedReadCursor(sqlite3.Cursor):
        def fetchall(self):
            rows = super().fetchall()
            # Exercise scheduling after actual DB reads, before subsequent writes.
            # The production transaction must protect this interval.
            time.sleep(0.04)
            return rows

    class DelayedReadConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            return self.cursor(factory=DelayedReadCursor).execute(sql, parameters)

    def delayed_connect(*args, **kwargs):
        return original_connect(*args, **kwargs, factory=DelayedReadConnection)

    monkeypatch.setattr(sqlite3, 'connect', delayed_connect)
    start = threading.Barrier(6)

    def worker(index):
        start.wait(timeout=5)
        try:
            provision(registry, uid=os.getuid(), tenant_id='tenant-same' if same_identity else f'tenant-{index}',
                      principal_id='principal-same' if same_identity else f'principal-{index}',
                      session_id=f'session-{index}', root_id=ROOT, store=store)
            return True
        except ResumeRefused:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(worker, range(6)))
    assert sum(results) == (6 if same_identity else 1)
    with original_connect(registry) as db:
        identities = db.execute('SELECT DISTINCT tenant,principal FROM host_binding WHERE uid=?',
                                (os.getuid(),)).fetchall()
    assert len(identities) == 1


def test_inconsistent_persisted_uid_mapping_refuses_resume(tmp_path):
    import sqlite3
    registry, store = enroll(tmp_path)
    with sqlite3.connect(registry) as db:
        info = store.stat()
        # Represents an inconsistent older registry, not a normal enrollment path.
        db.execute('INSERT INTO host_binding VALUES (?,?,?,?,?,?,?,?)',
                   (os.getuid(), 'other-tenant', 'other-principal', 'other-session', ROOT,
                    str(store), info.st_dev, info.st_ino))
    with pytest.raises(ResumeRefused, match='inconsistent'):
        read_binding(registry, unreachable)


@pytest.mark.parametrize('operation', ['provision', 'resume'])
@pytest.mark.parametrize('state_file', ['registry', 'store'])
def test_fifo_state_is_refused_without_waiting(tmp_path, operation, state_file):
    registry, store = enroll(tmp_path)
    target = registry if state_file == 'registry' else store
    target.unlink()
    os.mkfifo(target, 0o600)
    # A subprocess timeout bounds a regression without leaving a blocked thread.
    result = subprocess.run([sys.executable, '-c', """
import os
import sys
from tools.openappa.test_local_resume import ROOT, read_binding, unreachable
from tools.openappa.local_resume import ResumeRefused, provision
registry, store, operation = sys.argv[1:]
try:
    if operation == 'resume':
        read_binding(registry, unreachable)
    else:
        provision(registry, uid=os.getuid(), tenant_id='tenant-fixture',
                  principal_id='principal-fixture', session_id='new-session',
                  root_id=ROOT, store=store)
except ResumeRefused as error:
    assert 'state file must be private and host-owned' in str(error)
else:
    raise AssertionError('FIFO state was accepted')
""", str(registry), str(store), operation], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
