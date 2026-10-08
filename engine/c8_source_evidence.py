"""C8 read-only source-row export and explicit local stop event journal.

Rows are read from the accepted local SQLite authority store. Stop events must be
written at the actual call sites by the controlling process; no event is inferred.
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path

AUTHORITY_TABLES = (
    ("execution_claims_v1", "claim_id"),
    ("authority_grants_v1", "grant_id"),
    ("authority_approvals_v1", "approval_ref"),
    ("authority_policies_v1", "ref"),
)
STOP_KINDS = ("stop_requested", "stop_acknowledged", "dispatch_closed",
              "quiescence_observed", "destination_reconciled")


def export_authority_rows(database: str | Path, *, claim_id: str) -> dict:
    """Return rows actually retained for the claim and related authority IDs."""
    with sqlite3.connect(f"file:{Path(database).resolve()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        claim = db.execute("SELECT * FROM execution_claims_v1 WHERE claim_id=?", (claim_id,)).fetchone()
        if claim is None:
            return {"generation":"c8-source-rows/0.1","claim_id":claim_id,
                    "status":"missing_claim","rows":{}}
        c = dict(claim)
        result = {"execution_claims_v1":[c]}
        refs = {
            "authority_grants_v1": ("grant_id", c.get("grant_id")),
            "authority_approvals_v1": ("grant_id", c.get("grant_id")),
        }
        for table,(column,value) in refs.items():
            result[table] = [dict(x) for x in db.execute(
                f"SELECT * FROM {table} WHERE {column}=? ORDER BY rowid", (value,))] if value else []
        policies=[]
        try:
            for approval in result["authority_approvals_v1"]:
                for item in json.loads(approval.get("policy_versions_json") or "[]"):
                    if isinstance(item,dict) and item.get("ref"):
                        policies.extend(dict(x) for x in db.execute(
                            "SELECT * FROM authority_policies_v1 WHERE ref=? AND version=?",
                            (item["ref"],item.get("version"))))
        except (ValueError,TypeError,KeyError):
            return {"generation":"c8-source-rows/0.1","claim_id":claim_id,
                    "status":"unknown_policy_reference","rows":result}
        result["authority_policies_v1"]=policies
    return {"generation":"c8-source-rows/0.1","claim_id":claim_id,
            "status":"source_rows_retained","rows":result,
            "provenance":"local_sqlite_rows_not_independent_verification"}


class LocalStopJournal:
    """Append-only local process stop observations, never an OS stop mechanism."""
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS c8_stop_events (
                event_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL UNIQUE,
                lifecycle_id TEXT NOT NULL, effect_id TEXT NOT NULL,
                kind TEXT NOT NULL, observation_json TEXT NOT NULL)""")

    def record(self, *, event_id: str, sequence: int, lifecycle_id: str,
               effect_id: str, kind: str, observation: dict) -> None:
        if kind not in STOP_KINDS or not isinstance(observation,dict):
            raise ValueError("unsupported stop event")
        if not all(isinstance(x,str) and x for x in (event_id,lifecycle_id,effect_id)):
            raise ValueError("stop event identity required")
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO c8_stop_events VALUES (?,?,?,?,?,?)",
                       (event_id,sequence,lifecycle_id,effect_id,kind,
                        json.dumps(observation,sort_keys=True,allow_nan=False)))

    def export(self, lifecycle_id: str) -> dict:
        with sqlite3.connect(f"file:{self.path.resolve()}?mode=ro",uri=True) as db:
            rows=db.execute("""SELECT event_id,sequence,lifecycle_id,effect_id,kind,observation_json
                               FROM c8_stop_events WHERE lifecycle_id=? ORDER BY sequence""",
                            (lifecycle_id,)).fetchall()
        return {"generation":"c8-local-stop/0.1","lifecycle_id":lifecycle_id,
                "events":[{"event_id":a,"sequence":b,"lifecycle_id":c,"effect_id":d,
                           "kind":e,"observation":json.loads(f)} for a,b,c,d,e,f in rows],
                "provenance":"explicit_local_controller_observations_only"}
