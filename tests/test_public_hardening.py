"""W7 public-mode security boundary, including every mutating route."""

from __future__ import annotations

import pytest
from flask import jsonify, url_for

from sabermetrics.ui.app import create_app
from sabermetrics.ui.extensions import client_ip
from scripts.setup_db import setup_database


def _public_app(tmp_path, monkeypatch):
    db_path = tmp_path / "public.db"
    setup_database(db_path)
    monkeypatch.setenv("SABER_PUBLIC", "1")
    monkeypatch.setenv("SABER_AUTH_MODE", "hybrid")
    monkeypatch.setenv("SABER_SECRET_KEY", "stable-test-secret" * 4)
    monkeypatch.setenv("MTG_V1_DSN", "postgresql://mtg_consumer@db.invalid/mtg")
    monkeypatch.setenv("CEDH_SIMULATOR_URL", "http://sim.invalid:8080")
    monkeypatch.setenv("SABER_TRUSTED_PROXY", "*")
    app = create_app(db_path)
    app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    return app


@pytest.mark.parametrize("variable", ["MTG_V1_DSN", "CEDH_SIMULATOR_URL"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_public_startup_requires_production_endpoints(
    tmp_path, monkeypatch, variable, value
):
    monkeypatch.setenv("SABER_PUBLIC", "1")
    monkeypatch.setenv("SABER_SECRET_KEY", "stable-test-secret")
    monkeypatch.setenv("MTG_V1_DSN", "postgresql://mtg_consumer@db.invalid/mtg")
    monkeypatch.setenv("CEDH_SIMULATOR_URL", "http://sim.invalid:8080")
    if value is None:
        monkeypatch.delenv(variable)
    else:
        monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError, match=variable + " must be set.*Set it"):
        create_app(tmp_path / "app.db")


def test_private_startup_allows_missing_production_endpoints(tmp_path, monkeypatch):
    for variable in ("SABER_PUBLIC", "MTG_V1_DSN", "CEDH_SIMULATOR_URL"):
        monkeypatch.delenv(variable, raising=False)
    app = create_app(tmp_path / "app.db")
    assert app.config["PUBLIC_DEPLOYMENT"] is False
    assert app.test_client().get("/healthz").status_code == 200


def test_public_cookie_is_secure_http_only_and_same_site_lax(tmp_path, monkeypatch):
    app = _public_app(tmp_path, monkeypatch)
    cookie = app.test_client().get("/login").headers["Set-Cookie"]
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_public_response_sends_hsts(tmp_path, monkeypatch):
    app = _public_app(tmp_path, monkeypatch)
    response = app.test_client().get("/login")
    assert response.headers["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains"
    )


def test_csrf_rejects_every_post_route_including_build_jobs(tmp_path, monkeypatch):
    app = _public_app(tmp_path, monkeypatch)
    post_urls = []
    with app.test_request_context():
        for rule in app.url_map.iter_rules():
            if "POST" not in rule.methods:
                continue
            values = {argument: "test-id" for argument in rule.arguments}
            post_urls.append(url_for(rule.endpoint, **values))

    assert "/lab/build" in post_urls
    client = app.test_client()
    for path in post_urls:
        response = client.post(path)
        assert response.status_code == 400, path


def test_rate_limit_key_uses_the_proxy_supplied_client_ip(tmp_path, monkeypatch):
    app = _public_app(tmp_path, monkeypatch)

    @app.get("/_test/client-ip")
    def show_client_ip():
        return jsonify(ip=client_ip())

    response = app.test_client().get(
        "/_test/client-ip", headers={"X-Forwarded-For": "203.0.113.77"}
    )
    assert response.json == {"ip": "203.0.113.77"}
