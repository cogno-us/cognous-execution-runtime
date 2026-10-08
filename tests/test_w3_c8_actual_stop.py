"""Source-authentic local controller observation journal qualification.

Controller records observations only after performing/observing each step.
This is a bounded test process, not a fleet stop claim.
"""
import multiprocessing as mp
import time

from engine.c8_source_evidence import LocalStopJournal
from engine.local_authority_effect import AtomicAuthorityEffectDestination
from engine.safe_executor import snapshot_envelope
from test_local_authority_effect import setup_atomic
from test_w2_stop_recovery import _blocked_worker,_attempt_rows
from test_local_authority_effect import effect_rows

def test_actual_local_stop_controller_retains_five_observations(tmp_path):
    _,env,destination,claim=setup_atomic(tmp_path)
    ctx=mp.get_context("spawn")
    reached=ctx.Event()
    proc=ctx.Process(target=_blocked_worker,
        args=(str(tmp_path),env,claim.claim_id,"after_commit",reached))
    proc.start()
    assert reached.wait(10)
    j=LocalStopJournal(tmp_path/"c8-stop.sqlite")
    life="local-stop-after-commit"
    effect=env.effect_id
    j.record(event_id="s1",sequence=1,lifecycle_id=life,effect_id=effect,
             kind="stop_requested",observation={"pid":proc.pid,"reason":"test_local_terminate"})
    proc.terminate()
    proc.join(10)
    assert proc.exitcode is not None
    j.record(event_id="s2",sequence=2,lifecycle_id=life,effect_id=effect,
             kind="stop_acknowledged",observation={"pid":proc.pid,"exitcode":proc.exitcode})
    assert not proc.is_alive()
    j.record(event_id="s3",sequence=3,lifecycle_id=life,effect_id=effect,
             kind="dispatch_closed",observation={"pid":proc.pid,"is_alive":False})
    reopened=AtomicAuthorityEffectDestination(tmp_path,clock=destination.clock)
    before_effects=effect_rows(reopened)
    before_attempts=_attempt_rows(reopened)
    time.sleep(0.02)
    assert effect_rows(reopened)==before_effects
    assert _attempt_rows(reopened)==before_attempts
    j.record(event_id="s4",sequence=4,lifecycle_id=life,effect_id=effect,
             kind="quiescence_observed",observation={
                "local_effect_rows_unchanged":True,
                "local_attempt_rows_unchanged":True})
    reconciliation=reopened.reconcile_claim(claim.claim_id,snapshot_envelope(env))
    assert reconciliation["status"]=="applied" and reconciliation["retry_eligible"] is False
    j.record(event_id="s5",sequence=5,lifecycle_id=life,effect_id=effect,
             kind="destination_reconciled",observation=reconciliation)
    exported=j.export(life)
    assert [r["kind"] for r in exported["events"]]==list((
        "stop_requested","stop_acknowledged","dispatch_closed",
        "quiescence_observed","destination_reconciled"))
