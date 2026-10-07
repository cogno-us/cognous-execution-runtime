from argparse import Namespace
from pathlib import Path

from tools import live_openshell_readiness as readiness


def test_immutable_image_digest_validation():
    assert readiness._validate_image_digest("local/refund@sha256:" + "a" * 64)
    assert not readiness._validate_image_digest("local/refund:latest")
    assert not readiness._validate_image_digest("sha256:" + "a" * 64)
    assert not readiness._validate_image_digest("local/refund@sha256:" + "A" * 64)


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
    assert result["live_qualification_executed"] is False
    assert result["read_only_environment_inspection"]["status"] == "unexecuted"
    assert result["qualification_matrix"]["live_confinement_enforcement"] == "unexecuted"
    assert result["safety"]["mutated_sandbox"] is False


def test_authorization_is_required_even_with_other_inputs(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "config.json"
    config.write_text("{}")

    monkeypatch.setattr(readiness, "_git_source_sha", lambda: readiness.ACCEPTED_SOURCE_SHA)
    monkeypatch.setattr(
        readiness,
        "_binary_check",
        lambda *args, **kwargs: {"status": "present", "path": "/synthetic/openshell", "sha256": "0" * 64},
    )
    monkeypatch.setattr(readiness, "_container_runtime", lambda name: {"status": "usable", "path": f"/synthetic/{name}", "command": {"exit_code": 0}})
    monkeypatch.setattr(
        readiness,
        "_inspect_existing_environment",
        lambda **kwargs: {
            "status": "inspected",
            "image": "local/refund@sha256:" + "a" * 64,
            "sandbox_id": "sandbox-17",
        },
    )

    args = Namespace(
        binary="/synthetic/openshell",
        home=str(home),
        config=str(config),
        image="local/refund@sha256:" + "a" * 64,
        authorized_isolated_environment=False,
        output=str(tmp_path / "out.json"),
    )
    result, status = readiness.assess(args)
    assert status == 2
    assert "explicit authorization for an isolated qualification environment was not supplied" in result["blockers"]
    assert result["read_only_environment_inspection"]["status"] == "inspected"


def test_ready_requires_matching_inspected_image(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "config.json"
    config.write_text("{}")
    image = "local/refund@sha256:" + "a" * 64

    monkeypatch.setattr(readiness, "_git_source_sha", lambda: readiness.ACCEPTED_SOURCE_SHA)
    monkeypatch.setattr(
        readiness,
        "_binary_check",
        lambda *args, **kwargs: {"status": "present", "path": "/synthetic/openshell", "sha256": "0" * 64},
    )
    monkeypatch.setattr(readiness, "_container_runtime", lambda name: {"status": "usable", "path": f"/synthetic/{name}", "command": {"exit_code": 0}})
    monkeypatch.setattr(
        readiness,
        "_inspect_existing_environment",
        lambda **kwargs: {
            "status": "inspected",
            "image": image,
            "sandbox_id": "sandbox-17",
            "phase": "Ready",
        },
    )

    args = Namespace(
        binary="/synthetic/openshell",
        home=str(home),
        config=str(config),
        image=image,
        authorized_isolated_environment=True,
        output=str(tmp_path / "out.json"),
    )
    result, status = readiness.assess(args)
    assert status == 0
    assert result["classification"] == "ready"
    assert result["blockers"] == []
    assert result["live_qualification_executed"] is False
