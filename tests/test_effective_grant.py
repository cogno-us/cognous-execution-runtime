"""Component-local synthetic commit-precondition tests; no network effects."""
import pytest
from engine.effective_grant import EffectiveGrantDestination,DERIVATION
from engine.safe_executor import DurableRefundDestination,ExecutionEnvelope,EXECUTION_ENVELOPE_VERSION,snapshot_envelope,LocalDestinationExecutor
from test_producer_contract import operation,policy

def fixture(tmp_path):
    op=operation();snap=snapshot_envelope(ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION,'decision-1','effect-1',op))
    dest=EffectiveGrantDestination(tmp_path/'state')
    digest=dest.set_effective_grant(principal=op.principal,grant_id=op.grant_id,revision=op.grant_revision,permissions=list(op.requested_permissions))
    return op,snap,dest,digest

def test_valid_effective_grant_precondition_commits(tmp_path):
    op,snap,dest,digest=fixture(tmp_path)
    result=LocalDestinationExecutor(dest,policy(op)).execute_snapshot(snap,expected_effective_grant_digest=digest,effective_grant_derivation_version=DERIVATION)
    assert result.status=='executed' and dest.effect_count(op.grant_id)==1

@pytest.mark.parametrize('permissions,status',[(['refund.issue','refund.admin'],'active'),([], 'active'),(['refund.issue'],'revoked')])
def test_role_widened_between_read_and_commit_refused(tmp_path,permissions,status):
    op,snap,dest,read_digest=fixture(tmp_path)
    # A separate trusted writer lands after the caller's read, before commit.
    writer=EffectiveGrantDestination(dest.root)
    writer.set_effective_grant(principal=op.principal,grant_id=op.grant_id,revision='2',permissions=permissions,status=status)
    with pytest.raises(PermissionError,match='effective_grant_changed_at_commit'):
        dest.commit(snap,expected_effective_grant_digest=read_digest,effective_grant_derivation_version=DERIVATION)
    assert dest.observe(snap.effect_id)['state']=='absent' and dest.effect_count(op.grant_id)==0

@pytest.mark.parametrize('case',['bare','wrong-version','caller-digest','missing-grant'])
def test_missing_or_substituted_commit_precondition_holds(tmp_path,case):
    op,snap,dest,digest=fixture(tmp_path)
    if case=='missing-grant':
        with dest._connect() as c:c.execute('DELETE FROM effective_grants_v1')
    kwargs={} if case=='bare' else {'expected_effective_grant_digest':('sha256:'+'0'*64 if case=='caller-digest' else digest),
        'effective_grant_derivation_version':('unknown' if case=='wrong-version' else DERIVATION)}
    with pytest.raises(PermissionError):dest.commit(snap,**kwargs)
    assert dest.effect_count(op.grant_id)==0

def test_reopened_base_destination_cannot_bypass_profile(tmp_path):
    op,snap,dest,digest=fixture(tmp_path);base=DurableRefundDestination(dest.root)
    with pytest.raises(PermissionError,match='precondition_required'):base.commit(snap)
    assert dest.effect_count(op.grant_id)==0

def test_unsupported_destination_rejects_precondition(tmp_path):
    op,snap,dest,digest=fixture(tmp_path);base=DurableRefundDestination(tmp_path/'legacy')
    with pytest.raises(PermissionError,match='capability_unavailable'):
        base.commit(snap,expected_effective_grant_digest=digest,effective_grant_derivation_version=DERIVATION)
    assert base.effect_count(op.grant_id)==0

def test_lost_ack_retains_one_effect(tmp_path):
    op,snap,dest,digest=fixture(tmp_path)
    with pytest.raises(TimeoutError):dest.commit(snap,simulate='lost_ack',expected_effective_grant_digest=digest,effective_grant_derivation_version=DERIVATION)
    assert dest.commit(snap,expected_effective_grant_digest=digest,effective_grant_derivation_version=DERIVATION)['duplicate']
    assert dest.effect_count(op.grant_id)==1

def test_comparison_occurs_in_effect_transaction(tmp_path,monkeypatch):
    op,snap,dest,digest=fixture(tmp_path);trace=[];connect=dest._connect
    def traced():
        conn=connect();conn.set_trace_callback(trace.append);return conn
    monkeypatch.setattr(dest,'_connect',traced)
    dest.commit(snap,expected_effective_grant_digest=digest,effective_grant_derivation_version=DERIVATION)
    begin=next(i for i,x in enumerate(trace) if 'BEGIN IMMEDIATE' in x)
    check=next(i for i,x in enumerate(trace) if 'SELECT principal,digest,derivation,status FROM effective_grants_v1' in x)
    write=next(i for i,x in enumerate(trace) if 'INSERT INTO effects' in x)
    assert begin<check<write

def test_legacy_worker_does_not_require_optional_profile_module(tmp_path,monkeypatch):
    import builtins
    op=operation();snap=snapshot_envelope(ExecutionEnvelope(EXECUTION_ENVELOPE_VERSION,'legacy-decision','legacy-effect',op))
    dest=DurableRefundDestination(tmp_path/'legacy');original=builtins.__import__
    def guarded(name,*args,**kwargs):
        if name.endswith('effective_grant'):raise ModuleNotFoundError('optional profile is not packaged')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',guarded)
    result=LocalDestinationExecutor(dest,policy(op)).execute_snapshot(snap)
    assert result.status=='executed' and dest.effect_count(op.grant_id)==1
