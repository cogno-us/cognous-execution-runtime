import pytest
pytest.importorskip("agent_control_plane.local_authority_effect", reason="requires accepted W1 Control Plane atomic contract")
"""Actual accepted W1 authority-store rows through the new read-only exporter."""
import sqlite3
import pytest

from engine.c8_source_evidence import export_authority_rows
from test_local_authority_effect import setup_tenant_atomic

def test_actual_tenant_authority_export(tmp_path):
    _, destination, claim=setup_tenant_atomic(tmp_path)
    exported=export_authority_rows(destination.path,claim_id=claim.claim_id)
    assert exported["status"]=="source_rows_retained"
    assert exported["rows"]["execution_claims_v1"][0]["tenant_id"]==claim.tenant_id
    assert exported["rows"]["authority_grants_v1"]
    assert exported["rows"]["authority_approvals_v1"]
    assert exported["rows"]["authority_policies_v1"]
    assert exported["rows"]["authority_approvals_v1"][0]["proposal_commitment"]==claim.proposal_commitment

def test_actual_mismatched_tenant_is_preserved_not_corrected(tmp_path):
    _, destination, claim=setup_tenant_atomic(tmp_path)
    with sqlite3.connect(destination.path) as db:
        db.execute("UPDATE authority_approvals_v1 SET tenant_id='tenant-beta'")
    exported=export_authority_rows(destination.path,claim_id=claim.claim_id)
    assert exported["rows"]["authority_approvals_v1"][0]["tenant_id"]=="tenant-beta"
    assert exported["rows"]["execution_claims_v1"][0]["tenant_id"]!= "tenant-beta"
