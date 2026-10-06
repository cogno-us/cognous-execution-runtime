from dataclasses import asdict, replace
import copy
import json
import subprocess

import pytest

from engine.openshell_environment import (
    COMMAND, POLICY, OpenShellConfig, OpenShellCLI, OpenShellRefundDestination,
)
from engine.openshell_worker import handle
from engine.safe_executor import LocalDestinationExecutor, snapshot_envelope
from engine.control_plane_adapter import PinnedControlPlaneExecutor
from test_safe_executor import envelope, make_operation, policy, _integrated


def config(**kw):
    values = dict(gateway='local', workspace='default', sandbox='refund', sandbox_id='sandbox-1',
                  image='local/refund@sha256:' + 'a'*64, cli_sha256=__import__('hashlib').sha256(b'').hexdigest(), policy_json=json.dumps(POLICY, sort_keys=True),
                  policy_version=1, policy_hash='runtime-hash', config_revision=1,
                  provider_env_revision=0)
    values.update(kw)
    return OpenShellConfig(**values)


def metadata(c):
    return dict(id=c.sandbox_id, name=c.sandbox, workspace=c.workspace, phase='Ready',
                current_policy_version=c.policy_version, policy_source='sandbox',
                revision=c.policy_version, policy=json.loads(c.policy_json),
                configuration_admission=dict(state='accepted', policy_version=c.policy_version,
                    policy_hash=c.policy_hash, config_revision=c.config_revision,
                    provider_env_revision=c.provider_env_revision))


class FakeCLI:
    """Transport mock; real worker and SQLite, no OpenShell isolation claim."""
    def __init__(self, root, c):
        self.root = root
        self.info = metadata(c)
        self.calls = []
        self.failure = None

    def inspect(self, c):
        if self.failure == 'startup':
            raise OSError('gateway down')
        return copy.deepcopy(self.info)

    def invoke(self, c, request):
        self.calls.append(copy.deepcopy(request))
        if request['mode'] == 'commit' and self.failure == 'before_commit':
            raise TimeoutError('transport lost before result')
        result = handle(request, self.root)
        if request['mode'] == 'commit' and self.failure == 'lost_ack':
            raise TimeoutError('transport lost after commit')
        if request['mode'] == 'commit' and self.failure == 'fake_success':
            return {'duplicate': False}
        return result

    def request_stop(self, c):
        self.info['phase'] = 'Stopped'


def setup(tmp_path, op=None):
    c = config()
    cli = FakeCLI(tmp_path/'remote', c)
    d = OpenShellRefundDestination(tmp_path/'host', c, cli)
    req = envelope(op)
    snap = snapshot_envelope(req)
    d.bind(snap)
    return c, cli, d, snap, LocalDestinationExecutor(d, policy(req.operation))


def test_mocked_transport_real_worker_exact_effect_and_restart(tmp_path):
    c, cli, d, snap, executor = setup(tmp_path)
    first = executor.execute_snapshot(snap)
    assert first.status == 'executed' and first.newly_executed
    assert first.observation['destination_state']['payload'] == snap.operation.payload()
    assert first.observation['destination_state']['amount'] == snap.operation.amount
    restarted = OpenShellRefundDestination(tmp_path/'host', c, cli)
    second = LocalDestinationExecutor(restarted, executor.policy).execute_snapshot(snap)
    assert second.status == 'reconciled' and not second.newly_executed
    assert sum(x['mode'] == 'commit' for x in cli.calls) == 1
    with d._connect() as db:
        evidence = [r['kind'] for r in db.execute('SELECT kind FROM environment_events')]
    assert {'bound_configuration','runtime_observed','dispatch_requested','destination_observed'} <= set(evidence)


@pytest.mark.parametrize('failure', ['lost_ack', 'before_commit', 'startup'])
def test_unknown_delivery_never_blindly_retries(tmp_path, failure):
    c, cli, d, snap, executor = setup(tmp_path)
    cli.failure = failure
    first = executor.execute_snapshot(snap)
    assert first.status == 'unknown' and not first.newly_executed
    cli.failure = None
    restarted = OpenShellRefundDestination(tmp_path/'host', c, cli)
    result = LocalDestinationExecutor(restarted, executor.policy).execute_snapshot(snap)
    expected = {'lost_ack': 'reconciled', 'before_commit': 'unknown', 'startup': 'executed'}
    assert result.status == expected[failure]
    assert sum(x['mode'] == 'commit' for x in cli.calls) == 1


@pytest.mark.parametrize('field,value', [('sandbox_id','other'), ('timeout_seconds',16), ('config_revision',2)])
def test_environment_substitution_holds(tmp_path, field, value):
    c, cli, d, snap, executor = setup(tmp_path)
    substituted = OpenShellRefundDestination(tmp_path/'host', replace(c, **{field:value}), cli)
    with pytest.raises(PermissionError):
        substituted.bind(snap)
    with pytest.raises(PermissionError):
        substituted.observe_bound(snap)
    assert not cli.calls


@pytest.mark.parametrize('change', ['policy', 'id', 'admission', 'provider', 'phase'])
def test_effective_runtime_change_rejected(tmp_path, change):
    c, cli, d, snap, executor = setup(tmp_path)
    if change == 'policy':
        cli.info['policy']['network_policies'] = {'unapproved':{}}
    elif change == 'admission':
        cli.info['configuration_admission']['config_revision'] = 2
    elif change == 'provider':
        cli.info['configuration_admission']['provider_env_revision'] = 1
    else:
        cli.info[change] = 'substitution'
    assert executor.execute_snapshot(snap).status == 'denied'
    assert not cli.calls


@pytest.mark.parametrize('changes', [dict(executable=('/bin/sh','-c','evil')), dict(workdir='/tmp'),
    dict(image='image:latest'), dict(runtime='vm'), dict(timeout_seconds=True),
    dict(policy_json=json.dumps({**POLICY,'network_policies':{'allow':{}}}))])
def test_unsupported_profile_rejected(changes):
    with pytest.raises(ValueError):
        config(**changes)


def test_bound_operation_cannot_change(tmp_path):
    c, cli, d, snap, executor = setup(tmp_path)
    changed = snapshot_envelope(envelope(make_operation(target='urn:cognous:synthetic-account:other')))
    with pytest.raises(PermissionError):
        d.observe_bound(changed)
    assert not cli.calls


def test_cancellation_is_not_rollback(tmp_path):
    c, cli, d, snap, executor = setup(tmp_path)
    executor.execute_snapshot(snap)
    assert d.request_cancel(snap)['state'] == 'unknown'
    cli.info['phase'] = 'Ready'
    assert d.observe_bound(snap)['state'] == 'applied'


@pytest.mark.parametrize('mutation', [None,'missing','revoked','expired','policy','stale_evidence','payload'])
def test_actual_pinned_control_plane_with_mocked_openshell(tmp_path, mutation):
    h,p,resolver,workflow,decision,_,_,req = _integrated(tmp_path)
    c = config()
    cli = FakeCLI(tmp_path/'sandbox', c)
    d = OpenShellRefundDestination(tmp_path/'host', c, cli)
    d.bind(snapshot_envelope(req))
    executor = PinnedControlPlaneExecutor(workflow=workflow, destination=d, policy=policy(req.operation))
    grant = resolver.contexts[h.PROFILE]['grant']
    if mutation == 'missing':
        decision = decision.model_copy(update={'decision_id':'missing'})
    elif mutation == 'revoked':
        resolver.statuses[grant['grant_id']].status = 'revoked'
    elif mutation == 'expired':
        grant['expires_at'] = (h.NOW-h.timedelta(seconds=1)).isoformat()
    elif mutation == 'policy':
        resolver.policies[h.POLICY_REF].version = '2'
    elif mutation == 'stale_evidence':
        resolver.evidence['urn:cognous:evidence:refund-entitlement'].observed_at = (h.NOW-h.timedelta(minutes=10)).isoformat()
    elif mutation == 'payload':
        req.operation.payload['refund_reason'] = 'substituted'
    if mutation == 'payload':
        with pytest.raises(ValueError):
            executor.execute(envelope=req, proposal=p, decision=decision, now=h.NOW)
    else:
        result = executor.execute(envelope=req, proposal=p, decision=decision, now=h.NOW)
        assert result.status == ('executed' if mutation is None else 'denied')
    assert sum(x['mode'] == 'commit' for x in cli.calls) == (mutation is None)


def test_cli_contract_uses_constant_argv_and_stdin(monkeypatch, tmp_path):
    binary = tmp_path/'openshell'
    binary.touch()
    cli = OpenShellCLI(str(binary), str(tmp_path))
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout='{}')
    monkeypatch.setattr(subprocess, 'run', run)
    cli.invoke(config(), {'payload': '$(touch /tmp/no); ; malicious'})
    argv, kw = calls[0]
    assert argv[-len(COMMAND):] == list(COMMAND)
    assert '--no-login-shell' in argv and 'BASH_ENV=/dev/null' in argv
    assert kw['shell'] is False and 'malicious' in kw['input']
    assert not any('malicious' in a for a in argv)
    assert set(kw['env']) == {'HOME','PATH','XDG_CONFIG_HOME'}


def test_post_dispatch_policy_change_is_unknown_not_success(tmp_path):
    c, cli, d, snap, executor = setup(tmp_path)
    invoke = cli.invoke
    def changed(c, request):
        result = invoke(c, request)
        if request['mode'] == 'commit':
            cli.info['configuration_admission']['config_revision'] = 2
        return result
    cli.invoke = changed
    result = executor.execute_snapshot(snap)
    assert result.status == 'unknown' and not result.newly_executed


def test_process_success_without_effect_is_unknown(tmp_path):
    c, cli, d, snap, executor = setup(tmp_path)
    invoke = cli.invoke
    cli.invoke = lambda c,r: {'duplicate':False} if r['mode']=='commit' else invoke(c,r)
    result = executor.execute_snapshot(snap)
    assert result.status == 'unknown' and result.observed_state == 'unknown'
    assert not result.newly_executed


def test_local_restriction_prevents_sandbox_dispatch(tmp_path):
    c, cli, d, snap, executor = setup(tmp_path)
    executor.policy = replace(executor.policy, max_amount=1)
    with pytest.raises(PermissionError):
        executor.execute_snapshot(snap)
    assert cli.calls == []


def test_partial_destination_holds(tmp_path):
    c, cli, d, snap, executor = setup(tmp_path)
    from engine.safe_executor import DurableRefundDestination
    DurableRefundDestination(cli.root).commit(snap, simulate='partial')
    result = executor.execute_snapshot(snap)
    assert result.status == 'partial' and not result.newly_executed
    assert not any(x['mode']=='commit' for x in cli.calls)


def test_cli_digest_substitution_rejects(tmp_path):
    binary = tmp_path/'openshell'
    binary.write_text('substituted')
    cli = OpenShellCLI(str(binary), str(tmp_path))
    with pytest.raises(PermissionError, match='digest'):
        cli.invoke(config(), {})


def _process_delivery(root, queue):
    from pathlib import Path
    root = Path(root)
    c = config()
    cli = FakeCLI(root/'remote', c)
    d = OpenShellRefundDestination(root/'host', c, cli)
    snap = snapshot_envelope(envelope())
    result = LocalDestinationExecutor(d, policy(make_operation())).execute_snapshot(snap)
    queue.put((result.status, sum(x['mode']=='commit' for x in cli.calls)))


def test_separate_processes_reserve_dispatch_once(tmp_path):
    import multiprocessing
    from engine.safe_executor import DurableRefundDestination
    c, cli, d, snap, executor = setup(tmp_path)
    DurableRefundDestination(cli.root)
    ctx = multiprocessing.get_context('spawn')
    queue = ctx.Queue()
    processes = [ctx.Process(target=_process_delivery, args=(str(tmp_path),queue)) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    results = [queue.get(timeout=2) for _ in processes]
    assert sum(n for _,n in results) == 1
    assert d.observe_bound(snap)['state'] == 'applied'
    assert DurableRefundDestination(cli.root).effect_count(snap.operation.grant_id) == 1


@pytest.mark.parametrize('mutate', ['version','driver','server','healthy',None])
def test_cli_runtime_contract(monkeypatch, tmp_path, mutate):
    binary = tmp_path/'openshell'
    binary.touch()
    cli = OpenShellCLI(str(binary), str(tmp_path))
    info = {'version':'0.1.2','status':'healthy','server':'https://127.0.0.1:17670',
            'compute_drivers':[{'capabilities':{'driver_name':'docker'}}]}
    if mutate == 'version': info['version'] = '0.1.3'
    if mutate == 'driver': info['compute_drivers'][0]['capabilities']['driver_name'] = 'vm'
    if mutate == 'server': info['server'] = 'https://remote.invalid'
    if mutate == 'healthy': info['status'] = 'degraded'
    cli.run = lambda c,args,**kw: json.dumps(info if args[0]=='gateway' else metadata(c))
    if mutate:
        with pytest.raises(PermissionError): cli.inspect(config())
    else:
        assert cli.inspect(config())['runtime_evidence'] == info
