"""Opt-in Linux local-host resume binding; grants no institutional authority.

Trust boundary: the host process and private directory are trusted. Kernel UIDs
map to exactly one tenant/principal through explicit operator provisioning.
Same-UID hostile processes, database rollback and distributed hosts are excluded.
"""
from contextlib import closing
from dataclasses import dataclass
import os
from pathlib import Path
import socket
import sqlite3
import stat
import struct


class ResumeRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class Binding:
    uid: int
    tenant_id: str
    principal_id: str
    session_id: str
    root_id: str
    store_path: str
    device: int
    inode: int


def _text(value):
    if not isinstance(value, str) or not value or value != value.strip() or any(ord(c) < 32 for c in value):
        raise ResumeRefused('invalid binding identifier')
    return value


def _private_directory(path):
    path = Path(path).absolute()
    if path.resolve(strict=True) != path:
        raise ResumeRefused('symlinked directory')
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ResumeRefused('host directory must be private and host-owned')
    return path


def _open_owned(path):
    path = Path(path).absolute()
    _private_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        os.close(fd)
        raise ResumeRefused('state file must be private and host-owned')
    return fd, info


def provision(registry, *, uid, tenant_id, principal_id, session_id, root_id, store):
    """Explicit host-operator enrollment, never called by resume.

    Does not create an OpenAPPA store/root or authenticate an end user. A UID
    cannot be mapped to multiple tenant/principal identities in this registry.
    """
    if type(uid) is not int or uid < 0:
        raise ResumeRefused('invalid uid')
    values = tuple(_text(v) for v in (tenant_id, principal_id, session_id, root_id))
    registry = Path(registry).absolute()
    _private_directory(registry.parent)
    store = Path(store).absolute()
    if store == registry:
        raise ResumeRefused('registry cannot be the trajectory store')
    fd, info = _open_owned(store)
    os.close(fd)
    # Exclusive creation sets permissions atomically; never follow a symlink.
    try:
        fd = os.open(registry, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC, 0o600)
        os.close(fd)
    except FileExistsError:
        fd, _ = _open_owned(registry)
        os.close(fd)
    with closing(sqlite3.connect(registry.as_uri() + '?mode=rw', uri=True)) as db, db:
        # Reserve the writer before reading identity; SELECT alone does not start
        # sqlite3's implicit transaction. Concurrent enrollments must serialize.
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS host_binding (uid INTEGER, tenant TEXT, principal TEXT, session TEXT, root TEXT, path TEXT, device INTEGER, inode INTEGER, PRIMARY KEY(uid, session))')
        prior = db.execute('SELECT tenant, principal FROM host_binding WHERE uid=?', (uid,)).fetchall()
        if any(row != values[:2] for row in prior):
            raise ResumeRefused('one uid must map to one tenant/principal')
        try:
            db.execute('INSERT INTO host_binding VALUES (?,?,?,?,?,?,?,?)',
                       (uid, *values, str(store), info.st_dev, info.st_ino))
        except sqlite3.IntegrityError as error:
            raise ResumeRefused('session is already enrolled; no implicit rebind') from error


def resume(peer, registry, session_id, *, read_existing):
    """Read one pre-enrolled trajectory for a connected kernel-identified peer.

    read_existing is trusted host code: it must open SQLite with mode=rw (no
    CREATE), call actual try_status for the supplied root, and propagate errors.
    This function does not dispatch tools or issue flow/institutional permits.
    """
    fds = []
    try:
        _text(session_id)
        if peer.family != socket.AF_UNIX:
            raise ResumeRefused('local Unix peer required')
        peer.getpeername()  # Reject listeners/unconnected sockets.
        _, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        registry = Path(registry).absolute()
        fd, registry_info = _open_owned(registry)
        fds.append(fd)
        with closing(sqlite3.connect(registry.as_uri() + '?mode=ro', uri=True)) as db:
            # One read transaction observes the identity set and session together.
            db.execute('BEGIN')
            identities = db.execute('SELECT DISTINCT tenant,principal FROM host_binding WHERE uid=?', (uid,)).fetchall()
            if len(identities) != 1:
                raise ResumeRefused('uid mapping is absent or inconsistent')
            rows = db.execute('SELECT uid,tenant,principal,session,root,path,device,inode FROM host_binding WHERE uid=? AND session=?', (uid, session_id)).fetchall()
        if len(rows) != 1:
            raise ResumeRefused('no enrolled session for authenticated uid')
        binding = Binding(*rows[0])
        for value in (binding.tenant_id, binding.principal_id, binding.session_id, binding.root_id):
            _text(value)
        fd, info = _open_owned(binding.store_path)
        fds.append(fd)
        if (info.st_dev, info.st_ino) != (binding.device, binding.inode):
            raise ResumeRefused('trajectory store was replaced')
        result = read_existing(binding)
        # Detect accidental replacement during the callback. This is not an
        # atomic defense against a hostile host or another same-UID writer.
        for path, expected in ((registry, registry_info), (Path(binding.store_path), info)):
            observed = path.stat(follow_symlinks=False)
            if (observed.st_dev, observed.st_ino) != (expected.st_dev, expected.st_ino):
                raise ResumeRefused('state identity changed during resume')
        return binding, result
    except ResumeRefused:
        raise
    except (OSError, ValueError, sqlite3.Error) as error:
        raise ResumeRefused('peer or persisted state unavailable') from error
    finally:
        for fd in fds:
            os.close(fd)
