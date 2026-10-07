from argparse import Namespace
import json
from types import SimpleNamespace

import pytest

from tools import live_openshell_readiness as readiness


IMAGE = "local/refund@sha256:" + "a" * 64


def config():
    return SimpleNamespace(
        sandbox_id="sandbox-17",
        sandbox="refund",
        workspace="default",
        image=IMAGE,
        executable=("/usr/bin/env", "-i", "/usr/local/bin/python3"),
        workdir="/var/lib/cognous",
        cpu="1",
        memory="256Mi",
        policy_version=1,
        policy_hash="policy-hash",
        config_revision=7,
        provider_env_revision=3,
        policy_json=json.dumps(
            {
                "version": 1,
                "filesystem_policy": {
                    "include_workdir": False,
                    "read_only": ["/usr", "/opt/cognous"],
                    "read_write": ["/var/lib/cognous"],
                },
                "landlock": {"compatibility": "hard_requirement"},
                "process": {"run_as_user": "1000", "run_as_group": "1000"},
                "network_policies": {},
            },
            sort_keys=True,
        ),
        digest="sha256:config",
    )


def observed(c=None):
    c = c or config()
    return {
        "id": c.sandbox_id,
        "name": c.sandbox,
        "workspace": c.workspace,
        "phase": "Ready",
        "current_policy_version": c.policy_version,
        "policy_source": "sandbox",
        "revision": c.policy_version,
        "policy": json.loads(c.policy_json),
        "configuration_admission": {
            "state": "accepted",
            "policy_version": c.policy_version,
            "policy_hash": c.policy_hash,
            "config_revision": c.config_revision,
            "provider_env_revision": c.provider_env_revision,
        },
        "runtime_evidence": {
            "version": "0.1.2",
            "status": "healthy",
            "server": "https://127.0.0.1:17670",
            "compute_drivers": [{"capabilities": {"driver_name": "docker"}}],
        },
    }


def base_args(tmp_path, *, authorized=True):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    cfg = tmp_path / "config.json"
    cfg.write_text("{}")
    return Namespace(
        binary="/synthetic/openshell",
        home=str(home),
        config=str(cfg),
        image=IMAGE,
        authorized_isolated_environment=authorized,
        output=str(tmp_path / "out.json"),
    )


def install_local_prerequisite_mocks(monkeypatch):
    monkeypatch.setattr(readiness, "_git_source_sha", lambda: readiness.ACCEPTED_SOURCE_SHA)
    monkeypatch.setattr(
        readiness,
        "_binary_check",
        lambda *args, **kwargs: {
            "status": "present",
            "path": "/synthetic/openshell",
            "sha256": "0" * 64,
        },
    )
    monkeypatch.setattr(
        readiness,
        "_container_runtime",
        lambda name: {
            "status": "usable" if name == "docker" else "missing",
            "path": f"/synthetic/{name}" if name == "docker" else None,
            "command": {"exit_code": 0} if name == "docker" else None,
        },
    )


def test_immutable_image_digest_validation():
    assert readiness._validate_image_digest(IMAGE)
    assert not readiness._validate_image_digest("local/refund:latest")
    assert not readiness._validate_image_digest("sha256:" + "a" * 64)
    assert not readiness._validate_image_digest("local/refund@sha256:" + "A" * 64)


@pytest.mark.parametrize("field,value", [
    ("id", "replacement"),
    ("id", None),
    ("name", "other"),
    ("workspace", "other"),
    ("phase", "Stopped"),
    ("policy_source", "global"),
    ("current_policy_version", 2),
    ("revision", 2),
])
def test_identity_readiness_and_policy_metadata_mismatch_rejected(field, value):
    c = config()
    info = observed(c)
    if value is None:
        info.pop(field)
    else:
        info[field] = value
    with pytest.raises(PermissionError, match="identity, readiness or policy"):
        readiness._validate_inspected_environment(c, info)


def test_policy_content_mismatch_rejected():
    c = config()
    info = observed(c)
    info["policy"]["network_policies"] = {"unexpected": {}}
    with pytest.raises(PermissionError, match="identity, readiness or policy"):
        readiness._validate_inspected_environment(c, info)


@pytest.mark.parametrize("mutation", [
    "missing",
    "rejected",
    "policy_version",
    "policy_hash",
    "config_revision",
    "provider_env_revision",
])
def test_configuration_admission_mismatch_rejected(mutation):
    c = config()
    info = observed(c)
    if mutation == "missing":
        info.pop("configuration_admission")
    elif mutation == "rejected":
        info["configuration_admission"]["state"] = "rejected"
    else:
        info["configuration_admission"][mutation] = (
            "different" if mutation == "policy_hash"
            else info["configuration_admission"][mutation] + 1
        )
    with pytest.raises(PermissionError, match="pinned admitted revision"):
        readiness._validate_inspected_environment(c, info)


def test_valid_matching_inspection_separates_configured_and_observed_evidence():
    c = config()
    result = readiness._validate_inspected_environment(c, observed(c))

    configured = result["configured_or_provisioning_attributed"]
    live = result["observed"]
    validation = result["validation"]

    assert configured["image"] == IMAGE
    assert configured["cpu"] == "1"
    assert configured["memory"] == "256Mi"
    assert live["sandbox_id"] == c.sandbox_id
    assert live["phase"] == "Ready"
    assert "image" not in live
    assert "cpu" not in live
    assert "memory" not in live
    assert "image_identity" in validation["not_independently_observed_by_pinned_api"]
    assert validation["adapter_semantics_matched"] is True


def test_missing_prerequisites_are_blocked_without_inspection(monkeypatch, tmp_path):
    monkeypatch.setattr(readiness, "_git_source_sha", lambda: readiness.ACCEPTED_SOURCE_SHA)
    monkeypatch.setattr(readiness, "_binary_check", lambda *args, **kwargs: {"status": "missing", "path": None})
    monkeypatch.setattr(readiness, "_container_runtime", lambda name: {"status": "missing", "path": None})

    args = Namespace(
        binary=None,
        home=None,
        config=None,
        image=None,
        authorized_isolated_environment=False,
        output=str(tmp_path / "out.json"),
    )
    result, status = readiness.assess(args)
    assert status == 2
    assert result["classification"] == "blocked"
    assert result["read_only_environment_inspection"]["status"] == "unexecuted"
    assert result["qualification_matrix"]["live_confinement_enforcement"] == "unexecuted"


def test_no_gateway_inspection_without_explicit_opt_in(monkeypatch, tmp_path):
    install_local_prerequisite_mocks(monkeypatch)
    calls = []
    monkeypatch.setattr(
        readiness,
        "_inspect_existing_environment",
        lambda **kwargs: calls.append(kwargs) or {"status": "inspected_and_matched"},
    )

    result, status = readiness.assess(base_args(tmp_path, authorized=False))
    assert status == 2
    assert result["classification"] == "blocked"
    assert calls == []
    assert result["read_only_environment_inspection"]["status"] == "unexecuted"
    assert "explicit authorization for an isolated qualification environment was not supplied" in result["blockers"]


def test_valid_matching_inspection_can_be_ready_without_overclaiming(monkeypatch, tmp_path):
    install_local_prerequisite_mocks(monkeypatch)
    c = config()
    inspection = {
        "status": "inspected_and_matched",
        **readiness._validate_inspected_environment(c, observed(c)),
    }
    monkeypatch.setattr(readiness, "_inspect_existing_environment", lambda **kwargs: inspection)

    result, status = readiness.assess(base_args(tmp_path, authorized=True))
    assert status == 0
    assert result["classification"] == "ready"
    assert result["blockers"] == []
    assert result["live_qualification_executed"] is False
    assert (
        result["read_only_environment_inspection"]
        ["configured_or_provisioning_attributed"]["image"]
        == IMAGE
    )
    assert "actual sandbox image identity independently observed by OpenShell" in result["readiness_meaning"]["ready_does_not_establish"]


def test_configured_image_mismatch_blocks_without_calling_it_observed(monkeypatch, tmp_path):
    install_local_prerequisite_mocks(monkeypatch)
    c = config()
    c.image = "local/refund@sha256:" + "b" * 64
    inspection = {
        "status": "inspected_and_matched",
        **readiness._validate_inspected_environment(c, observed(c)),
    }
    monkeypatch.setattr(readiness, "_inspect_existing_environment", lambda **kwargs: inspection)

    result, status = readiness.assess(base_args(tmp_path, authorized=True))
    assert status == 2
    assert result["classification"] == "blocked"
    assert (
        "supplied image digest does not match configured/provisioning-attributed image"
        in result["blockers"]
    )
    assert "image" not in result["read_only_environment_inspection"]["observed"]
