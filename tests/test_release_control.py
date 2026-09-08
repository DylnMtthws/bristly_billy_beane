"""Release gates must reject ambiguous or unverified production inputs."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "release_control",
    Path(__file__).resolve().parents[1] / "scripts/release_control.py",
)
assert spec and spec.loader
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def valid_run():
    return {
        "id": 123,
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
        "event": "push",
        "head_branch": "main",
        "path": ".github/workflows/ci.yml",
        "head_sha": "a" * 40,
        "repository": {"id": 1},
        "head_repository": {"id": 1},
    }


@pytest.mark.parametrize(
    "change",
    [
        {"conclusion": "failure"},
        {"status": "in_progress"},
        {"event": "pull_request"},
        {"head_branch": "feature"},
        {"path": ".github/workflows/other.yml"},
        {"head_repository": {"id": 2}},
        {"repository": {"id": 2}},
        {"head_sha": "b" * 40},
    ],
)
def test_provenance_rejects_wrong_or_stale_runs(change):
    with pytest.raises(release.ReleaseError):
        release.validate_provenance(valid_run() | change, "a" * 40, 1)


def test_provenance_accepts_exact_successful_main_run():
    release.validate_provenance(valid_run(), "a" * 40, 1)


def test_bundle_checksum_and_attempt_are_bound(tmp_path, monkeypatch):
    config = tmp_path / "fly.production.toml"
    config.write_text(
        "app='dylnmtthws-decklab'\n[[mounts]]\nsource='decklab_data'\ndestination='/data'\n"
    )
    monkeypatch.setattr(release, "CONFIG", config)
    image = tmp_path / "image.tar.gz"
    image.write_bytes(b"test archive")
    manifest = {
        "schema_version": 1,
        "repository": release.REPOSITORY,
        "sha": "a" * 40,
        "image_id": "sha256:" + "b" * 64,
        "image_sha256": release.file_sha(image),
        "config_sha256": release.file_sha(config),
        "run_id": 123,
        "run_attempt": 1,
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    release.validate_bundle(tmp_path, expected_sha="a" * 40, run=valid_run())
    with pytest.raises(release.ReleaseError):
        release.validate_bundle(tmp_path, run=valid_run() | {"run_attempt": 2})
    image.write_bytes(b"tampered")
    with pytest.raises(release.ReleaseError):
        release.validate_bundle(tmp_path)


def machine():
    return {
        "id": "abc123",
        "state": "started",
        "config": {"mounts": [{"volume": "vol_123", "path": "/data"}]},
    }


def test_single_writer_and_original_volume_required():
    release.validate_machine([machine()], "abc123", "vol_123")
    for machines in (
        [],
        [machine(), machine()],
        [machine() | {"state": "stopped"}],
        [
            machine()
            | {"config": {"mounts": [{"volume": "vol_other", "path": "/data"}]}}
        ],
    ):
        with pytest.raises(release.ReleaseError):
            release.validate_machine(machines, "abc123", "vol_123")


def test_ui_release_scope_excludes_data_and_release_code():
    assert release.release_paths_safe(
        ["src/sabermetrics/ui/static/deck-lab.css", "tests/test_layout.py"]
    )
    for path in (
        "src/sabermetrics/db.py",
        "pyproject.toml",
        "fly.production.toml",
        ".github/workflows/ci.yml",
        "src/sabermetrics/ui/templates/auth/login.html",
        "src/sabermetrics/ui/static/admin.js",
    ):
        assert not release.release_paths_safe([path])
    assert not release.release_paths_safe([])


def test_deploy_refuses_non_manual_and_missing_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    with pytest.raises(release.ReleaseError, match="manual"):
        release.deploy(tmp_path)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_REPOSITORY", release.REPOSITORY)
    monkeypatch.delenv("FLY_API_TOKEN", raising=False)
    with pytest.raises(release.ReleaseError, match="credential"):
        release.deploy(tmp_path)


def test_only_new_completed_snapshot_can_release_backup_gate():
    snapshots = [
        {"id": "vs_old", "status": "created", "created_at": "2026-09-08T00:00:00Z"},
        {"id": "vs_pending", "status": "running", "created_at": "2026-09-09T00:00:00Z"},
        {"id": "vs_unknown", "status": "created"},
    ]
    after = 1788912000.0  # 2026-09-09 UTC
    assert release.completed_snapshot(snapshots, {"vs_old"}, after) is None
    snapshots.append(
        {"id": "vs_new", "status": "created", "created_at": "2026-09-09T00:01:00Z"}
    )
    assert release.completed_snapshot(snapshots, {"vs_old"}, after) == "vs_new"


def test_bootstrap_requires_exact_base_and_narrow_health_change():
    files = [
        {"filename": "scripts/release_control.py", "status": "added"},
        {
            "filename": "Dockerfile",
            "status": "modified",
            "patch": "@@ -1 +1 @@\n+LABEL org.opencontainers.image.revision=$SABER_BUILD_SHA",
        },
    ]
    assert release.bootstrap_safe(files, "a" * 40, "a" * 40)
    assert not release.bootstrap_safe(files, "b" * 40, "a" * 40)
    files.append(
        {
            "filename": "src/sabermetrics/ui/app.py",
            "status": "modified",
            "patch": "@@ -1 +1 @@\n-do_auth()\n+skip_auth()",
        }
    )
    assert not release.bootstrap_safe(files, "a" * 40, "a" * 40)
