"""Hosting configuration remains local by default and explicit in containers."""

from pathlib import Path

import pytest

from sabermetrics.cedh.cost_ledger import CostLedger
from sabermetrics.cedh.settings import load_cedh_settings
from sabermetrics.config import resolve_db_path
from sabermetrics.ui.app import create_app, run_server


def _capture_server(monkeypatch, **kwargs):
    captured = {}
    monkeypatch.setattr("waitress.serve", lambda app, **kw: captured.update(kw))
    run_server(**kwargs)
    return captured


def test_default_server_configuration_is_local(monkeypatch, tmp_path):
    captured = _capture_server(monkeypatch, db_path=tmp_path / "app.db")
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 5000
    assert captured["trusted_proxy"] == "127.0.0.1"


def test_server_configuration_reads_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("SABER_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("SABER_PORT", "8080")
    monkeypatch.setenv("SABER_TRUSTED_PROXY", "*")
    captured = _capture_server(monkeypatch, db_path=tmp_path / "app.db")
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 8080
    assert captured["trusted_proxy"] == "*"
    assert captured["trusted_proxy_count"] == 1


def test_public_tailscale_auth_on_public_bind_is_fatal(monkeypatch):
    monkeypatch.setenv("SABER_PUBLIC", "1")
    monkeypatch.setenv("SABER_AUTH_MODE", "tailscale")
    monkeypatch.setenv("SABER_BIND_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="use hybrid auth"):
        run_server()


def test_shared_database_path_resolver(monkeypatch):
    monkeypatch.setenv("SABER_DB_PATH", "/data/test.db")
    assert resolve_db_path() == Path("/data/test.db")
    assert CostLedger().db_path == Path("/data/test.db")
    assert create_app().config["DB_PATH"] == Path("/data/test.db")


def test_simulator_environment_overrides_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv("CEDH_SIMULATOR_URL", "http://sim.internal:8080")
    monkeypatch.setenv("CEDH_SIMULATOR_TIMEOUT", "197")
    settings = load_cedh_settings(tmp_path / "missing.yaml")
    assert settings.simulator.mode == "http"
    assert settings.simulator.url == "http://sim.internal:8080"
    assert settings.simulator.timeout_seconds == 197


@pytest.mark.parametrize("build_sha", [None, "a" * 40])
def test_healthz_is_public_and_reports_version(tmp_path, monkeypatch, build_sha):
    if build_sha is None:
        monkeypatch.delenv("SABER_BUILD_SHA", raising=False)
    else:
        monkeypatch.setenv("SABER_BUILD_SHA", build_sha)
    app = create_app(tmp_path / "missing.db")
    response = app.test_client().get("/healthz")
    assert response.status_code == 200
    assert response.json == {
        "status": "ok",
        "version": "0.1.0",
        "build_sha": build_sha or "unknown",
    }
