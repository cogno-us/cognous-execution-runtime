from __future__ import annotations

from typing import Any
from engine.safe_executor import FrozenEnvelope


def reconcile_claim_exact(destination: Any, claim_id: str, snapshot: FrozenEnvelope) -> dict[str, Any]:
    with destination._connect() as conn:
        claim = conn.execute("SELECT state,effect_id,decision_id,operation_commitment,effect_operation_digest FROM execution_claims_v1 WHERE claim_id=?", (claim_id,)).fetchone()
        if claim is None:
            return {"status":"hold","retry_eligible":False,"reason":"claim_unavailable"}
        if claim["decision_id"] != snapshot.decision_id:
            return {"status":"hold","claim_state":claim["state"],"retry_eligible":False,"effect_id":snapshot.effect_id,"reason":"claim_decision_binding_mismatch"}
        if claim["effect_id"] != snapshot.effect_id:
            return {"status":"hold","claim_state":claim["state"],"retry_eligible":False,"effect_id":snapshot.effect_id,"reason":"claim_effect_binding_mismatch"}
        if claim["operation_commitment"] != destination._operation_commitment(snapshot):
            return {"status":"hold","claim_state":claim["state"],"retry_eligible":False,"effect_id":snapshot.effect_id,"reason":"claim_operation_binding_mismatch"}
        effect = conn.execute("SELECT state,operation_digest FROM effects WHERE effect_id=?", (claim["effect_id"],)).fetchone()
    if effect is not None:
        retained = claim["effect_operation_digest"]
        if claim["state"] != "consumed" or not retained or effect["operation_digest"] != retained or snapshot.operation.digest != retained:
            return {"status":"hold","claim_state":claim["state"],"retry_eligible":False,"effect_id":snapshot.effect_id,"reason":"retained_effect_operation_binding_mismatch"}
        return {"status":"applied" if effect["state"] == "applied" else "partial","claim_state":claim["state"],"retry_eligible":False,"effect_id":snapshot.effect_id,"operation_digest":retained}
    return {"status":"hold","claim_state":claim["state"],"retry_eligible":False,"effect_id":snapshot.effect_id,"reason":"no_durable_effect_for_claim"}
