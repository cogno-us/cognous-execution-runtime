from engine.c8_source_evidence import LocalStopJournal,export_authority_rows
import pytest

def test_missing_claim_is_missing_not_approved(tmp_path):
    from engine.local_authority_effect import AtomicAuthorityEffectDestination
    d=AtomicAuthorityEffectDestination(tmp_path,clock=lambda: __import__('datetime').datetime(2026,10,7,tzinfo=__import__('datetime').timezone.utc))
    out=export_authority_rows(d.path,claim_id="not-present")
    assert out["status"]=="missing_claim" and out["rows"]=={}

def test_stop_journal_never_fabricates_lifecycle(tmp_path):
    j=LocalStopJournal(tmp_path/"stops.sqlite")
    assert j.export("stop-1")["events"]==[]
    j.record(event_id="event-1",sequence=1,lifecycle_id="stop-1",effect_id="effect-1",
             kind="stop_requested",observation={"controller":"local"})
    assert [e["kind"] for e in j.export("stop-1")["events"]]==["stop_requested"]
    with pytest.raises(Exception):
        j.record(event_id="event-1",sequence=2,lifecycle_id="stop-1",
                 effect_id="effect-1",kind="stop_acknowledged",observation={})


def _j(tmp_path):
    return LocalStopJournal(tmp_path/"stops.sqlite")

def _rec(j,n,seq,kind="stop_requested",life="L",eff="e1"):
    j.record(event_id=f"ev-{n}",sequence=seq,lifecycle_id=life,effect_id=eff,kind=kind,observation={})

@pytest.mark.parametrize("bad",["7",7.5,-100,True,False,None])
def test_stop_journal_refuses_non_int_or_negative_sequence(tmp_path,bad):
    j=_j(tmp_path)
    with pytest.raises(ValueError):
        _rec(j,1,bad)
    assert j.export("L")["events"]==[]

def test_stop_journal_sequence_strictly_increasing(tmp_path):
    j=_j(tmp_path)
    _rec(j,1,5)
    for n,seq in ((2,5),(3,4),(4,0)):
        with pytest.raises(ValueError):
            _rec(j,n,seq,kind="stop_acknowledged")
    _rec(j,5,6,kind="stop_acknowledged")
    assert [e["sequence"] for e in j.export("L")["events"]]==[5,6]

def test_stop_journal_reconciled_needs_prior_stop_requested(tmp_path):
    j=_j(tmp_path)
    with pytest.raises(ValueError):
        _rec(j,1,1,kind="destination_reconciled")
    _rec(j,2,2,life="other",eff="e9")
    with pytest.raises(ValueError):  # another lifecycle's request does not count
        _rec(j,3,3,kind="destination_reconciled")
    assert j.export("L")["events"]==[]
    _rec(j,4,4)
    _rec(j,5,5,kind="destination_reconciled")

def test_stop_journal_lifecycle_bound_to_one_effect(tmp_path):
    j=_j(tmp_path)
    _rec(j,1,1)
    with pytest.raises(ValueError):
        _rec(j,2,2,kind="stop_acknowledged",eff="e2")
    _rec(j,3,3,kind="stop_acknowledged")
    assert {e["effect_id"] for e in j.export("L")["events"]}=={"e1"}
