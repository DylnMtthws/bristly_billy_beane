"""Progressive Research loading, fragment privacy, and browser event races."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.research import ResearchRepo
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

STATIC = Path(__file__).parents[1] / "src/sabermetrics/ui/static"


def _database(tmp_path):
    path = tmp_path / "research-loading.db"
    setup_database(path)
    user = db.UsersRepo(path).create(
        email="loading@example.test",
        display_name="Loading",
        role="user",
        status="active",
    )
    with db.connect(path) as conn:
        conn.execute(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,color_identity,is_legal_commander,is_legal_in_99)
            VALUES('commander','oracle','Loading Commander',2,'Legendary Creature','[]',1,1)"""
        )
        conn.commit()
    return path, user


def _app_client(path, user, monkeypatch):
    monkeypatch.setenv("SABER_SKIP_DOTENV", "1")
    monkeypatch.setenv("SABER_RESEARCH_SYNC", "0")
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    monkeypatch.setenv("SABER_BUILD_SHA", "a" * 40)
    app = create_app(path)
    app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = user
        session["_fresh"] = True
    return app, client


def _login(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True
    return client


def _wait_fragment(client, timeout=5.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(
            "/research/",
            headers={
                "X-Research-Fragment": "1",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        if last.status_code == 200 and b"dl-research-table" in last.data:
            return last
        time.sleep(0.05)
    raise AssertionError(
        f"default research fragment was not ready: {getattr(last, 'status_code', None)}"
    )


def test_initial_html_does_not_call_slow_loader_inline(tmp_path, monkeypatch):
    path, user = _database(tmp_path)
    started = threading.Event()
    release = threading.Event()
    original = ResearchRepo.commanders

    def blocked(self, **kwargs):
        started.set()
        assert release.wait(5)
        return original(self, **kwargs)

    monkeypatch.setattr(ResearchRepo, "commanders", blocked)
    _app, client = _app_client(path, user, monkeypatch)
    assert started.wait(2)
    start = time.perf_counter()
    response = client.get("/research/")
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert response.status_code == 200
    assert elapsed_ms < 1500
    assert b'id="research-filters"' in response.data
    assert b"Research type" in response.data
    assert b'data-research-freshness="pending"' in response.data
    assert b"Load results" in response.data
    assert b"results=full" in response.data
    assert b"Loading Commander" not in response.data
    fragment = client.get(
        "/research/",
        headers={
            "X-Research-Fragment": "1",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    assert fragment.status_code == 202
    assert fragment.headers["Cache-Control"] == "private, no-store"
    assert fragment.headers["X-Research-Freshness"] == "pending"
    release.set()


def test_prepared_snapshot_serves_default_and_meta_without_reload(
    tmp_path, monkeypatch
):
    path, user = _database(tmp_path)
    calls: list[dict] = []
    original = ResearchRepo.commanders

    def wrapped(self, **kwargs):
        calls.append(kwargs)
        return original(self, **kwargs)

    monkeypatch.setattr(ResearchRepo, "commanders", wrapped)
    _app, client = _app_client(path, user, monkeypatch)
    fragment = _wait_fragment(client)
    assert fragment.headers["Cache-Control"] == "private, no-store"
    assert fragment.headers["X-Research-Freshness"] == "fresh"
    assert b"Loading Commander" in fragment.data
    baseline = len(calls)
    first = client.get("/research/")
    meta = client.get("/research/?tab=metagame")
    assert first.status_code == meta.status_code == 200
    assert first.headers["Cache-Control"] == "private, no-store"
    assert b"Loading Commander" in first.data
    assert b"Loading Commander" in meta.data
    assert len(calls) == baseline
    assert client.get("/research/?tab=metagame&q=missing").status_code == 200
    assert len(calls) == baseline + 1
    assert client.get("/research/?tab=metagame&window=30").status_code == 200
    assert len(calls) == baseline + 2
    assert client.get("/research/?tab=metagame&favorites=1").status_code == 200
    assert len(calls) == baseline + 3
    assert client.get("/research/?tab=metagame&sort=name").status_code == 200
    assert len(calls) == baseline + 4
    assert client.get("/research/?tab=metagame&color=U").status_code == 200
    assert len(calls) == baseline + 5
    assert client.get("/research/?window=90").status_code == 200
    assert len(calls) == baseline + 5


def test_two_users_cannot_inherit_favorites_or_fragment_html(tmp_path, monkeypatch):
    path, alice = _database(tmp_path)
    app, alice_client = _app_client(path, alice, monkeypatch)
    _wait_fragment(alice_client)
    bob = db.UsersRepo(path).create(
        email="bob-load@example.test",
        display_name="Bob Loading",
        role="user",
        status="active",
    )
    db.FavoritesRepo(path).toggle_commander(alice, "commander")
    bob_client = _login(app, bob)
    alice_page = alice_client.get("/research/")
    bob_page = bob_client.get("/research/?tab=metagame")
    assert alice_page.status_code == bob_page.status_code == 200
    assert b"loading@example.test" in alice_page.data
    assert b"bob-load@example.test" in bob_page.data
    assert b"bob-load@example.test" not in alice_page.data
    assert b"loading@example.test" not in bob_page.data
    assert b"dl-icon-button active" in alice_page.data
    assert b"dl-icon-button active" not in bob_page.data
    token = re.compile(rb'csrf-token" content="([^"]+)"')
    alice_csrf = token.search(alice_page.data)
    bob_csrf = token.search(bob_page.data)
    assert alice_csrf and bob_csrf
    assert alice_csrf.group(1) != bob_csrf.group(1)
    alice_frag = alice_client.get(
        "/research/",
        headers={"X-Research-Fragment": "1", "X-Requested-With": "XMLHttpRequest"},
    )
    bob_frag = bob_client.get(
        "/research/?tab=metagame",
        headers={"X-Research-Fragment": "1", "X-Requested-With": "XMLHttpRequest"},
    )
    assert alice_frag.headers["Cache-Control"] == "private, no-store"
    assert bob_frag.headers["Cache-Control"] == "private, no-store"
    assert b"dl-icon-button active" in alice_frag.data
    assert b"dl-icon-button active" not in bob_frag.data
    snapshot = app.extensions["research_default_cache"].snapshot_path.read_text()
    assert "loading@example.test" not in snapshot
    assert "bob-load@example.test" not in snapshot
    assert "csrf" not in snapshot.lower()
    assert "favorited" not in snapshot


def test_anonymous_fragment_is_auth_not_results(tmp_path, monkeypatch):
    path, user = _database(tmp_path)
    app, _client = _app_client(path, user, monkeypatch)
    anon = app.test_client()
    response = anon.get(
        "/research/",
        headers={
            "X-Research-Fragment": "1",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    assert response.status_code == 401
    assert response.get_json()["error"] == "authentication required"


def test_full_results_computes_when_snapshot_missing(tmp_path, monkeypatch):
    path, user = _database(tmp_path)
    started = threading.Event()
    release = threading.Event()
    original = ResearchRepo.commanders

    def blocked(self, **kwargs):
        started.set()
        assert release.wait(5)
        return original(self, **kwargs)

    monkeypatch.setattr(ResearchRepo, "commanders", blocked)
    _app, client = _app_client(path, user, monkeypatch)
    assert started.wait(2)
    pending = client.get("/research/")
    assert b'data-research-freshness="pending"' in pending.data
    release.set()
    full = client.get("/research/?results=full")
    assert full.status_code == 200
    assert b"Loading Commander" in full.data
    assert b"research-filters" in full.data


def test_healthz_does_not_wait_for_research_warm(tmp_path, monkeypatch):
    path, user = _database(tmp_path)
    started = threading.Event()
    release = threading.Event()
    original = ResearchRepo.commanders

    def blocked(self, **kwargs):
        started.set()
        assert release.wait(5)
        return original(self, **kwargs)

    monkeypatch.setattr(ResearchRepo, "commanders", blocked)
    app, client = _app_client(path, user, monkeypatch)
    start = time.perf_counter()
    health = client.get("/healthz")
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert health.status_code == 200
    assert health.get_json()["status"] == "ok"
    assert elapsed_ms < 500
    release.set()
    app.extensions["research_default_cache"].wait_for_idle(3)


def test_research_browser_races_failure_history_and_nojs():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for Research progressive-loading checks")
    result = subprocess.run(
        [
            node,
            str(Path(__file__).with_name("research_loading_harness.js")),
            str(STATIC / "deck-lab-research.js"),
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or "research js harness failed")
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["passed"]
