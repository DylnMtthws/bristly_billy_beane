"""Research control accents, headings, sliders, Build buttons, and tab CSS (DYL-42–48)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "sabermetrics" / "ui" / "static"
CONTROLS_CSS = STATIC / "feedback-controls.css"
DECK_LAB_CSS = STATIC / "deck-lab.css"
FRAGMENT = (
    ROOT
    / "src"
    / "sabermetrics"
    / "ui"
    / "templates"
    / "deck_lab"
    / "research_fragment.html"
)
BOUND_RANGE = (
    ROOT
    / "src"
    / "sabermetrics"
    / "ui"
    / "templates"
    / "deck_lab"
    / "_bound_range.html"
)
HARNESS = Path(__file__).with_name("feedback_controls_harness.js")


def _app_client(tmp_path, monkeypatch):
    path = tmp_path / "feedback-controls.db"
    setup_database(path)
    user = db.UsersRepo(path).create(
        email="controls@example.test",
        display_name="Controls",
        role="user",
        status="active",
    )
    with db.connect(path) as conn:
        conn.execute(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,color_identity,is_legal_commander,is_legal_in_99)
            VALUES('kinnan','oracle','Kinnan Controls',2,'Legendary Creature','["G","U"]',1,1)"""
        )
        conn.commit()
    monkeypatch.setenv("SABER_SKIP_DOTENV", "1")
    monkeypatch.setenv("SABER_RESEARCH_SYNC", "0")
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    app = create_app(path)
    app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False, WTF_CSRF_ENABLED=False)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = user
        session["_fresh"] = True
    return client


def test_filter_headings_and_commander_build_are_scoped(tmp_path, monkeypatch):
    client = _app_client(tmp_path, monkeypatch)
    cards = client.get("/research/?tab=cards").get_data(as_text=True)
    commanders = client.get("/research/?tab=commanders").get_data(as_text=True)
    fragment = FRAGMENT.read_text()
    for heading in (
        "Colors",
        "Color identity",
        "Mana value",
        "Power",
        "Toughness",
        "Oracle text",
        "Rarity",
        "Supertype",
        "Meta share",
    ):
        assert f'class="dl-filter-heading">{heading}<' in fragment or (
            'class="dl-filter-heading" for=' in fragment and heading in fragment
        )
    assert 'class="dl-filter-heading">Colors<' in cards
    assert 'class="dl-filter-heading">Mana value<' in cards
    assert 'class="dl-filter-heading">Power<' in cards
    assert 'class="dl-filter-heading">Toughness<' in cards
    assert 'class="dl-filter-heading">Color identity<' in commanders
    assert 'class="dl-button dl-catalog-build">Build<' in commanders
    assert "dl-catalog-card-actions" in commanders
    assert re.search(
        r'<button class="dl-icon-button[^"]*" data-fav-commander[^>]*>♥</button>',
        commanders,
    )
    assert "/research/commander/kinnan/build" in commanders
    assert "dl-catalog-build" not in fragment.split("dl-mobile-results", 1)[-1]
    assert 'class="dl-button dl-button-primary">Build<' in fragment
    assert 'class="dl-button dl-button-primary" type="submit">Apply<' in cards
    assert "data-research-tabs" in cards


def test_cards_and_commanders_sliders_render_query_live_labels(tmp_path, monkeypatch):
    client = _app_client(tmp_path, monkeypatch)
    default_cards = client.get("/research/?tab=cards").get_data(as_text=True)
    default_commanders = client.get("/research/?tab=commanders").get_data(as_text=True)
    for html in (default_cards, default_commanders):
        assert "data-bound-selection" not in html
        assert html.count('data-bound-live="min"') >= 1
        assert html.count("<span>0</span><span>5</span><span>10+</span>") >= 1
        assert re.search(r'data-bound-live="min"[^>]*hidden', html)
        assert 'step="1"' in html
        assert 'aria-valuetext="0"' in html
        assert 'aria-valuetext="10+"' in html
    ranged = client.get("/research/?tab=cards&mana_min=1&mana_max=7").get_data(
        as_text=True
    )
    min_live = re.search(r'data-bound-live="min"[^>]*>([^<]+)', ranged)
    max_live = re.search(r'data-bound-live="max"[^>]*>([^<]+)', ranged)
    assert min_live is not None and min_live.group(1) == "1"
    assert max_live is not None and max_live.group(1) == "7"
    assert "hidden" not in min_live.group(0)
    close = client.get("/research/?tab=commanders&mana_min=2&mana_max=3").get_data(
        as_text=True
    )
    combined = re.search(r'data-bound-live="combined"[^>]*>([^<]+)', close)
    assert combined is not None and combined.group(1) == "2–3"
    assert "hidden" not in combined.group(0)


def test_research_control_js_slider_and_tab_behavior():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute research control behavior")
    result = subprocess.run(
        [node, str(HARNESS), str(STATIC / "deck-lab-research.js")],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        pytest.fail(
            result.stderr or result.stdout or "feedback controls harness failed"
        )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["passed"]
    assert payload["live"]["baselineHidden"]
    assert payload["live"]["mid"] == ["1", "7"]
    assert payload["live"]["close"] == "2–3"
    assert payload["live"]["coincident"] == "4"
    assert payload["live"]["clamped"] == ["6", "6"]
    assert payload["live"]["cleared"]
    assert payload["tabs"]["immediate"] == "2"
    assert payload["tabs"]["dir"] == "forward"
    assert payload["tabs"]["rapid"] == "0"
    assert payload["tabs"]["reducedStatic"]
    assert payload["tabs"]["filterUnchanged"]
    assert payload["tabs"]["fetchOnClick"]
    assert payload["tabs"]["popstate"] == "3"
