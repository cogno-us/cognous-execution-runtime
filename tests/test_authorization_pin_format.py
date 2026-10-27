"""Frozen-format tests for the authorization pin (pure module: no database, file or lock is touched)."""
import copy
import json
from pathlib import Path

import pytest

from engine import authorization_pin_format as F

VEC = json.loads((Path(__file__).parent / "fixtures" / "authorization_pin_vectors.json").read_text())
D = lambda c: "sha256:" + c * 64


def test_vectors_reproduce_byte_for_byte():
    assert F.payload_digest(VEC["payload"]) == VEC["payload_digest"]
    assert F.encode_row(VEC["row_install"]) == VEC["row_install_text"]
    assert F.decode_row(VEC["row_install_text"]) == VEC["row_install"]
    assert F.anchor_encode(VEC["anchor_install_pending"]).decode() == VEC["anchor_install_pending_bytes"]
    assert F.anchor_decode(VEC["anchor_install_pending_bytes"].encode()) == VEC["anchor_install_pending"]
    assert F.fence_path("/f", "dec-1").name == VEC["fence_file_dec_1"]
    assert F.row_defect(VEC["row_terminal"]) is None and F.intent_defect(VEC["terminal_intent"]) is None


def test_payload_is_a_closed_member_set():
    assert tuple(VEC["payload"]) != () and set(VEC["payload"]) == set(F.PAYLOAD_MEMBERS) and len(F.PAYLOAD_MEMBERS) == 17
    for name in F.PAYLOAD_MEMBERS:
        broken = copy.deepcopy(VEC["payload"]); broken.pop(name)
        assert F.payload_defect(broken) == name
    extra = {**VEC["payload"], "family": "x"}
    assert F.payload_defect(extra) == "family"
    for name, bad in (("tenant_id", ""), ("tenant_id", None), ("selection_epoch", 0), ("selection_epoch", True), ("policy_generation", "7"),
                      ("binding_digest", "sha256:XYZ"), ("activation_ref", F.ZERO_DIGEST), ("not_before", "2026-10-27T00:00:00"),
                      ("expires_at", "2026-10-26T00:00:00Z")):
        assert F.payload_defect({**VEC["payload"], name: bad}) is not None, (name, bad)


def test_row_digest_detects_any_change():
    row = copy.deepcopy(VEC["row_install"])
    row["status"] = "revoked"
    assert F.row_defect(row) == "pin_digest"
    row = copy.deepcopy(VEC["row_install"]); row["payload"]["operation"] = "op-other"
    assert F.row_defect(row) == "pin_digest"
    row = copy.deepcopy(VEC["row_install"]); row["decision_id"] = "dec-2"
    assert F.row_defect(row) == "decision_id"
    with pytest.raises(F.PinFormatError):
        F.decode_row("not json")


def test_chain_and_decision_state():
    r1, r2 = VEC["row_install"], VEC["row_terminal"]
    a0 = F.make_anchor(pin_store_id="s", committed_epoch=0, committed_head_digest=F.ZERO_DIGEST, intent=None, anchored_at="2026-10-27T00:00:00Z")
    a1 = F.make_anchor(pin_store_id="s", committed_epoch=1, committed_head_digest=r1["pin_digest"], intent=None, anchored_at="2026-10-27T00:00:00Z")
    assert F.chain_defect([r1, r2], a0) is None and F.chain_defect([r1, r2], a1) is None and F.chain_defect([r2], a1) is not None
    assert F.chain_defect([], a0) is None and F.chain_defect([], a1) == F.CAUSE_CHAIN_BROKEN
    assert F.chain_defect([r2], a0) == F.CAUSE_CHAIN_BROKEN
    assert F.decision_state([r1, r2]) == (r2, None) and F.decision_state([r1]) == (r1, None) and F.decision_state([]) == (None, None)
    assert F.decision_state([r2])[1] == F.CAUSE_CONFLICT                         # first row must be active
    assert F.decision_state([r1, r2, r1])[1] == F.CAUSE_CONFLICT                 # anything after a terminal row (re-activation)
    assert F.decision_state([r1, r1])[1] == F.CAUSE_CONFLICT


def _anchor(committed, head_digest, intent):
    return F.make_anchor(pin_store_id="s", committed_epoch=committed, committed_head_digest=head_digest, intent=intent, anchored_at="2026-10-27T00:00:00Z")


def test_section7_admission_table():
    r1 = VEC["row_install"]
    h0, h1 = F.EMPTY_HEAD, F.Head(1, r1["pin_digest"], "dec-1", "active")
    term = F.make_intent(epoch=2, decision_id="dec-1", action="terminal", status="revoked", provenance_ref=D("e"))
    inst = F.make_intent(epoch=2, decision_id="dec-2", action="install", status="active", provenance_ref=D("e"))
    h2 = F.Head(2, D("9"), "dec-1", "revoked")
    cases = [
        (None, h1, "dec-1", ("hold_all", F.CAUSE_ANCHOR_UNAVAILABLE)),
        ({"junk": 1}, h1, "dec-1", ("hold_all", F.CAUSE_ANCHOR_UNAVAILABLE)),
        (_anchor(0, F.ZERO_DIGEST, None), h0, "dec-1", ("accept", None)),
        (_anchor(1, r1["pin_digest"], None), h1, "dec-1", ("accept", None)),
        (_anchor(0, F.ZERO_DIGEST, None), h1, "dec-1", ("accept", None)),                                    # store ahead of a stale anchor
        (_anchor(1, r1["pin_digest"], None), h0, "dec-1", ("hold_all", F.CAUSE_ANCHOR_INCONSISTENT)),        # store older than the anchor
        (_anchor(1, D("7"), None), h1, "dec-1", ("hold_all", F.CAUSE_ANCHOR_INCONSISTENT)),                  # different entry at the anchored epoch
        (_anchor(1, r1["pin_digest"], term), h1, "dec-1", ("hold_decision", F.CAUSE_PENDING)),               # pending terminal, row not committed
        (_anchor(1, r1["pin_digest"], term), h1, "dec-9", ("accept", None)),                                 # other decisions are admitted
        (_anchor(1, r1["pin_digest"], inst), h1, "dec-2", ("accept", None)),                                 # install intent is not a barrier
        (_anchor(1, r1["pin_digest"], term), h2, "dec-1", ("accept", None)),                                 # row committed, anchor completion pending
        (_anchor(1, r1["pin_digest"], term), F.Head(2, D("9"), "dec-1", "withdrawn"), "dec-1", ("hold_all", F.CAUSE_ANCHOR_INCONSISTENT)),
        (_anchor(1, r1["pin_digest"], term), F.Head(3, D("9"), "dec-1", "revoked"), "dec-1", ("hold_all", F.CAUSE_ANCHOR_INCONSISTENT)),
        (_anchor(0, F.ZERO_DIGEST, term), h0, "dec-1", ("hold_all", F.CAUSE_ANCHOR_INCONSISTENT)),           # intent epoch is not committed+1
    ]
    for anchor, head, decision, expected in cases:
        got = F.anchor_admission(anchor, head, decision)
        assert (got.verdict, got.cause) == expected, (anchor and anchor.get("intent"), head, decision, got)


def test_fence_path_is_a_fixed_hash_and_never_a_claim_chosen_path():
    for hostile in ("../../etc/passwd", "a/b", "dec\x00x"):
        p = F.fence_path("/fence", hostile)
        assert p.parent == Path("/fence") and p.name.endswith(".fence") and len(p.name) == 70 and "/" not in p.name
    with pytest.raises(F.PinFormatError):
        F.fence_path("/fence", "")
