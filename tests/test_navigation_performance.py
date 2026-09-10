"""Navigation budgets cover database reuse, isolation and browser asset caching."""

import json
import shutil
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest
from flask import url_for

from sabermetrics import db
from sabermetrics.research import ResearchRepo
from sabermetrics.ui import navigation
from sabermetrics.ui.app import create_app
from sabermetrics.ui.navigation import ResearchLandingCache
from scripts.setup_db import setup_database


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
    app = create_app(path)
    app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = user
        session["_fresh"] = True
    return client


def test_meta_reuses_default_cohort_but_not_user_favorites(client, monkeypatch):
    calls = Mock(wraps=ResearchRepo.commanders)
    monkeypatch.setattr(
        ResearchRepo, "commanders", lambda self, **kw: calls(self, **kw)
    )
    favorites = Mock(return_value={"commander"})
    monkeypatch.setattr(db.FavoritesRepo, "commander_ids", favorites)
    assert client.get("/research/").status_code == 200
    favorites.return_value = set()
    response = client.get("/research/?tab=metagame")
    assert response.status_code == 200
    assert b"dl-icon-button active" not in response.data
    # The expensive cohort query must not run again when selecting default Meta.
    assert calls.call_count == 1
    assert client.get("/research/?tab=metagame&q=missing").status_code == 200
    assert calls.call_count == 2
    assert client.get("/research/?tab=metagame&window=30").status_code == 200
    assert calls.call_count == 3
    assert client.get("/research/?tab=metagame&favorites=1").status_code == 200
    assert calls.call_count == 4


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
    load = Mock(return_value={"results": []})
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: cache.get(path, load), range(16)))
    assert len(results) == 16
    assert load.call_count == 1


def test_build_and_profile_avoid_retired_repositories(client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Navigation queried a retired or research dependency")

    monkeypatch.setattr(db.DecksRepo, "list_for_owner", forbidden)
    monkeypatch.setattr(db.CedhCandidatesRepo, "list_for_owner", forbidden)
    monkeypatch.setattr(ResearchRepo, "commanders", forbidden)
    for path in ("/build", "/profile"):
        response = client.get(path)
        assert response.status_code == 200
        assert b"data-navigation-status" in response.data


def test_versioned_assets_cache_without_caching_account_html(client):
    app = client.application
    with app.test_request_context():
        asset = url_for("static", filename="deck-lab.css")
    assert "v=" + "a" * 40 in asset
    response = client.get(asset)
    assert response.cache_control.public
    assert response.cache_control.max_age == 31536000
    assert response.cache_control.immutable
    assert (
        client.get(
            asset, headers={"If-None-Match": response.headers["ETag"]}
        ).status_code
        == 304
    )
    for path in (
        "/static/deck-lab.css",
        "/static/deck-lab.css?v=old",
        "/profile",
        "/build",
        "/static/missing.js?v=" + "a" * 40,
    ):
        assert not client.get(path).cache_control.immutable
    with app.test_request_context():
        assert url_for("main.profile") == "/profile"
    # Each deployment generates distinct URLs; development keeps revalidation.
    for build in ("b" * 40, "unknown"):
        from flask import Flask

        other = Flask("assets")
        navigation.configure_navigation(other, build)
        with other.test_request_context():
            target = url_for("static", filename="deck-lab.css")
        assert ("v=" + build in target) == (build != "unknown")


def test_navigation_feedback_timeout_and_browser_conventions():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for browser event regression checks")
    script = (
        Path(__file__).parents[1] / "src/sabermetrics/ui/static/deck-lab-navigation.js"
    )
    harness = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const status = {hidden: true, textContent: ''}, handlers = {};
let timeout, delay;
const location = new URL('https://decklab.studio/research/');
const window = {addEventListener:(k,f)=>handlers[k]=f,
 setTimeout:(f,n)=>{timeout=f;delay=n;return 1;},clearTimeout:()=>{timeout=null;}};
const document = {querySelector:()=>status,addEventListener:(k,f)=>handlers[k]=f};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'), {document,window,location,URL});
function click(href, extras={}) {
 const link={href,target:'',hasAttribute:()=>false};
 handlers.click({button:0,target:{closest:()=>link},...extras});
}
for (const [href,extra] of [
 ['https://decklab.studio/profile',{ctrlKey:true}],
 ['https://decklab.studio/profile',{defaultPrevented:true}],
 ['https://decklab.studio/profile',{button:1}],
 ['https://example.org/',{}],['https://decklab.studio/research/#filters',{}]
]) {click(href,extra);assert(status.hidden);}
click('https://decklab.studio/profile');
assert(!status.hidden);assert.strictEqual(status.textContent,'Loading…');assert(delay<=8000);
timeout();assert(status.textContent.includes('try the link again'));
handlers.pageshow();assert(status.hidden);assert.strictEqual(timeout,null);
click('https://decklab.studio/research/?tab=metagame');assert(!status.hidden);
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
