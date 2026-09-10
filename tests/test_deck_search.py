"""Public Decks color/commander filters, pairing, and autocomplete."""

from __future__ import annotations

from sabermetrics import db
from sabermetrics.commander_pairs import can_participate_in_pair, compatible_pair
from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True


def _insert_cards(path, rows):
    with db.connect(path) as conn:
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,color_identity,oracle_text,
             is_legal_commander,is_legal_in_99)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()


def _titles(listed):
    return [item["title"] for item in listed["results"]]


def _seed_deck_search(path):
    _insert_cards(
        path,
        [
            (
                "kinnan",
                "ok",
                "Kinnan",
                2,
                "Legendary Creature — Human",
                '["G","U"]',
                "At the beginning of your upkeep, untap another target permanent.",
                1,
                1,
            ),
            (
                "kinnan-alt",
                "ok",
                "Kinnan",
                2,
                "Legendary Creature — Human",
                '["G","U"]',
                "At the beginning of your upkeep, untap another target permanent.",
                1,
                1,
            ),
            (
                "tymna",
                "otym",
                "Tymna",
                4,
                "Legendary Creature — Human Cleric",
                '["W","B"]',
                "Partner (You can have two commanders if both have partner.)",
                1,
                1,
            ),
            (
                "thrasios",
                "othr",
                "Thrasios",
                2,
                "Legendary Creature — Merfolk Wizard",
                '["G","U"]',
                "Partner (You can have two commanders if both have partner.)",
                1,
                1,
            ),
            (
                "krenko",
                "okre",
                "Krenko",
                3,
                "Legendary Creature — Goblin",
                '["R"]',
                "",
                1,
                1,
            ),
            (
                "kozilek",
                "okoz",
                "Kozilek",
                10,
                "Legendary Creature — Eldrazi",
                "[]",
                "",
                1,
                1,
            ),
            ("ring", "or", "Sol Ring", 1, "Artifact", "[]", "", 0, 1),
            (
                "bolt",
                "ob",
                "Lightning Bolt",
                1,
                "Instant",
                '["R"]',
                "Lightning Bolt deals 3 damage to any target.",
                0,
                1,
            ),
        ],
    )
    owner = db.UsersRepo(path).create(
        email="owner@example.test", display_name="Owner", status="active"
    )
    viewer = db.UsersRepo(path).create(
        email="viewer@example.test", display_name="Viewer", status="active"
    )
    repo = DeckDocumentRepo(path)
    kinnan = repo.create(owner, title="Public Kinnan", commander_card_id="kinnan")
    kinnan_alt = repo.create(
        owner, title="Alt printing Kinnan", commander_card_id="kinnan-alt"
    )
    pair = repo.create(
        owner,
        title="Tymna Thrasios",
        commander_card_ids=["tymna", "thrasios"],
    )
    red = repo.create(owner, title="Public Krenko", commander_card_id="krenko")
    colorless = repo.create(owner, title="Public Kozilek", commander_card_id="kozilek")
    empty = repo.create(owner, title="No commander yet")
    private = repo.create(owner, title="Private Kinnan", commander_card_id="kinnan")
    repo.set_visibility(owner, kinnan, "public")
    repo.set_visibility(owner, kinnan_alt, "public")
    repo.set_visibility(owner, pair, "public")
    repo.set_visibility(owner, red, "public")
    repo.set_visibility(owner, colorless, "public")
    repo.set_visibility(owner, empty, "public")
    document = repo.get(owner, kinnan)
    unsorted = document["zones"][0]["id"]
    with db.connect(path) as conn:
        conn.execute(
            """INSERT INTO deck_entries
            (id, deck_id, zone_id, card_id, oracle_id, name, quantity,
             is_commander, sort_order, type_line, color_identity)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                db.new_id(),
                kinnan,
                unsorted,
                "bolt",
                "ob",
                "Lightning Bolt",
                1,
                0,
                0,
                "Instant",
                '["R"]',
            ),
        )
        conn.commit()
    return {
        "owner": owner,
        "viewer": viewer,
        "repo": repo,
        "kinnan": kinnan,
        "kinnan_alt": kinnan_alt,
        "pair": pair,
        "red": red,
        "colorless": colorless,
        "empty": empty,
        "private": private,
    }


def _research_app(path):
    app = create_app(path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
        DECK_LAB_REDESIGN_ENABLED=True,
        DECK_LAB_RESEARCH_ENABLED=True,
        DECK_LAB_BUILDER_ENABLED=True,
    )
    return app


def _research_client(path, user_id):
    app = _research_app(path)
    client = app.test_client()
    _login(client, user_id)
    return app, client


def test_can_participate_in_pair_uses_ability_lines_not_substring():
    partner = {
        "name": "Tymna",
        "oracle_text": "Partner (You can have two commanders if both have partner.)",
        "type_line": "Legendary Creature — Human",
    }
    mention = {
        "name": "Kinnan",
        "oracle_text": "Each other nontoken creature you control has partner.",
        "type_line": "Legendary Creature — Human",
    }
    background = {
        "name": "Haunted One",
        "oracle_text": "Creatures you control have menace.",
        "type_line": "Legendary Enchantment — Background",
    }
    assert can_participate_in_pair(partner) is True
    assert can_participate_in_pair(mention) is False
    assert can_participate_in_pair(background) is True
    assert compatible_pair(partner, partner) is False


def test_public_deck_filters_compose_before_count(tmp_path):
    path = tmp_path / "deck-search.db"
    setup_database(path)
    seeded = _seed_deck_search(path)
    repo = seeded["repo"]

    listed = repo.list_public()
    assert "Private Kinnan" not in _titles(listed)
    assert listed["total"] == len(listed["results"]) == 6

    include_ug = repo.list_public(colors=["U", "G"], color_mode="include")
    assert set(_titles(include_ug)) == {
        "Public Kinnan",
        "Alt printing Kinnan",
        "Tymna Thrasios",
    }
    assert include_ug["total"] == 3

    exactly_ug = repo.list_public(colors=["U", "G"], color_mode="exactly")
    assert set(_titles(exactly_ug)) == {"Public Kinnan", "Alt printing Kinnan"}

    exclude_r = repo.list_public(colors=["R"], color_mode="exclude")
    assert "Public Krenko" not in _titles(exclude_r)
    assert "Public Kinnan" in _titles(exclude_r)
    assert "No commander yet" not in _titles(exclude_r)

    include_r = repo.list_public(colors=["R"], color_mode="include")
    assert _titles(include_r) == ["Public Krenko"]

    exactly_c = repo.list_public(colors=["C"], color_mode="exactly")
    assert _titles(exactly_c) == ["Public Kozilek"]
    assert "No commander yet" not in _titles(exactly_c)

    include_c = repo.list_public(colors=["C"], color_mode="include")
    assert _titles(include_c) == ["Public Kozilek"]


def test_public_deck_commander_and_partner_matching(tmp_path):
    path = tmp_path / "deck-pairs.db"
    setup_database(path)
    seeded = _seed_deck_search(path)
    repo = seeded["repo"]

    reversed_pair = repo.list_public(commander="Thrasios", partner="Tymna")
    assert _titles(reversed_pair) == ["Tymna Thrasios"]

    same_card = repo.list_public(commander="Tymna", partner="Tymna")
    assert same_card["results"] == []
    assert same_card["total"] == 0

    invalid_pair = repo.list_public(commander="Tymna", partner="Kinnan")
    assert invalid_pair["results"] == []
    assert invalid_pair["total"] == 0

    unmatched = repo.list_public(commander="NoSuchCommander")
    assert unmatched["results"] == []
    assert unmatched["total"] == 0

    partial = repo.list_public(commander="Kin")
    assert set(_titles(partial)) == {"Public Kinnan", "Alt printing Kinnan"}

    stale_partner = repo.list_public(commander="Kinnan", partner="Tymna")
    assert _titles(stale_partner) == []

    printing = repo.list_public(commander="Kinnan")
    assert set(_titles(printing)) == {"Public Kinnan", "Alt printing Kinnan"}

    composed = repo.list_public(
        query="Public",
        colors=["U", "G"],
        color_mode="exactly",
        commander="Kin",
    )
    assert _titles(composed) == ["Public Kinnan"]


def test_deck_commander_suggestions_are_capped_public_metadata(tmp_path):
    path = tmp_path / "deck-suggest.db"
    setup_database(path)
    seeded = _seed_deck_search(path)
    repo = seeded["repo"]

    primary = repo.suggest_deck_commanders(query="Tym")
    assert primary
    assert primary[0]["name"] == "Tymna"
    assert primary[0]["can_pair"] is True
    assert set(primary[0]) == {"id", "name", "can_pair"}

    kinnan = repo.suggest_deck_commanders(query="Kinnan")
    assert kinnan[0]["can_pair"] is False

    partners = repo.suggest_deck_commanders(query="", partner_of="tymna")
    names = {item["name"] for item in partners}
    assert "Thrasios" in names
    assert "Kinnan" not in names
    assert "Tymna" not in names
    payload = str(partners)
    assert "owner@example.test" not in payload
    assert "email" not in payload


def test_research_decks_filters_persist_and_hide_stale_partner(tmp_path, monkeypatch):
    path = tmp_path / "deck-routes.db"
    setup_database(path)
    seeded = _seed_deck_search(path)
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    app, client = _research_client(path, seeded["viewer"])

    listing = client.get("/research?tab=decks")
    assert listing.status_code == 200
    assert b"Public Kinnan" in listing.data
    assert b"Private Kinnan" not in listing.data
    assert b'name="deck_color"' in listing.data
    assert b'name="commander"' in listing.data
    assert b"data-deck-partner-wrap" in listing.data
    assert b'role="combobox"' in listing.data
    assert b'aria-controls="deck-commander-suggest"' in listing.data

    filtered = client.get(
        "/research?tab=decks&deck_color=U&deck_color=G&deck_color_mode=exactly"
        "&commander=Kinnan"
    )
    assert filtered.status_code == 200
    assert b"Public Kinnan" in filtered.data
    assert b"Tymna Thrasios" not in filtered.data
    assert b'value="U"' in filtered.data and b"checked" in filtered.data
    assert b'value="exactly"' in filtered.data
    assert b'value="Kinnan"' in filtered.data
    assert b"No public decks match this search." not in filtered.data

    empty = client.get("/research?tab=decks&commander=NoSuchCommander")
    assert empty.status_code == 200
    assert b"No public decks match this search." in empty.data
    assert b"Public Kinnan" not in empty.data

    partner_field = client.get("/research?tab=decks&commander=Tymna")
    assert b'id="deck-partner-filter"' in partner_field.data
    assert b"data-deck-partner-wrap hidden" not in partner_field.data
    assert b'name="partner"' in partner_field.data

    stale = client.get("/research?tab=decks&commander=Kinnan&partner=Tymna")
    assert b"Public Kinnan" not in stale.data
    assert b"data-deck-partner-wrap hidden" in stale.data
    assert b"disabled" in stale.data

    page = client.get("/research?tab=decks&deck_color=U&deck_color_mode=include&page=1")
    assert b"deck_color=U" in page.data or b'name="deck_color"' in page.data
    assert b"deck_color_mode" in page.data

    anon = app.test_client()
    assert anon.get("/research?tab=decks").status_code == 302
    assert anon.get("/research/deck-commanders?q=Tym").status_code == 302

    suggestions = client.get("/research/deck-commanders?q=Tym")
    assert suggestions.status_code == 200
    assert suggestions.headers.get("Cache-Control") == "private, no-store"
    body = suggestions.get_json()
    assert body["results"][0]["name"] == "Tymna"
    assert body["results"][0]["can_pair"] is True
    assert "email" not in str(body)
    assert "owner@example.test" not in str(body)

    partners = client.get("/research/deck-commanders?partner_of=tymna&q=Thr")
    names = {item["name"] for item in partners.get_json()["results"]}
    assert "Thrasios" in names
    assert "Kinnan" not in names


def test_public_deck_filters_apply_before_pagination(tmp_path, monkeypatch):
    path = tmp_path / "deck-pages.db"
    setup_database(path)
    seeded = _seed_deck_search(path)
    repo = seeded["repo"]
    owner = seeded["owner"]
    for index in range(30):
        deck_id = repo.create(
            owner, title=f"Paged Kinnan {index:02d}", commander_card_id="kinnan"
        )
        repo.set_visibility(owner, deck_id, "public")
    page1 = repo.list_public(
        commander="Kinnan", colors=["U"], color_mode="include", per_page=24
    )
    page2 = repo.list_public(
        commander="Kinnan",
        colors=["U"],
        color_mode="include",
        per_page=24,
        page=2,
    )
    assert page1["has_next"] is True
    assert page1["total"] == page2["total"]
    assert page1["results"][0]["id"] != page2["results"][0]["id"]
    assert all("Kinnan" in item["commander_names"] for item in page1["results"])
    assert all("Kinnan" in item["commander_names"] for item in page2["results"])
    assert "Private Kinnan" not in _titles(page1) + _titles(page2)

    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    _, client = _research_client(path, seeded["viewer"])
    html = client.get(
        "/research?tab=decks&commander=Kinnan&deck_color=U&deck_color_mode=include"
    )
    assert html.status_code == 200
    assert b"page=2" in html.data
    assert b"commander=Kinnan" in html.data
    assert b"deck_color=U" in html.data
    assert b"deck_color_mode=include" in html.data
