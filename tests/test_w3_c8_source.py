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
