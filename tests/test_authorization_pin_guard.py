"""F-A1 Runtime guard oracles (Registry r08-v2 Amendment 4; worker C). BUILDER_TESTED, non-attesting; NOT independently qualified.

Scope: the READ-ONLY Runtime half (``engine/authorization_pin.py`` and its use in ``AtomicAuthorityEffectDestination``). The pin store, anchor and
fence files here are written by a TEST-ONLY writer defined in this module using the frozen format module; it is NOT the trusted installer, and
nothing here proves OS-identity isolation (these are same-identity, in-process tests: they show the guard logic and the lock protocol, not
privilege separation). Reuses the V05/F-01 fixtures by import; requires the same environment as those suites (OCS_REGISTRY_PIN_DIR, PyNaCl,
Control Plane V02, pydantic >= 2.12); otherwise the module is reported as skipped, never as passed.
"""
from __future__ import annotations

import dataclasses
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

import test_registry_selection_effect_guard as g

from agent_control_plane import canonical_policy_binding as cpb
from engine import authorization_pin_format as fmt
from engine.authorization_pin import AuthorizationPinConfig, PinReader
from engine.local_authority_effect import AtomicAuthorityEffectDestination

v4, sa, flow = g.v4, g.sa, g.flow
FIELDS = sa.sa_fields()
EXTRA = {"allowed_actions": "act-extra", "allowed_adapters": "a-extra", "allowed_units": "u-extra",
         "allowed_institutions": "inst-extra", "allowed_authority_domains": "domain-extra"}
STORE_ID = "pin-store-test-1"


def D(ch: str) -> str:
    return "sha256:" + ch * 64


# ================================================================================================================ TEST-ONLY pin writer
class PinWriter:
    """TEST-ONLY. Builds a pin store, anchor and fence files with the frozen format module. Not the trusted installer."""

    def __init__(self, base: Path, store_id: str = STORE_ID):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)
        self.store = self.base / "pins.sqlite3"
        self.anchor = self.base / "pins.anchor.json"
        self.fences = self.base / "fences"
        self.fences.mkdir(exist_ok=True)
        self.store_id = store_id
        conn = sqlite3.connect(self.store, isolation_level=None)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute(f"CREATE TABLE {fmt.PIN_TABLE}(pin_epoch INTEGER PRIMARY KEY, decision_id TEXT NOT NULL, row_json TEXT NOT NULL)")
        conn.execute(f"CREATE TABLE {fmt.META_TABLE}(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(f"INSERT INTO {fmt.META_TABLE} VALUES('profile', ?)", (fmt.PIN_PROFILE,))
        conn.execute(f"INSERT INTO {fmt.META_TABLE} VALUES('pin_store_id', ?)", (store_id,))
        conn.close()
        self.write_anchor(0, fmt.ZERO_DIGEST)

    # -- store
    def _sql(self, statement, params=()):
        conn = sqlite3.connect(self.store, isolation_level=None)
        try:
            return conn.execute(statement, params).fetchall()
        finally:
            conn.close()

    def head(self):
        rows = self._sql(f"SELECT row_json FROM {fmt.PIN_TABLE} ORDER BY pin_epoch DESC LIMIT 1")
        if not rows:
            return 0, fmt.ZERO_DIGEST
        row = json.loads(rows[0][0])
        return row["pin_epoch"], row["pin_digest"]

    def rows(self, decision_id=None):
        return [json.loads(r[0]) for r in self._sql(f"SELECT row_json FROM {fmt.PIN_TABLE} ORDER BY pin_epoch")
                if decision_id is None or json.loads(r[0])["decision_id"] == decision_id]

    def append(self, payload, status="active", *, epoch=None, prev=None):
        head_epoch, head_digest = self.head()
        row = fmt.make_row(pin_epoch=epoch if epoch is not None else head_epoch + 1, decision_id=payload["decision_id"], status=status,
                           payload=payload, provenance={"intent_digest": D("1"), "reason": "test-only writer"},
                           prev_pin_digest=prev if prev is not None else head_digest)
        self._sql(f"INSERT INTO {fmt.PIN_TABLE} VALUES(?,?,?)", (row["pin_epoch"], row["decision_id"], fmt.encode_row(row)))
        return row

    def fence(self, decision_id):
        path = fmt.fence_path(self.fences, decision_id)
        path.touch()
        return path

    # -- anchor
    def write_anchor(self, committed, digest, intent=None):
        anchor = fmt.make_anchor(pin_store_id=self.store_id, committed_epoch=committed, committed_head_digest=digest, intent=intent,
                                 anchored_at="2026-10-25T00:00:00Z")
        self.anchor.write_bytes(fmt.anchor_encode(anchor))
        return anchor

    def complete(self, intent=None):
        epoch, digest = self.head()
        return self.write_anchor(epoch, digest, intent)

    def install(self, payload):
        row = self.append(payload)
        self.fence(payload["decision_id"])
        self.complete()
        return row

    def terminal(self, payload, status):
        row = self.append(payload, status)
        self.complete()
        return row

    def intent(self, decision_id, action="terminal", status="revoked"):
        epoch, digest = self.head()
        anchor = fmt.anchor_decode(self.anchor.read_bytes())
        intent = fmt.make_intent(epoch=anchor["committed_epoch"] + 1, decision_id=decision_id, action=action,
                                 status="active" if action == "install" else status, provenance_ref="test-provenance")
        return self.write_anchor(anchor["committed_epoch"], anchor["committed_head_digest"], intent)


# ================================================================================================================ fixtures
def mk_env(decision_id="decision-1", effect_id="effect-1", **changes):
    op = dataclasses.replace(flow.operation(), **{**g.op_changes(), **changes})
    return g.ExecutionEnvelope(g.EXECUTION_ENVELOPE_VERSION, decision_id, effect_id, op)


class Case:
    """A governed destination (V04/V05 fixtures) plus a SECOND handle on the same database that requires a pin."""

    def __init__(self, tmp_path, *, deadline=1.0, dest_cls=AtomicAuthorityEffectDestination, activate=True):
        self.gov = g.Gov(tmp_path, activate=activate)
        self.pins = PinWriter(Path(tmp_path) / "pins")
        self.cfg = AuthorizationPinConfig(pin_store_path=self.pins.store, fence_dir=self.pins.fences, anchor_path=self.pins.anchor,
                                          pin_store_id=STORE_ID, fence_deadline_seconds=deadline)
        self.dest = dest_cls(self.gov.e.root, clock=lambda: self.gov.e.now, authorization_pin=self.cfg)
        self.dest.configure_selection_authority(self.gov.e.anchor)
        self.n = 0

    @property
    def now(self):
        return self.gov.now

    def binding(self, **over):
        return g.binding_of(self.gov.state(), **over)

    def payload(self, env, binding=None, **over):
        binding = binding if binding is not None else self.binding()
        op = g.snapshot_envelope(env).operation
        p = {"tenant_id": op.tenant_id, "institution_id": op.institution_id, "authority_domain": op.authority_domain,
             "consumer_id": binding["consumer_id"], "operation": binding["operation"], "decision_id": env.decision_id,
             "effect_id": env.effect_id, "action_id": op.action_id, "grant_id": op.grant_id, "grant_revision": op.grant_revision,
             "approval_commitment": D("a"), "binding_digest": cpb.binding_digest(binding), "selection_epoch": binding["selection_epoch"],
             "activation_ref": binding["activation_ref"], "policy_generation": 1,
             "not_before": (self.now - self._days(3)).isoformat(), "expires_at": (self.now + self._days(5)).isoformat()}
        p.update(over)
        return p

    @staticmethod
    def _days(n):
        from datetime import timedelta
        return timedelta(days=n)

    def claim(self, env=None, binding="current", claim_id=None):
        self.n += 1
        env = env or mk_env()
        cid = claim_id or f"pin-claim-{self.n}"
        if binding == "current":
            binding = self.binding()
        self.gov.dest.provision_claim(g.make_claim(env, self.now, binding=binding, claim_id=cid))
        return env, cid

    def executor(self, policy=None, dest=None):
        return g.AtomicLocalControlPlaneExecutor(workflow=None, destination=dest or self.dest, policy=policy or g.effect_policy())

    def honest(self, **pin_over):
        """Install the honest pin for decision-1 and provision the honest claim."""
        env = mk_env()
        binding = self.binding()
        self.pins.install(self.payload(env, binding, **pin_over))
        return (env,) + self.claim(env, binding)[1:]

    def run(self, env, cid, policy=None):
        return self.executor(policy).execute(envelope=env, claim_id=cid)

    def counts(self):
        return self.gov.counts()


def held(path: Path) -> bool:
    """True when a LOCK_EX probe on the fence is blocked, that is some shared holder is present."""
    fd = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def refused(result, code, cls, cause):
    typed = g.refused(result, code, cls)
    assert typed["cause"] == cause, typed
    return typed


@pytest.fixture
def case(tmp_path):
    return Case(tmp_path)


class Recorder(AtomicAuthorityEffectDestination):
    """Records the `_transaction_stage` seam and, at each stage, whether the decision fence is held by this process."""
    stages: list
    fence_probe = None

    def _transaction_stage(self, stage):
        if not hasattr(self, "stages"):
            self.stages = []
        self.stages.append((stage, type(self).fence_probe() if type(self).fence_probe else None))


# ================================================================================================================ valid path and receipt
def test_honest_claim_executes_and_the_receipt_carries_the_pin(case):
    env, cid = case.honest()
    result = case.run(env, cid)
    assert result.status == "executed" and case.counts()[0] == 1
    row = case.pins.rows("decision-1")[0]
    receipt = result.control_plane_evidence["authority_receipt"]
    assert receipt["authorization_pin"] == {"pin_epoch": 1, "pin_digest": row["pin_digest"], "anchor_committed_epoch": 1,
                                            "payload_digest": fmt.payload_digest(row["payload"])}
    assert json.loads(case.gov.receipt())["authorization_pin"] == receipt["authorization_pin"]
    assert receipt["selection_guard"]["result"] == "binding_verified"


def test_without_a_pin_option_receipt_and_behaviour_are_unchanged(tmp_path):
    gov = g.Gov(tmp_path)
    assert gov.dest.authorization_pin_required is False
    env, cid, result = gov.run()
    assert result.status == "executed"
    assert "authorization_pin" not in json.loads(gov.receipt())


def test_constructor_rejects_a_non_config(tmp_path):
    with pytest.raises(TypeError):
        AtomicAuthorityEffectDestination(Path(tmp_path) / "d", clock=lambda: g.v4.r.NOW, authorization_pin={"pin_store_path": "x"})


# ================================================================================================================ original F-A1 substitution
def test_original_fa1_substitution_is_refused_on_the_operation_with_zero_effects(tmp_path):
    case = Case(tmp_path)
    wide_payload = {k: sorted(set(v4.PAYLOAD[k]) | {EXTRA[k]}) for k in FIELDS}
    wide_rec = v4.reg_record(payload=wide_payload)
    other = v4.select_stmt(wide_rec, sid="sel-other", operation="op-other")
    ctx = v4.c.ExpectedContext("consumer-test", "tenant-a", "inst-a", "domain-a", "op-other")
    both = (v4.s.GRANT, v4.s.v.AuthorityGrant("consumer-test", "tenant-a", "inst-a", "domain-a", "op-other"))
    t2 = v4.s.v.TrustSnapshot("snap-test-1", 2, "2026-10-27T00:00:00Z", (v4.s.v.IssuerEntry(v4.s.KEY_ID, "issuer-test-1", "ed25519", v4.PK, both),))
    case.gov.dest.sync_selection("snapshot", trust=t2)
    case.gov.e.trust = t2
    case.gov.dest.activate_selection(record_text=v4.r.dumps(wide_rec), selection_text=v4.text(other), context=ctx, trust=case.gov.e.trust,
                                     verifier=v4.ev.Ed25519Verifier())    # another VALIDLY ACTIVATED operation, wide record
    wide_policy = g.effect_policy(**{k: frozenset(wide_payload[k]) for k in FIELDS})
    attack = g.binding_of(case.gov.e.state(v4.GS_OTHER), operation="op-other", selection_id="sel-other",
                          effective_policy_digest=sa.policy_digest_of(wide_policy))
    # the honest pin is for the original operation; the supplier rewrote the binding and recomputed the (unkeyed) claim commitment
    env = mk_env()
    case.pins.install(case.payload(env, case.binding()))
    env, cid = case.claim(env, attack, claim_id="attack")
    typed = refused(case.run(env, cid, wide_policy), "policy_identity_mismatch", "REFUSE", "pin_binding_mismatch:operation")
    assert typed["code"] == "policy_identity_mismatch" and case.counts() == (0, 0, 0, 0)
    # control: the SAME attack on a destination without the pin option executes (this is the defect the pin closes)
    env2 = mk_env("decision-2", "effect-2")
    env2, cid2 = case.claim(env2, attack, claim_id="attack-control")
    assert case.executor(wide_policy, dest=case.gov.dest).execute(envelope=env2, claim_id=cid2).status == "executed"


# ================================================================================================================ pin step refusals
@pytest.mark.parametrize("member", ["effect_id", "action_id", "tenant_id", "institution_id", "authority_domain", "grant_id", "grant_revision"])
def test_each_lineage_member_mismatch_is_refused(tmp_path, member):
    case = Case(tmp_path)
    env, cid = case.honest(**{member: "other-" + member})
    refused(case.run(env, cid), "policy_identity_mismatch", "REFUSE", f"pin_lineage_mismatch:{member}")
    assert case.counts() == (0, 0, 0, 0)


BINDING_CASES = {
    "consumer_id": dict(consumer_id="consumer-x"),
    "operation": dict(operation="op-x"),
    "selection_epoch": dict(selection_epoch=7),
    "activation_ref": dict(activation_ref=D("9")),
}


@pytest.mark.parametrize("member", sorted(BINDING_CASES))
def test_each_claim_binding_member_that_differs_from_the_pin_is_refused(tmp_path, member):
    case = Case(tmp_path)
    env = mk_env()
    case.pins.install(case.payload(env, case.binding()))
    env, cid = case.claim(env, case.binding(**BINDING_CASES[member]))
    refused(case.run(env, cid), "policy_identity_mismatch", "REFUSE", f"pin_binding_mismatch:{member}")
    assert case.counts() == (0, 0, 0, 0)


@pytest.mark.parametrize("member,scope", [
    ("scope.tenant", {"tenant": "tenant-x", "institution": "inst-a", "authority_domain": "domain-a"}),
    ("scope.institution", {"tenant": "tenant-a", "institution": "inst-x", "authority_domain": "domain-a"}),
    ("scope.authority_domain", {"tenant": "tenant-a", "institution": "inst-a", "authority_domain": "domain-x"}),
])
def test_binding_scope_members_are_compared_with_the_pin_independently(case, member, scope):
    """The Control Plane claim model already refuses a binding whose scope differs from the claim's, so these members cannot be varied through a
    validated claim; the guard still compares them (defence in depth), shown here on the guard function with a stand-in claim."""
    from engine.authorization_pin import PinLookup, check_claim_against_pin
    env = mk_env()
    binding = case.binding()
    payload = case.payload(env, binding)
    case.pins.install(payload)
    row = case.pins.rows("decision-1")[0]
    op = g.snapshot_envelope(env).operation
    claim = types.SimpleNamespace(policy_binding=case.binding(scope=scope), effect_id=env.effect_id, action_id=op.action_id, tenant_id=op.tenant_id,
                                  institution_id=op.institution_id, authority_domain=op.authority_domain, grant_id=op.grant_id,
                                  grant_revision=op.grant_revision)
    lookup = PinLookup(fmt.anchor_decode(case.pins.anchor.read_bytes()), row)
    with pytest.raises(sa.SelectionRefusal) as exc:
        check_claim_against_pin(lookup, claim, case.now)
    assert (exc.value.code, exc.value.refusal_class, exc.value.cause) == ("policy_identity_mismatch", "REFUSE", f"pin_binding_mismatch:{member}")
    claim.policy_binding = binding
    assert check_claim_against_pin(lookup, claim, case.now).pin_scope["operation"] == "op-test"


def test_a_pin_binding_digest_that_differs_from_the_claims_digest_is_refused(tmp_path):
    case = Case(tmp_path)
    env, cid = case.honest(binding_digest=D("7"))
    refused(case.run(env, cid), "policy_identity_mismatch", "REFUSE", "pin_binding_mismatch:binding_digest")


def test_missing_pin_is_refused(case):
    env = mk_env()
    case.pins.fence("decision-1")
    env, cid = case.claim(env)
    refused(case.run(env, cid), "policy_identity_mismatch", "REFUSE", "pin_absent")
    assert case.counts() == (0, 0, 0, 0)


@pytest.mark.parametrize("status", fmt.TERMINAL_STATUSES)
def test_terminal_status_is_refused(case, status):
    env = mk_env()
    payload = case.payload(env)
    case.pins.install(payload)
    case.pins.terminal(payload, status)
    env, cid = case.claim(env)
    refused(case.run(env, cid), "policy_identity_mismatch", "REFUSE", f"pin_status:{status}")
    assert case.counts() == (0, 0, 0, 0)


def test_expired_and_not_yet_valid_pins_are_refused(tmp_path):
    for key, delta, name in (("expires_at", -1, "expired"), ("not_before", +1, "future")):
        case = Case(Path(tmp_path) / name)
        when = (case.now + case._days(delta)).isoformat()
        over = {key: when}
        if key == "expires_at":
            over["not_before"] = (case.now - case._days(9)).isoformat()
        else:
            over["expires_at"] = (case.now + case._days(9)).isoformat()
        env, cid = case.honest(**over)
        refused(case.run(env, cid), "policy_identity_mismatch", "REFUSE", "pin_not_valid_now")
        assert case.counts() == (0, 0, 0, 0)


def test_window_is_half_open_at_the_lock_time_clock(tmp_path):
    case = Case(tmp_path)
    env, cid = case.honest(not_before=case.now.isoformat(), expires_at=(case.now + case._days(1)).isoformat())
    assert case.run(env, cid).status == "executed"           # now == not_before is inside [not_before, expires_at)
    case2 = Case(Path(tmp_path) / "b")
    env, cid = case2.honest(not_before=(case2.now - case2._days(1)).isoformat(), expires_at=case2.now.isoformat())
    refused(case2.run(env, cid), "policy_identity_mismatch", "REFUSE", "pin_not_valid_now")  # now == expires_at is outside


def test_claim_without_a_binding_on_a_pin_required_destination_is_refused(case):
    env = mk_env(tenant_id=None)
    case.pins.fence("decision-1")
    case.pins.install(case.payload(mk_env(), case.binding()))
    env, cid = case.claim(env, binding=None)
    refused(case.run(env, cid), "policy_identity_mismatch", "REFUSE", "pin_required_binding_absent")
    assert case.counts() == (0, 0, 0, 0)


# ================================================================================================================ anchor decision table
def test_anchor_missing_holds_every_decision(case):
    env, cid = case.honest()
    case.pins.anchor.unlink()
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_anchor_unavailable")
    assert case.counts() == (0, 0, 0, 0)


@pytest.mark.parametrize("garbage", [b"", b"not json", b'{"profile":"ocs-s4/pin-anchor/1"}', b"\xff\xfe\x00"])
def test_anchor_corrupt_holds(case, garbage):
    env, cid = case.honest()
    case.pins.anchor.write_bytes(garbage)
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_anchor_unavailable")


def test_anchor_tampered_digest_holds(case):
    env, cid = case.honest()
    anchor = json.loads(case.pins.anchor.read_text())
    anchor["committed_epoch"] = 1
    anchor["anchored_at"] = "2026-10-26T00:00:00Z"            # members edited without recomputing anchor_digest
    case.pins.anchor.write_text(json.dumps(anchor))
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_anchor_unavailable")


def test_anchor_ahead_of_the_store_holds(case):
    env, cid = case.honest()
    case.pins.write_anchor(5, D("5"))
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_anchor_inconsistent")


def test_anchor_head_digest_differs_at_equal_epoch_holds(case):
    env, cid = case.honest()
    case.pins.write_anchor(1, D("5"))
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_anchor_inconsistent")


def test_anchor_behind_the_store_with_an_intact_chain_is_accepted(case):
    env = mk_env()
    case.pins.install(case.payload(env))
    other = case.payload(mk_env("decision-9", "effect-9"))
    case.pins.append(other)                                     # store head 2, anchor still at 1 (stale anchor, ADR 3.6)
    env, cid = case.claim(env)
    result = case.run(env, cid)
    assert result.status == "executed"
    assert result.control_plane_evidence["authority_receipt"]["authorization_pin"]["anchor_committed_epoch"] == 1


def test_anchor_behind_with_a_broken_chain_holds(case):
    env = mk_env()
    case.pins.install(case.payload(env))
    case.pins.append(case.payload(mk_env("decision-9", "effect-9")), prev=D("4"))   # head row does not link to the anchored head
    env, cid = case.claim(env)
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_chain_broken")


def test_anchor_store_id_different_from_the_configuration_holds(case):
    env, cid = case.honest()
    other = PinWriter(case.pins.base / "other", store_id="another-store")
    case.pins.anchor.write_bytes(other.anchor.read_bytes())
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_anchor_inconsistent")


@pytest.mark.parametrize("variant", ["wrong_epoch", "committed_but_other_decision", "far_ahead"])
def test_inconsistent_intent_holds_every_decision(case, variant):
    env = mk_env()
    payload = case.payload(env)
    case.pins.install(payload)                                  # H = C = 1
    epoch, digest = case.pins.head()
    if variant == "wrong_epoch":
        intent = fmt.make_intent(epoch=epoch + 5, decision_id="decision-9", action="terminal", status="revoked", provenance_ref="p")
        case.pins.write_anchor(epoch, digest, intent)
    elif variant == "committed_but_other_decision":
        case.pins.terminal(payload, "revoked")                 # H = 2 committed
        intent = fmt.make_intent(epoch=2, decision_id="decision-9", action="terminal", status="revoked", provenance_ref="p")
        case.pins.write_anchor(1, digest, intent)               # row at H does not match the intent descriptor
    else:
        case.pins.append(case.payload(mk_env("decision-8", "effect-8")))
        case.pins.append(case.payload(mk_env("decision-9", "effect-9")))   # H = 3
        intent = fmt.make_intent(epoch=2, decision_id="decision-9", action="install", status="active", provenance_ref="p")
        case.pins.write_anchor(1, digest, intent)               # H > intent.epoch
    env, cid = case.claim(env)
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_anchor_inconsistent")
    assert case.counts() == (0, 0, 0, 0)


def test_pending_terminal_intent_holds_only_the_covered_decision(case):
    e1, e2 = mk_env(), mk_env("decision-2", "effect-2")
    case.pins.install(case.payload(e1))
    case.pins.install(case.payload(e2))
    case.pins.intent("decision-1")                               # H = C = 2, terminal intent at epoch 3 covers decision-1
    e1, c1 = case.claim(e1)
    e2, c2 = case.claim(e2)
    refused(case.run(e1, c1), "store_unknown", "HOLD", "pin_transition_pending")
    assert case.counts() == (0, 0, 0, 0)
    assert case.run(e2, c2).status == "executed" and case.counts()[0] == 1


def test_pending_terminal_intent_holds_before_touching_the_fence(tmp_path):
    case = Case(tmp_path, dest_cls=Recorder)
    env = mk_env()
    case.pins.install(case.payload(env))
    case.pins.intent("decision-1")
    case.pins.fence_path = fmt.fence_path(case.pins.fences, "decision-1")
    case.pins.fence_path.unlink()                                # fence absent: the lock-free precheck must still decide first
    env, cid = case.claim(env)
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_transition_pending")
    assert not hasattr(case.dest, "stages")                      # no transaction was opened


def test_committed_terminal_row_with_pending_anchor_completion(case):
    """Crash state E: the terminal row is committed, the anchor completion is pending. Section 6.2 steps 0 and 2 HOLD the covered decision on
    the intent alone; the section 7 table's accept (and the row's own refusal) is what the reader and the guard decide beneath that."""
    from engine.authorization_pin import check_claim_against_pin
    e1, e2 = mk_env(), mk_env("decision-2", "effect-2")
    p1 = case.payload(e1)
    case.pins.install(p1)
    case.pins.install(case.payload(e2))                          # C = H = 2
    case.pins.append(p1, "revoked")                              # H = 3, committed under the exclusive fence; anchor completion pending
    intent = fmt.make_intent(epoch=3, decision_id="decision-1", action="terminal", status="revoked", provenance_ref="p")
    case.pins.write_anchor(2, case.pins.rows()[1]["pin_digest"], intent)
    lookup = PinReader(case.cfg).lookup("decision-1")            # the table says accept: the store is consistent and the row refuses its decision
    assert lookup.row["status"] == "revoked"
    op = g.snapshot_envelope(e1).operation
    claim = types.SimpleNamespace(policy_binding=case.binding(), effect_id=e1.effect_id, action_id=op.action_id, tenant_id=op.tenant_id,
                                  institution_id=op.institution_id, authority_domain=op.authority_domain, grant_id=op.grant_id,
                                  grant_revision=op.grant_revision)
    with pytest.raises(sa.SelectionRefusal) as exc:
        check_claim_against_pin(lookup, claim, case.now)
    assert exc.value.cause == "pin_status:revoked"
    e1, c1 = case.claim(e1)
    e2, c2 = case.claim(e2)
    refused(case.run(e1, c1), "store_unknown", "HOLD", "pin_transition_pending")      # destination order: held on the intent
    assert case.counts() == (0, 0, 0, 0)
    assert case.run(e2, c2).status == "executed"                                      # other decisions are admitted


def test_pending_install_intent_is_not_a_barrier_and_the_decision_has_no_pin_yet(case):
    env = mk_env()
    case.pins.fence("decision-1")
    case.pins.intent("decision-1", action="install")
    env, cid = case.claim(env)
    refused(case.run(env, cid), "policy_identity_mismatch", "REFUSE", "pin_absent")


# ================================================================================================================ pin store failures
def test_pin_store_garbage_holds(case):
    env, cid = case.honest()
    case.pins.store.write_bytes(b"this is not a sqlite database" * 100)
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_store_unreadable")
    assert case.counts() == (0, 0, 0, 0)


def test_pin_store_missing_holds(case):
    env, cid = case.honest()
    case.pins.store.unlink()
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_store_unreadable")


def test_pin_store_hot_journal_holds(case):
    env, cid = case.honest()
    sqlite3.connect(case.pins.store, isolation_level=None).execute("CREATE TABLE junk(x TEXT)").connection.close()
    code = ("import sqlite3,sys,os\nc=sqlite3.connect(sys.argv[1],isolation_level=None)\nc.execute('PRAGMA cache_size=1')\nc.execute('BEGIN')\n"
            "for i in range(3000):\n    c.execute('INSERT INTO junk VALUES(?)',('x'*500,))\nos._exit(0)\n")
    subprocess.run([sys.executable, "-I", "-c", code, str(case.pins.store)], check=True, timeout=60)
    assert Path(str(case.pins.store) + "-journal").exists(), "the killed writer must leave a hot journal"
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_store_unreadable")
    assert case.counts() == (0, 0, 0, 0)
    assert Path(str(case.pins.store) + "-journal").exists()      # the read-only executor did not recover (and so did not write) the store


def test_pin_store_wrong_profile_or_missing_tables_hold(case):
    env, cid = case.honest()
    case.pins._sql(f"UPDATE {fmt.META_TABLE} SET value='other/profile' WHERE key='profile'")
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_store_unreadable")
    case.pins._sql(f"UPDATE {fmt.META_TABLE} SET value=? WHERE key='profile'", (fmt.PIN_PROFILE,))
    case.pins._sql(f"UPDATE {fmt.META_TABLE} SET value='another' WHERE key='pin_store_id'")
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_anchor_inconsistent")
    case.pins._sql(f"DROP TABLE {fmt.PIN_TABLE}")
    case.pins._sql(f"UPDATE {fmt.META_TABLE} SET value=? WHERE key='pin_store_id'", (STORE_ID,))
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_store_unreadable")


def test_tampered_row_without_a_recomputed_digest_holds(case):
    env, cid = case.honest()
    case.pins._sql(f"UPDATE {fmt.PIN_TABLE} SET row_json = replace(row_json, 'op-test', 'op-evil')")
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_store_unreadable")


def test_chain_broken_holds(case):
    env = mk_env()
    case.pins.install(case.payload(env))
    case.pins.append(case.payload(mk_env("decision-9", "effect-9")))
    case.pins.append(case.payload(mk_env("decision-8", "effect-8")), prev=D("3"))   # valid row, wrong link; the anchor stays at epoch 1
    env, cid = case.claim(env)
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_chain_broken")


def test_epoch_gap_holds(case):
    env = mk_env()
    case.pins.install(case.payload(env))
    case.pins.append(case.payload(mk_env("decision-9", "effect-9")), epoch=3)     # anchor stays at epoch 1
    env, cid = case.claim(env)
    refused(case.run(env, cid), "store_unknown", "HOLD", "pin_chain_broken")


def test_conflicting_rows_for_a_decision_hold(tmp_path):
    for name, build in (("two_active", lambda c, p: c.pins.append(dict(p, approval_commitment=D("b")))),
                        ("active_after_terminal", None)):
        case = Case(Path(tmp_path) / name)
        env = mk_env()
        p = case.payload(env)
        case.pins.install(p)
        if build:
            build(case, p)
        else:
            case.pins.append(p, "revoked")
            case.pins.append(p, "active")
        case.pins.complete()
        env, cid = case.claim(env)
        refused(case.run(env, cid), "store_unknown", "HOLD", "pin_conflict")
        assert case.counts() == (0, 0, 0, 0)


# ================================================================================================================ fence
def test_fence_file_absent_holds_and_opens_no_transaction(tmp_path):
    case = Case(tmp_path, dest_cls=Recorder)
    env = mk_env()
    case.pins.install(case.payload(env))
    fmt.fence_path(case.pins.fences, "decision-1").unlink()
    env, cid = case.claim(env)
    refused(case.run(env, cid), "verifier_unavailable", "HOLD", "pin_fence_unavailable")
    assert not hasattr(case.dest, "stages") and case.counts() == (0, 0, 0, 0)


def test_fence_that_cannot_be_obtained_holds_and_opens_no_transaction(tmp_path):
    case = Case(tmp_path, deadline=0.3, dest_cls=Recorder)
    env, cid = case.honest()
    holder = subprocess.Popen(
        [sys.executable, "-I", "-c", "import fcntl,os,sys\nfd=os.open(sys.argv[1],os.O_RDONLY)\nfcntl.flock(fd,fcntl.LOCK_EX)\nprint('ready',flush=True)\nsys.stdin.read()\n",
         str(fmt.fence_path(case.pins.fences, "decision-1"))], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "ready"
        started = time.monotonic()
        refused(case.run(env, cid), "verifier_unavailable", "HOLD", "pin_fence_unavailable")
        assert 0.25 <= time.monotonic() - started < 5
        assert not hasattr(case.dest, "stages") and case.counts() == (0, 0, 0, 0)
    finally:
        holder.stdin.close()
        holder.wait(timeout=10)
    assert case.run(env, cid).status == "executed"            # the holder exited (the OS released its lock): the same claim now passes


def test_fence_is_released_after_success_refusal_and_exception(tmp_path):
    case = Case(tmp_path)
    fence = fmt.fence_path(case.pins.fences, "decision-1")
    env, cid = case.honest()
    assert case.run(env, cid).status == "executed" and not held(fence)             # success
    env2, cid2 = case.claim(mk_env("decision-1", "effect-3"), claim_id="second")   # same decision, pin for effect-1: lineage refusal
    refused(case.run(env2, cid2), "policy_identity_mismatch", "REFUSE", "pin_lineage_mismatch:effect_id")
    assert not held(fence)                                                         # refusal

    class Boom(AtomicAuthorityEffectDestination):
        def _transaction_stage(self, stage):
            if stage == "after_selection_guard":
                raise RuntimeError("boom")

    case3 = Case(Path(tmp_path) / "x", dest_cls=Boom)
    env3, cid3 = case3.honest()
    with pytest.raises(RuntimeError):
        case3.dest.execute_claim_atomic(cid3, g.snapshot_envelope(env3), attempt_id="a1", policy_effective_digest=sa.policy_digest_of(g.effect_policy()),
                                        policy_effective_sets=sa.policy_sets_of(g.effect_policy()))
    assert not held(fmt.fence_path(case3.pins.fences, "decision-1")) and case3.counts() == (0, 0, 0, 0)   # exception, nothing committed


def test_lock_order_fence_is_held_before_begin_immediate_and_until_the_end(tmp_path):
    case = Case(tmp_path, dest_cls=Recorder)
    fence = fmt.fence_path(case.pins.fences, "decision-1")
    Recorder.fence_probe = staticmethod(lambda: held(fence))
    try:
        env, cid = case.honest()
        assert case.run(env, cid).status == "executed"
    finally:
        Recorder.fence_probe = None
    stages = case.dest.stages
    names = [s for s, _ in stages]
    assert names.index("before_begin") < names.index("after_begin") < names.index("after_selection_guard") < names.index("after_commit")
    assert all(flag is True for _, flag in stages)               # the shared fence was already held at before_begin and stayed held after commit
    assert not held(fence)


def test_shared_fences_do_not_exclude_each_other(case):
    fence = fmt.fence_path(case.pins.fences, "decision-1")
    case.honest()
    fd = os.open(fence, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)           # another honest in-flight effect
        env, cid = mk_env(), "pin-claim-1"
        assert case.run(env, cid).status == "executed"
        assert held(fence)                                       # the other holder is still there
    finally:
        os.close(fd)


# ================================================================================================================ unchanged downstream checks
def test_f01_containment_still_refuses_a_widened_policy_under_a_valid_pin(case):
    env = mk_env()
    binding = g.binding_of(case.gov.state(), effective_policy_digest=sa.policy_digest_of(g.effect_policy(allowed_actions=frozenset({"act-list", "act-extra"}))))
    case.pins.install(case.payload(env, binding))
    env, cid = case.claim(env, binding)
    wide = g.effect_policy(allowed_actions=frozenset({"act-list", "act-extra"}))
    refused(case.run(env, cid, wide), "policy_identity_mismatch", "REFUSE", "policy_not_subset:allowed_actions")
    assert case.counts() == (0, 0, 0, 0) and not held(fmt.fence_path(case.pins.fences, "decision-1"))


def test_registry_selection_is_asked_about_the_pins_scope(case, monkeypatch):
    store = case.dest._selection_store
    original, calls = store.check_effect, []

    def spy(*args, **kwargs):
        calls.append(kwargs.get("pin_scope"))
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "check_effect", spy)
    env, cid = case.honest()
    assert case.run(env, cid).status == "executed"
    assert calls == [{"consumer_id": "consumer-test", "tenant": "tenant-a", "institution": "inst-a", "authority_domain": "domain-a",
                      "operation": "op-test"}]
    # a claim naming a different operation never reaches the Registry guard at all
    env2 = mk_env("decision-2", "effect-2")
    case.pins.install(case.payload(env2, case.binding()))
    env2, cid2 = case.claim(env2, case.binding(operation="op-other"))
    refused(case.run(env2, cid2), "policy_identity_mismatch", "REFUSE", "pin_binding_mismatch:operation")
    assert len(calls) == 1


def test_check_effect_builds_the_governed_scope_from_pin_scope_not_from_the_binding(case):
    store = case.dest._selection_store
    policy = g.effect_policy()
    forged = case.binding(operation="op-nowhere")                 # a binding naming an operation that has no activation row
    claim = types.SimpleNamespace(policy_binding=forged)
    pin_scope = {"consumer_id": "consumer-test", "tenant": "tenant-a", "institution": "inst-a", "authority_domain": "domain-a", "operation": "op-test"}
    args = (claim, case.now, sa.policy_digest_of(policy), sa.policy_sets_of(policy))
    with case.dest._connect() as conn:
        with pytest.raises(sa.SelectionRefusal) as exc:
            store.check_effect(conn, *args)                       # legacy behaviour: the claim's scope decides
        assert exc.value.code == "not_activated"
        assert store.check_effect(conn, *args, pin_scope=pin_scope)["result"] == "binding_verified"   # pin scope decides
        with pytest.raises(sa.SelectionRefusal) as exc:
            store.check_effect(conn, *args, pin_scope={**pin_scope, "operation": "op-nowhere"})
        assert exc.value.code == "not_activated"
        with pytest.raises(sa.SelectionRefusal) as exc:
            store.check_effect(conn, *args, pin_scope={"operation": "op-test"})
        assert (exc.value.code, exc.value.cause) == ("field_invalid", "pin_scope")
        with pytest.raises(sa.SelectionRefusal) as exc:
            store.check_effect(conn, types.SimpleNamespace(policy_binding=None), case.now, None, None, pin_scope=pin_scope)
        assert (exc.value.code, exc.value.cause) == ("policy_identity_mismatch", "pin_required_binding_absent")


def test_failing_evidence_write_leaves_zero_effects_and_releases_the_fence(tmp_path):
    class Failing(AtomicAuthorityEffectDestination):
        def _selection_receipt_evidence(self, evidence):
            raise OSError("evidence store unavailable")

    case = Case(tmp_path, dest_cls=Failing)
    env, cid = case.honest()
    result = case.run(env, cid)
    assert result.status != "executed" and case.counts() == (0, 0, 0, 0)
    assert not held(fmt.fence_path(case.pins.fences, "decision-1"))


def test_a_pin_required_destination_without_a_selection_store_holds(tmp_path):
    case = Case(tmp_path)
    bare = AtomicAuthorityEffectDestination(case.gov.e.root, clock=lambda: case.now, authorization_pin=case.cfg)   # no selection authority configured
    env, cid = case.honest()
    result = case.executor(dest=bare).execute(envelope=env, claim_id=cid)
    refused(result, "store_unknown", "HOLD", "selection_store_not_configured")
    assert case.counts() == (0, 0, 0, 0)


# ================================================================================================================ no pin writes; constructor-only
PIN_ARTIFACTS = lambda case: [case.pins.store, case.pins.anchor, *case.pins.fences.glob("*.fence")]    # noqa: E731


def snapshot_files(paths):
    out = {}
    for p in paths:
        st = p.stat()
        out[str(p)] = (p.read_bytes(), st.st_mtime_ns, st.st_size)
    return out


def test_provision_claim_cannot_create_or_modify_any_pin_artifact(case, monkeypatch):
    env, cid = case.honest()                    # honest() provisioned through the NON-pin handle; provision through the pin handle now
    before, listing = snapshot_files(PIN_ARTIFACTS(case)), sorted(os.listdir(case.pins.base)) + sorted(os.listdir(case.pins.fences))
    opened, connected = [], []
    real_open, real_connect = os.open, sqlite3.connect

    def spy_open(path, flags, *a, **k):
        opened.append((str(path), flags))
        return real_open(path, flags, *a, **k)

    def spy_connect(database, *a, **k):
        connected.append(str(database))
        return real_connect(database, *a, **k)

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(sqlite3, "connect", spy_connect)
    env2 = mk_env("decision-2", "effect-2")
    raw = g.make_claim(env2, case.now, binding=case.binding(), claim_id="prov-2")
    case.dest.provision_claim(raw)                                 # the pin-required handle itself
    assert snapshot_files(PIN_ARTIFACTS(case)) == before
    assert sorted(os.listdir(case.pins.base)) + sorted(os.listdir(case.pins.fences)) == listing
    pin_paths = {str(case.pins.store), str(case.pins.anchor), str(case.pins.fences)}
    touched = [p for p, _ in opened if any(p.startswith(x) for x in pin_paths)] + [d for d in connected if str(case.pins.base) in d]
    assert touched == []                                           # provisioning does not even open a pin artifact
    # and a full execution only ever opens pin artifacts read-only
    opened.clear(), connected.clear()
    assert case.run(env, cid).status == "executed"
    pin_opens = [(p, f) for p, f in opened if any(p.startswith(x) for x in pin_paths)]
    assert pin_opens and all(f & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND) == 0 for _, f in pin_opens)
    pin_connects = [d for d in connected if str(case.pins.base) in d]
    assert pin_connects and all(d.startswith("file:") and d.endswith("?mode=ro") for d in pin_connects)
    assert snapshot_files(PIN_ARTIFACTS(case)) == before


def test_the_destination_exposes_no_pin_write_function(case):
    names = [n for n in dir(case.dest) if "pin" in n.lower() and not n.startswith("__")]
    assert sorted(n for n in names if not n.startswith("_")) == ["authorization_pin_required"]
    assert not any(verb in n.lower() for n in dir(case.dest) for verb in ("install_pin", "write_pin", "set_pin", "revoke_pin", "write_anchor", "create_fence"))
    assert not any(callable(getattr(PinReader, n)) and n.lstrip("_") in ("write", "install", "append", "commit") for n in dir(PinReader))


def test_authorization_pin_cannot_be_changed_after_construction(case, tmp_path):
    with pytest.raises(AttributeError):
        case.dest.authorization_pin_required = False
    for name in ("_authorization_pin_config", "_pin_reader"):
        with pytest.raises(AttributeError):
            setattr(case.dest, name, None)
    case.dest.authorization_pin = None                              # an unrelated public attribute name has no effect on enforcement
    case.dest.authorization_pin_config = None
    assert case.dest.authorization_pin_required is True
    env = mk_env()
    case.pins.fence("decision-1")
    env, cid = case.claim(env)
    refused(case.run(env, cid), "policy_identity_mismatch", "REFUSE", "pin_absent")        # still enforced
    # and a destination built WITHOUT the option cannot be switched on afterwards
    plain = g.Gov(Path(tmp_path) / "plain")
    plain.dest.authorization_pin = case.cfg
    with pytest.raises(AttributeError):
        plain.dest.authorization_pin_required = True
    assert plain.run()[2].status == "executed"


def test_config_is_frozen_and_validated(case):
    with pytest.raises(dataclasses.FrozenInstanceError):
        case.cfg.pin_store_id = "x"
    for bad in ({"pin_store_id": ""}, {"fence_deadline_seconds": 0}, {"fence_deadline_seconds": float("nan")}, {"pin_store_path": ""}):
        with pytest.raises(ValueError):
            AuthorizationPinConfig(**{**dataclasses.asdict(case.cfg), **bad})


def test_reader_methods_are_read_only_and_map_failures_to_hold(case):
    case.honest()
    reader = PinReader(case.cfg)
    assert reader.read_anchor()["committed_epoch"] == 1
    assert reader.read_head().epoch == 1 and reader.rows_from(1)[0]["decision_id"] == "decision-1"
    assert [r["pin_epoch"] for r in reader.rows_for("decision-1")] == [1] and reader.rows_for("nobody") == []
    with pytest.raises(sa.SelectionRefusal) as exc:
        reader.rows_from(0) if False else PinReader(dataclasses.replace(case.cfg, pin_store_path=case.pins.base / "missing.sqlite3")).read_head()
    assert (exc.value.code, exc.value.refusal_class, exc.value.cause) == ("store_unknown", "HOLD", "pin_store_unreadable")
    with pytest.raises(sqlite3.OperationalError):                   # the read-only connection cannot write even if asked to
        conn = reader._open()
        try:
            conn.execute(f"INSERT INTO {fmt.META_TABLE} VALUES('x','y')")
        finally:
            conn.close()
