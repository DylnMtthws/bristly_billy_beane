"""Navigation budgets cover database reuse, isolation and browser asset caching."""

import json
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest
from flask import Flask, request, session, url_for

from sabermetrics import db
from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.research import ResearchRepo
from sabermetrics.ui import navigation
from sabermetrics.ui.app import create_app
from sabermetrics.ui.navigation import ResearchLandingCache
from scripts.setup_db import setup_database

STATIC = Path(__file__).parents[1] / "src/sabermetrics/ui/static"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SABER_SKIP_DOTENV", "1")
    monkeypatch.setenv("SABER_RESEARCH_SYNC", "0")
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    monkeypatch.setenv("SABER_BUILD_SHA", "a" * 40)
    path = tmp_path / "navigation.db"
    setup_database(path)
    user = db.UsersRepo(path).create(
        email="navigation@example.test",
        display_name="Navigation",
        role="user",
        status="active",
    )
    with db.connect(path) as conn:
        conn.execute(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,color_identity,is_legal_commander,is_legal_in_99)
            VALUES('commander','oracle','Navigation Commander',2,'Legendary Creature','[]',1,1)"""
        )
        conn.commit()
    app = create_app(path)
    app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = user
        session["_fresh"] = True
    return client


def _track_commanders(monkeypatch):
    original = ResearchRepo.commanders
    calls: list[dict] = []

    def wrapped(self, **kwargs):
        calls.append(kwargs)
        return original(self, **kwargs)

    monkeypatch.setattr(ResearchRepo, "commanders", wrapped)
    return calls


def _login(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True
    return client


def test_meta_reuses_default_cohort_but_not_user_favorites(client, monkeypatch):
    calls = _track_commanders(monkeypatch)
    favorites = Mock(return_value={"commander"})
    monkeypatch.setattr(db.FavoritesRepo, "commander_ids", favorites)
    first = client.get("/research/")
    assert first.status_code == 200
    assert b"dl-icon-button active" in first.data
    favorites.return_value = set()
    response = client.get("/research/?tab=metagame")
    assert response.status_code == 200
    assert b"dl-icon-button active" not in response.data
    # The expensive cohort query must not run again when selecting default Meta.
    assert len(calls) == 1
    assert "favorites" not in calls[0]
    assert client.get("/research/?tab=metagame&q=missing").status_code == 200
    assert len(calls) == 2
    assert client.get("/research/?tab=metagame&window=30").status_code == 200
    assert len(calls) == 3
    assert client.get("/research/?tab=metagame&favorites=1").status_code == 200
    assert len(calls) == 4
    assert client.get("/research/?tab=metagame&sort=name").status_code == 200
    assert len(calls) == 5
    assert client.get("/research/?tab=metagame&color=U").status_code == 200
    assert len(calls) == 6
    assert client.get("/research/?window=90").status_code == 200
    assert len(calls) == 6


def test_meta_then_commanders_still_uses_one_cohort_query(client, monkeypatch):
    calls = _track_commanders(monkeypatch)
    assert client.get("/research/?tab=metagame").status_code == 200
    assert client.get("/research/").status_code == 200
    assert len(calls) == 1


def test_two_users_cannot_inherit_favorites_or_account_html(client):
    path = Path(client.application.config["DB_PATH"])
    with client.session_transaction() as session:
        alice = session["_user_id"]
    bob = db.UsersRepo(path).create(
        email="bob-nav@example.test",
        display_name="Bob Navigation",
        role="user",
        status="active",
    )
    db.FavoritesRepo(path).toggle_commander(alice, "commander")
    alice_client = client
    bob_client = _login(client.application, bob)
    alice_page = alice_client.get("/research/")
    bob_page = bob_client.get("/research/?tab=metagame")
    assert alice_page.status_code == bob_page.status_code == 200
    assert b"navigation@example.test" in alice_page.data
    assert b"bob-nav@example.test" in bob_page.data
    assert b"bob-nav@example.test" not in alice_page.data
    assert b"navigation@example.test" not in bob_page.data
    assert b"Bob Navigation" not in alice_page.data
    assert b"dl-icon-button active" in alice_page.data
    assert b"dl-icon-button active" not in bob_page.data
    token = re.compile(rb'csrf-token" content="([^"]+)"')
    alice_csrf = token.search(alice_page.data)
    bob_csrf = token.search(bob_page.data)
    assert alice_csrf and bob_csrf
    assert alice_csrf.group(1) != bob_csrf.group(1)


def test_build_lists_only_the_owner_editable_decks(client):
    path = Path(client.application.config["DB_PATH"])
    with client.session_transaction() as session:
        alice = session["_user_id"]
    bob = db.UsersRepo(path).create(
        email="builder-b@example.test",
        display_name="Builder B",
        role="user",
        status="active",
    )
    repo = DeckDocumentRepo(path)
    repo.create(alice, title="Alice Editable")
    repo.create(bob, title="Bob Editable")
    alice_library = client.get("/build")
    bob_library = _login(client.application, bob).get("/build")
    assert b"Alice Editable" in alice_library.data
    assert b"Bob Editable" not in alice_library.data
    assert b"Bob Editable" in bob_library.data
    assert b"Alice Editable" not in bob_library.data
    assert b'name="q"' in alice_library.data
    assert b"Favorites" in alice_library.data


def test_cache_invalidates_on_committed_wal_update_and_expiry(tmp_path, monkeypatch):
    path = tmp_path / "cache.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE sample(value)")
    conn.commit()
    cache = ResearchLandingCache()
    now = [100.0]
    monkeypatch.setattr(navigation, "monotonic", lambda: now[0])
    load = Mock(return_value={"results": [{"name": "before"}]})
    cache.get(path, load)["results"][0]["name"] = "mutated"
    assert cache.get(path, load)["results"][0]["name"] == "before"
    assert load.call_count == 1
    conn.execute("INSERT INTO sample VALUES(1)")
    conn.commit()
    cache.get(path, load)
    assert load.call_count == 2
    now[0] += 31
    cache.get(path, load)
    assert load.call_count == 3
    conn.close()


def test_cache_coalesces_concurrent_loads_and_does_not_cache_errors(tmp_path):
    path = tmp_path / "cache.db"
    path.touch()
    cache = ResearchLandingCache()
    fail = Mock(side_effect=ValueError("unavailable"))
    with pytest.raises(ValueError):
        cache.get(path, fail)
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow():
        calls.append(1)
        started.set()
        assert release.wait(2)
        return {"results": [{"ok": True}]}

    with ThreadPoolExecutor(max_workers=8) as executor:
        first = executor.submit(cache.get, path, slow)
        assert started.wait(2)
        rest = [executor.submit(cache.get, path, slow) for _ in range(7)]
        release.set()
        results = [first.result(timeout=2)] + [item.result(timeout=2) for item in rest]
    assert len(calls) == 1
    assert len(results) == 8
    results[0]["results"][0]["ok"] = False
    assert cache.get(path, slow)["results"][0]["ok"] is True


def test_failed_snapshot_is_not_retained(tmp_path):
    path = tmp_path / "cache.db"
    path.touch()
    cache = ResearchLandingCache()

    class Uncopyable:
        def __deepcopy__(self, memo):
            raise RuntimeError("cannot copy")

    with pytest.raises(RuntimeError):
        cache.get(path, lambda: {"results": [Uncopyable()]})
    load = Mock(return_value={"results": []})
    assert cache.get(path, load) == {"results": []}
    assert load.call_count == 1


def test_failed_http_cohort_fill_is_retried(client, monkeypatch):
    original = ResearchRepo.commanders
    calls = []

    def flaky(self, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("unavailable")
        return original(self, **kwargs)

    monkeypatch.setattr(ResearchRepo, "commanders", flaky)
    with pytest.raises(RuntimeError):
        client.get("/research/")
    assert client.get("/research/?tab=metagame").status_code == 200
    assert len(calls) == 2


def test_build_and_profile_avoid_retired_repositories(client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Navigation queried a retired or research dependency")

    monkeypatch.setattr(db.DecksRepo, "list_for_owner", forbidden)
    monkeypatch.setattr(db.CedhCandidatesRepo, "list_for_owner", forbidden)
    monkeypatch.setattr(db.CedhCandidatesRepo, "get", forbidden)
    monkeypatch.setattr(db.BuildJobsRepo, "get", forbidden)
    monkeypatch.setattr(ResearchRepo, "commanders", forbidden)
    for path in ("/build", "/profile"):
        response = client.get(path)
        assert response.status_code == 200
        assert b"data-navigation-status" in response.data
        assert b'aria-live="polite"' in response.data
    profile = client.get("/profile")
    assert b"navigation@example.test" in profile.data
    assert b'name="display_name"' in profile.data
    assert b'name="new_password"' in profile.data
    assert b'name="csrf_token"' in profile.data


def test_versioned_assets_cache_without_caching_account_html(client):
    app = client.application
    with app.test_request_context():
        asset = url_for("static", filename="deck-lab.css")
    assert "v=" + "a" * 40 in asset
    response = client.get(asset)
    assert response.cache_control.public
    assert response.cache_control.max_age == 31536000
    assert response.cache_control.immutable
    assert "Set-Cookie" not in response.headers
    not_modified = client.get(
        asset, headers={"If-None-Match": response.headers["ETag"]}
    )
    assert not_modified.status_code == 304
    assert not_modified.cache_control.immutable
    unversioned = client.get("/static/deck-lab.css")
    assert unversioned.headers.get("Cache-Control") == "no-cache"
    for path in (
        "/static/deck-lab.css?v=old",
        "/profile",
        "/build",
        "/static/missing.js?v=" + "a" * 40,
    ):
        assert not client.get(path).cache_control.immutable
    profile = client.get("/profile")
    assert not profile.cache_control.public
    with app.test_request_context():
        assert url_for("main.profile") == "/profile"
    # Each deployment generates distinct URLs; development keeps revalidation.
    for build in ("b" * 40, "unknown"):
        other = Flask("assets")
        navigation.configure_navigation(other, build)
        with other.test_request_context():
            target = url_for("static", filename="deck-lab.css")
        assert ("v=" + build in target) == (build != "unknown")


def test_private_or_cookie_static_responses_are_not_immutably_cached():
    sha = "c" * 40
    app = Flask("cookie-cache", static_folder=str(STATIC))
    app.secret_key = "navigation-asset-test"
    navigation.configure_navigation(app, sha)

    @app.after_request
    def mark_private(response):
        if request.args.get("mode") == "cookie":
            response.set_cookie("session", "secret")
        elif request.args.get("mode") == "private":
            response.headers["Cache-Control"] = "private, no-store"
        elif request.args.get("mode") == "session":
            session.modified = True
        return response

    client = app.test_client()
    versioned = "/static/deck-lab.css?v=" + sha
    cookie = client.get(versioned + "&mode=cookie")
    assert cookie.headers.get("Set-Cookie")
    assert not cookie.cache_control.immutable
    assert not cookie.cache_control.public
    private = client.get(versioned + "&mode=private")
    assert private.headers.get("Cache-Control") == "private, no-store"
    session_cookie = client.get(versioned + "&mode=session")
    assert session_cookie.headers.get("Set-Cookie")
    assert not session_cookie.cache_control.immutable
    assert not session_cookie.cache_control.public


def test_records_server_navigation_timings(client):
    """Server-only timings on this tiny fixture DB; not a browser or Mac baseline."""
    samples = {}
    for path in ("/build", "/profile", "/research/", "/research/?tab=metagame"):
        start = time.perf_counter()
        assert client.get(path).status_code == 200
        samples[path] = (time.perf_counter() - start) * 1000
    assert samples["/profile"] < 500
    assert samples["/build"] < 500
    assert samples["/research/"] < 2000
    assert samples["/research/?tab=metagame"] < 2000


def test_navigation_feedback_timeout_and_browser_conventions():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for browser event regression checks")
    script = STATIC / "deck-lab-navigation.js"
    harness = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const status = {hidden: true, textContent: ''};
const handlers = {};
function on(name, fn) { (handlers[name] = handlers[name] || []).push(fn); }
function fire(name, event) { (handlers[name] || []).forEach(fn => fn(event)); }
let timeout, delay;
const location = new URL('https://decklab.studio/research/');
const window = {addEventListener: on,
 setTimeout:(f,n)=>{timeout=f;delay=n;return 1;},clearTimeout:()=>{timeout=null;}};
const document = {querySelector:()=>status,addEventListener:on};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'), {document,window,location,URL});
function click(href, extras={}) {
 const {linkTarget='', download=false, closestMissing=false, textNode=false, ...eventExtras} = extras;
 const link={href,target:linkTarget,hasAttribute:(name)=>name==='download' && download};
 const target = closestMissing ? {} : textNode
  ? {nodeType:3, parentElement:{closest:()=>link}}
  : {closest:()=> extras.nolink ? null : link};
 fire('click', {button:0,target,defaultPrevented:false,...eventExtras});
}
for (const [href,extra] of [
 ['https://decklab.studio/profile',{ctrlKey:true}],
 ['https://decklab.studio/profile',{metaKey:true}],
 ['https://decklab.studio/profile',{shiftKey:true}],
 ['https://decklab.studio/profile',{altKey:true}],
 ['https://decklab.studio/profile',{defaultPrevented:true}],
 ['https://decklab.studio/profile',{button:1}],
 ['https://decklab.studio/profile',{linkTarget:'_blank'}],
 ['https://decklab.studio/profile',{download:true}],
 ['https://example.org/',{}],['https://decklab.studio/research/#filters',{}]
]) {click(href,extra);assert(status.hidden);}
click('https://decklab.studio/profile', {closestMissing:true});
assert(status.hidden);
click('https://decklab.studio/profile');
assert(!status.hidden);assert.strictEqual(status.textContent,'Loading…');assert(delay<=8000);
timeout();assert(status.textContent.includes('try the link again'));
fire('pageshow');assert(status.hidden);assert.strictEqual(timeout,null);
click('https://decklab.studio/research/?tab=metagame');assert(!status.hidden);
fire('keydown', {key:'Escape'});assert(status.hidden);
click('https://decklab.studio/profile', {textNode:true});
assert(!status.hidden);
console.log(JSON.stringify({passed:true}));
"""
    result = subprocess.run(
        [node, "-e", harness, str(script)],
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )
    assert json.loads(result.stdout)["passed"]
