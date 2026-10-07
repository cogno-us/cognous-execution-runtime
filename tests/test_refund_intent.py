from dataclasses import replace
import multiprocessing
import os
import sqlite3

import pytest

from engine.refund_intent import RefundIntent, RefundIntentRegistry
from engine.safe_executor import DurableRefundDestination, snapshot_envelope
from test_safe_executor import envelope, make_operation


def fixture(root):
    destination = DurableRefundDestination(root)
    registry = RefundIntentRegistry(destination)
    intent = RefundIntent('urn:cognous:institution:demo', 'customer-refunds', 'c1', 'r1')
    snapshot = snapshot_envelope(envelope(make_operation(
        payload={'customer_id': 'c1', 'refund_reason': 'duplicate'}, effective_max_effects=10)))
    return destination, registry, intent, snapshot


def rows(destination):
    with sqlite3.connect(destination.path.as_uri() + '?mode=ro', uri=True) as conn:
        return conn.execute('SELECT effect_id, dispatch_started FROM refund_intent_claims_v1').fetchall()


def test_registered_claim_and_distinct_intent(tmp_path):
    d, r, i, s = fixture(tmp_path)
    r.provision(i, s)
    r.provision(i, s)
    assert r.reserve(i, s).newly_reserved
    other = replace(i, request_id='r2')
    second = replace(s, effect_id='e2')
    r.provision(other, second)
    assert r.reserve(other, second).newly_reserved
    assert sorted(rows(d)) == [('e2', 0), ('effect-1', 0)]
    assert d.effect_count(s.operation.grant_id) == 0  # registry never executes


def test_replan_links_original_and_never_transfers_dispatch(tmp_path):
    d, r, i, s = fixture(tmp_path)
    r.provision(i, s)
    r.reserve(i, s)
    replan = replace(s, effect_id='new-effect', decision_id='fresh-decision',
                     operation=replace(s.operation, grant_id='fresh-grant', proposal_commitment='new'))
    result = r.reserve(i, replan)
    assert result.original_effect_id == s.effect_id and not result.newly_reserved
    with pytest.raises(PermissionError):
        r.mark_dispatch_started(i, replan)
    assert r.mark_dispatch_started(i, s)
    assert not r.mark_dispatch_started(i, s)
    assert rows(d) == [('effect-1', 1)]


@pytest.mark.parametrize('change', [dict(amount=70), dict(unit='EUR'), dict(target='another'),
                                  dict(adapter_id='another'), dict(effects=2),
                                  dict(payload_json='{"customer_id":"c1","refund_reason":"changed"}')])
def test_changed_operation_rejected(tmp_path, change):
    d, r, i, s = fixture(tmp_path)
    r.provision(i, s)
    changed = replace(s, operation=replace(s.operation, **change))
    for operation in (r.provision, r.reserve):
        with pytest.raises(PermissionError):
            operation(i, changed)
    assert rows(d) == []


@pytest.mark.parametrize('change', [dict(institution_id='other'), dict(authority_domain='other'),
                                  dict(customer_id='other'), dict(request_id='forged')])
def test_untrusted_identity_rejected(tmp_path, change):
    d, r, i, s = fixture(tmp_path)
    r.provision(i, s)
    with pytest.raises(PermissionError):
        r.reserve(replace(i, **change), s)
    assert rows(d) == []


def test_effect_cannot_own_two_intents(tmp_path):
    d, r, i, s = fixture(tmp_path)
    j = replace(i, request_id='second')
    r.provision(i, s)
    r.provision(j, s)
    r.reserve(i, s)
    with pytest.raises(sqlite3.IntegrityError):
        r.reserve(j, s)
    assert rows(d) == [('effect-1', 0)]


def compete(path, intent, snapshot, barrier, queue):
    registry = RefundIntentRegistry(DurableRefundDestination(path))
    barrier.wait(timeout=15)
    claim = registry.reserve(intent, snapshot)
    try:
        started = registry.mark_dispatch_started(intent, snapshot)
    except PermissionError:
        started = False
    queue.put((claim.newly_reserved, claim.original_effect_id, started))


def test_competing_processes_have_one_owner_and_one_dispatch_claim(tmp_path):
    d, r, i, s = fixture(tmp_path)
    r.provision(i, s)
    ctx = multiprocessing.get_context('spawn')
    barrier, queue = ctx.Barrier(4), ctx.Queue()
    processes = [ctx.Process(target=compete, args=(str(tmp_path), i, replace(s, effect_id=f'e{n}'), barrier, queue)) for n in range(4)]
    for p in processes:
        p.start()
    results = [queue.get(timeout=20) for p in processes]
    for p in processes:
        p.join(20)
        assert p.exitcode == 0
    assert sum(v[0] for v in results) == 1
    assert sum(v[2] for v in results) == 1
    assert len({v[1] for v in results}) == 1
    assert len(rows(d)) == 1


def crash(path, intent, snapshot, phase):
    registry = RefundIntentRegistry(DurableRefundDestination(path))
    if phase == 'before_commit':
        with registry._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('INSERT INTO refund_intent_claims_v1 VALUES (?,?,?,0)',
                         (intent.key, snapshot.effect_id, snapshot.operation.digest))
            os._exit(23)
    registry.reserve(intent, snapshot)
    if phase == 'dispatch':
        registry.mark_dispatch_started(intent, snapshot)
    os._exit(23)


@pytest.mark.parametrize('phase', ['before_commit', 'reserved', 'dispatch'])
def test_process_exit_and_reopen(tmp_path, phase):
    d, r, i, s = fixture(tmp_path)
    r.provision(i, s)
    p = multiprocessing.get_context('spawn').Process(target=crash, args=(str(tmp_path), i, s, phase))
    p.start()
    p.join(20)
    assert p.exitcode == 23
    reopened = RefundIntentRegistry(DurableRefundDestination(tmp_path))
    if phase == 'before_commit':
        assert rows(d) == []
        assert reopened.reserve(i, s).newly_reserved
    else:
        assert rows(d) == [('effect-1', int(phase == 'dispatch'))]
        assert not reopened.reserve(i, s).newly_reserved
        with pytest.raises(PermissionError):
            reopened.mark_dispatch_started(i, replace(s, effect_id='replacement'))
        if phase == 'dispatch':
            assert not reopened.mark_dispatch_started(i, s)
    assert d.observe(s.effect_id)['state'] == 'absent'


def test_absence_does_not_release_original_before_late_commit(tmp_path):
    d, r, i, s = fixture(tmp_path)
    r.provision(i, s)
    r.reserve(i, s)
    assert r.mark_dispatch_started(i, s)
    assert d.observe(s.effect_id)['state'] == 'absent'
    assert not r.reserve(i, replace(s, effect_id='replacement')).newly_reserved
    assert not r.mark_dispatch_started(i, s)
    # Simulates the already-running original, not a registry-authorized retry.
    d.commit(s)
    assert d.effect_count(s.operation.grant_id) == 1
    assert rows(d) == [('effect-1', 1)]


def test_storage_loss_fails_closed(tmp_path):
    d, r, i, s = fixture(tmp_path)
    r.provision(i, s)
    d.path.rename(d.path.with_suffix('.lost'))
    with pytest.raises(sqlite3.OperationalError):
        r.reserve(i, s)
    assert not d.path.exists()


def test_same_effect_changed_authority_cannot_consume_claim(tmp_path):
    d, r, i, s = fixture(tmp_path)
    r.provision(i, s)
    r.reserve(i, s)
    changed = replace(s, operation=replace(s.operation, grant_revision='2'))
    assert not r.reserve(i, changed).newly_reserved
    with pytest.raises(PermissionError):
        r.mark_dispatch_started(i, changed)
    assert rows(d) == [('effect-1', 0)]


def test_key_encoding_and_validation():
    a = RefundIntent('a|b', 'c', 'd', 'e')
    b = RefundIntent('a', 'b|c', 'd', 'e')
    assert a.key != b.key
    with pytest.raises(ValueError):
        replace(a, request_id=' ').key
    with pytest.raises(ValueError):
        replace(a, effect_class='transfer').key
