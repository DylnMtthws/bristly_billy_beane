"""Plain-text deck import: parser, preview, persistence, and routes."""

from __future__ import annotations

import pytest

from sabermetrics import db
from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.deck_text_import import (
    DeckTextImportError,
    parse_deck_text,
    resolve_import,
)
from scripts.setup_db import setup_database


def test_parser_accepts_common_text_exports():
    parsed = parse_deck_text("""
// Kinnan list
# comment
1 Sol Ring
1x Lightning Bolt (NEO) 12 *F*
1 Island [Ramp]
1 Kinnan [Commander{top}]
SB: 1 Lightning Bolt

Commander
1 Tymna the Weaver

Deck
1 Sol Ring (C21) 243

Sideboard
1 Lightning Bolt

Maybeboard
1 Island
""".strip())
    by_name = {(line.name, line.zone, line.commander): line for line in parsed.lines}
    assert by_name[("Sol Ring", "Unsorted", False)].quantity == 1
    assert by_name[("Lightning Bolt", "Unsorted", False)].quantity == 1
    assert by_name[("Island", "Unsorted", False)].quantity == 1
    assert by_name[("Kinnan", "Unsorted", True)].commander is True
    assert by_name[("Lightning Bolt", "Sideboard", False)].quantity == 1
    assert by_name[("Tymna the Weaver", "Unsorted", True)].commander is True
    assert by_name[("Island", "Maybeboard", False)].zone == "Maybeboard"


def test_parser_rejects_zero_negative_and_huge_quantities():
    with pytest.raises(DeckTextImportError) as zero:
        parse_deck_text("0 Sol Ring")
    assert "at least 1" in str(zero.value)
    with pytest.raises(DeckTextImportError) as negative:
        parse_deck_text("-1 Sol Ring")
    assert "at least 1" in str(negative.value)
    with pytest.raises(DeckTextImportError) as huge:
        parse_deck_text("100 Sol Ring")
    assert "at most 99" in str(huge.value)


def test_parser_rejects_urls_and_empty_lists():
    with pytest.raises(DeckTextImportError) as url:
        parse_deck_text("1 https://www.moxfield.com/decks/abc")
    assert "not a URL" in str(url.value)
    with pytest.raises(DeckTextImportError) as empty:
        parse_deck_text("// just a comment\n\n")
    assert "at least one card" in str(empty.value)


def _seed_cards(path):
    with db.connect(path) as conn:
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99,image_uri)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    "kinnan",
                    "ok",
                    "Kinnan, Bonder Prodigy",
                    2,
                    "Legendary Creature — Human Druid",
                    "Tap for mana.",
                    '["G","U"]',
                    1,
                    1,
                    "https://images.example.test/kinnan.jpg",
                ),
                (
                    "ring",
                    "or",
                    "Sol Ring",
                    1,
                    "Artifact",
                    "Add {C}{C}.",
                    "[]",
                    0,
                    1,
                    None,
                ),
                (
                    "bolt",
                    "ob",
                    "Lightning Bolt",
                    1,
                    "Instant",
                    "Deal 3 damage.",
                    '["R"]',
                    0,
                    1,
                    None,
                ),
                (
                    "island",
                    "oi",
                    "Island",
                    0,
                    "Basic Land — Island",
                    "",
                    '["U"]',
                    0,
                    1,
                    None,
                ),
                (
                    "tymna",
                    "ot",
                    "Tymna the Weaver",
                    4,
                    "Legendary Creature — Human Cleric",
                    "Partner\nDraw a card.",
                    '["W","B"]',
                    1,
                    1,
                    None,
                ),
                (
                    "thrasios",
                    "oth",
                    "Thrasios, Triton Hero",
                    2,
                    "Legendary Creature — Merfolk Wizard",
                    "Partner\n{4}: Scry 1.",
                    '["G","U"]',
                    1,
                    1,
                    None,
                ),
                (
                    "banned",
                    "osh",
                    "Shahrazad",
                    2,
                    "Sorcery",
                    "Players play a Magic subgame.",
                    "[]",
                    0,
                    0,
                    None,
                ),
                (
                    "fireice",
                    "ofi",
                    "Fire // Ice",
                    2,
                    "Instant // Instant",
                    "Fire deals 2 damage. // Tap target permanent.",
                    '["U","R"]',
                    0,
                    1,
                    None,
                ),
                (
                    "firelight",
                    "ofl",
                    "Fire // Lightning",
                    2,
                    "Instant // Instant",
                    "Fire again. // Lightning.",
                    '["R"]',
                    0,
                    1,
                    None,
                ),
            ],
        )
        conn.commit()


def _owner_repo(tmp_path):
    path = tmp_path / "import.db"
    setup_database(path)
    _seed_cards(path)
    owner = db.UsersRepo(path).create(
        email="owner@example.test", display_name="Owner", status="active"
    )
    return path, owner, DeckDocumentRepo(path)


def test_resolve_matches_exact_and_face_names_without_guessing(tmp_path):
    path, _owner, repo = _owner_repo(tmp_path)
    with db.connect(path) as conn:
        lookup = repo._card_lookup(conn)
    ice = resolve_import(parse_deck_text("1 Ice"), lookup)
    assert ice.entries[0].card["id"] == "fireice"
    with pytest.raises(DeckTextImportError) as unknown:
        resolve_import(parse_deck_text("1 Not A Real Card"), lookup)
    assert unknown.value.errors[0]["line"] == 1
    assert "Unknown card" in unknown.value.errors[0]["message"]
    with pytest.raises(DeckTextImportError) as ambiguous:
        resolve_import(parse_deck_text("1 Fire"), lookup)
    assert "more than one card" in ambiguous.value.errors[0]["message"]
    with pytest.raises(DeckTextImportError) as prefix:
        resolve_import(parse_deck_text("1 Sol"), lookup)
    assert "Unknown card" in prefix.value.errors[0]["message"]


def test_preview_does_not_write_and_reports_illegal_cards(tmp_path):
    path, owner, repo = _owner_repo(tmp_path)
    text = """
Commander
1 Kinnan, Bonder Prodigy

Deck
1 Sol Ring
1 Shahrazad
1 Island
""".strip()
    before = repo.list_for_owner(owner)
    for operation in (
        lambda: repo.preview_text_import(text, title="Kinnan paste"),
        lambda: repo.import_text(owner, text, title="Kinnan paste"),
    ):
        with pytest.raises(DeckTextImportError) as failure:
            operation()
        assert "not Commander-legal" in str(failure.value)
        assert failure.value.errors[0]["line"] == 6
    assert repo.list_for_owner(owner) == before
    with db.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM deck_documents").fetchone()[0] == 0


def test_import_creates_private_deck_and_deducts_commander_once(tmp_path):
    _path, owner, repo = _owner_repo(tmp_path)
    existing = repo.create(owner, title="Keep me", commander_card_id="kinnan")
    text = """
Commander
1 Kinnan, Bonder Prodigy

Deck
1 Kinnan, Bonder Prodigy
1 Sol Ring
1 Island

Sideboard
1 Lightning Bolt

Maybeboard
1 Island
""".strip()
    deck_id = repo.import_text(owner, text, title="Imported Kinnan")
    document = repo.get(owner, deck_id)
    assert document["title"] == "Imported Kinnan"
    assert document["visibility"] == "private"
    assert document["id"] != existing
    commanders = [entry for entry in document["entries"] if entry["is_commander"]]
    assert [entry["name"] for entry in commanders] == ["Kinnan, Bonder Prodigy"]
    zone_names = {zone["id"]: zone["name"] for zone in document["zones"]}
    library = [
        entry
        for entry in document["entries"]
        if not entry["is_commander"] and zone_names.get(entry["zone_id"]) == "Unsorted"
    ]
    names = {entry["name"]: entry["quantity"] for entry in library}
    assert names["Kinnan, Bonder Prodigy"] == 1
    assert names["Sol Ring"] == 1
    assert names["Island"] == 1
    side = next(zone for zone in document["zones"] if zone["name"] == "Sideboard")
    maybe = next(zone for zone in document["zones"] if zone["name"] == "Maybeboard")
    assert any(
        entry["zone_id"] == side["id"] and entry["name"] == "Lightning Bolt"
        for entry in document["entries"]
    )
    assert any(
        entry["zone_id"] == maybe["id"] and entry["name"] == "Island"
        for entry in document["entries"]
    )
    kept = repo.get(owner, existing)
    assert kept["title"] == "Keep me"
    assert kept["revision"] == 0


def test_missing_commander_selection_is_explicit_and_optional(tmp_path):
    _path, owner, repo = _owner_repo(tmp_path)
    text = "1 Kinnan, Bonder Prodigy\n1 Sol Ring"
    preview = repo.preview_text_import(text, title="Needs commander")
    assert preview["needs_commander_selection"] is True
    assert {card["id"] for card in preview["eligible_commanders"]} == {"kinnan"}
    assert "Choose one commander" in " ".join(preview["validation"]["issues"])
    selected = repo.preview_text_import(
        text, title="Needs commander", commander_card_ids=["kinnan"]
    )
    assert selected["needs_commander_selection"] is False
    assert selected["commanders"][0]["card_id"] == "kinnan"
    library_names = {
        card["name"] for zone in selected["zones"] for card in zone["cards"]
    }
    assert "Kinnan, Bonder Prodigy" not in library_names
    deck_id = repo.import_text(
        owner, text, title="Chosen commander", commander_card_ids=["kinnan"]
    )
    document = repo.get(owner, deck_id)
    assert [
        entry["name"] for entry in document["entries"] if entry["is_commander"]
    ] == ["Kinnan, Bonder Prodigy"]


def test_incompatible_pair_is_rejected_and_partners_import(tmp_path):
    _path, owner, repo = _owner_repo(tmp_path)
    illegal = """
Commanders
1 Kinnan, Bonder Prodigy
1 Tymna the Weaver
""".strip()
    with pytest.raises(DeckTextImportError) as exc:
        repo.preview_text_import(illegal)
    assert "recognized legal pair" in str(exc.value)
    partners = """
Commanders
1 Tymna the Weaver
1 Thrasios, Triton Hero

Deck
1 Sol Ring
1 Island
""".strip()
    deck_id = repo.import_text(owner, partners, title="Partners")
    document = repo.get(owner, deck_id)
    names = sorted(
        entry["name"] for entry in document["entries"] if entry["is_commander"]
    )
    assert names == ["Thrasios, Triton Hero", "Tymna the Weaver"]
    assert document["validation"]["commander_count"] == 2


def test_quantity_overflow_and_rollback(tmp_path, monkeypatch):
    _path, owner, repo = _owner_repo(tmp_path)
    with pytest.raises(DeckTextImportError) as overflow:
        repo.import_text(owner, "90 Island\n20 Island")
    assert "at most 99" in str(overflow.value)
    original = DeckDocumentRepo._insert_card

    def boom(self, *args, **kwargs):
        raise RuntimeError("write failed")

    monkeypatch.setattr(DeckDocumentRepo, "_insert_card", boom)
    with pytest.raises(RuntimeError):
        repo.import_text(
            owner,
            "Commander\n1 Kinnan, Bonder Prodigy\n\nDeck\n1 Sol Ring",
            title="Should roll back",
        )
    monkeypatch.setattr(DeckDocumentRepo, "_insert_card", original)
    assert repo.list_for_owner(owner) == []


def _client(tmp_path, monkeypatch):
    path, owner, _repo = _owner_repo(tmp_path)
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    from sabermetrics.ui.app import create_app

    app = create_app(path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
        DECK_LAB_REDESIGN_ENABLED=True,
        DECK_LAB_BUILDER_ENABLED=True,
    )
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = owner
        session["_fresh"] = True
    return client, owner, path


def test_library_exposes_import_and_preview_confirm_routes(tmp_path, monkeypatch):
    client, _owner, path = _client(tmp_path, monkeypatch)
    library = client.get("/build")
    assert library.status_code == 200
    assert b"Import deck" in library.data
    assert b'id="import-deck-dialog"' in library.data
    text = """Commander
1 Kinnan, Bonder Prodigy

Deck
1 Sol Ring
1 Island"""
    preview = client.post(
        "/build/import/preview",
        json={"title": "From paste", "text": text},
    )
    assert preview.status_code == 200
    body = preview.get_json()
    assert body["commanders"][0]["name"] == "Kinnan, Bonder Prodigy"
    with db.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM deck_documents").fetchone()[0] == 0
    created = client.post(
        "/build/import",
        json={"title": "From paste", "text": text},
    )
    assert created.status_code == 201
    payload = created.get_json()
    document = client.get(f"/api/decks/{payload['id']}").get_json()
    assert document["title"] == "From paste"
    assert document["visibility"] == "private"
    unknown = client.post(
        "/build/import/preview",
        json={"title": "From paste", "text": "1 Missing Card\n1 Sol Ring"},
    )
    assert unknown.status_code == 400
    error_body = unknown.get_json()
    assert error_body["text"] == "1 Missing Card\n1 Sol Ring"
    assert any("Unknown card" in item["message"] for item in error_body["errors"])
    missing = client.post(
        "/build/import/preview",
        json={"title": "No commander", "text": "1 Sol Ring\n1 Island"},
    )
    assert missing.status_code == 200
    assert missing.get_json()["needs_commander_selection"] is True
    pair = client.post(
        "/build/import",
        json={
            "title": "Bad pair",
            "text": "Commanders\n1 Kinnan, Bonder Prodigy\n1 Tymna the Weaver",
        },
    )
    assert pair.status_code == 400
    assert "legal pair" in pair.get_json()["errors"][0]["message"]
    other = db.UsersRepo(path).create(
        email="other@example.test", display_name="Other", status="active"
    )
    stolen = client.get(f"/api/decks/{payload['id']}").get_json()
    assert stolen["id"] == payload["id"]
    other_client = client.application.test_client()
    with other_client.session_transaction() as session:
        session["_user_id"] = other
        session["_fresh"] = True
    assert other_client.get(f"/api/decks/{payload['id']}").status_code == 404
    anonymous = client.application.test_client()
    assert (
        anonymous.post("/build/import/preview", json={"text": text}).status_code == 302
    )


def test_text_import_requires_csrf(tmp_path, monkeypatch):
    path, owner, _repo = _owner_repo(tmp_path)
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    from sabermetrics.ui.app import create_app

    app = create_app(path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=True,
        RATELIMIT_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
        DECK_LAB_REDESIGN_ENABLED=True,
        DECK_LAB_BUILDER_ENABLED=True,
    )
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = owner
        session["_fresh"] = True
    denied = client.post(
        "/build/import",
        data={"title": "CSRF", "text": "1 Sol Ring"},
    )
    assert denied.status_code == 400
    with db.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM deck_documents").fetchone()[0] == 0


def test_commander_section_quantities_are_not_silently_discarded(tmp_path):
    _path, owner, repo = _owner_repo(tmp_path)
    for text in (
        "Commander\n2 Kinnan, Bonder Prodigy\nDeck\n1 Sol Ring",
        "Commander\n1 Kinnan, Bonder Prodigy\n1 Kinnan, Bonder Prodigy",
    ):
        with pytest.raises(DeckTextImportError, match="exactly once"):
            repo.import_text(owner, text)
    assert repo.list_for_owner(owner) == []


def test_sideboard_commander_selection_transfers_exactly_one_copy(tmp_path):
    _path, owner, repo = _owner_repo(tmp_path)
    text = "Deck\n1 Sol Ring\nSideboard\n1 Kinnan, Bonder Prodigy"
    preview = repo.preview_text_import(text, commander_card_ids=["kinnan"])
    deck = repo.get(owner, repo.import_text(owner, text, commander_card_ids=["kinnan"]))
    assert sum(e["quantity"] for e in deck["entries"]) == 2
    assert deck["validation"]["library_count"] == 1
    assert preview["validation"]["library_count"] == 1


def test_import_auxiliary_zones_do_not_affect_main_deck_validation(tmp_path):
    _path, owner, repo = _owner_repo(tmp_path)
    text = "Commander\n1 Kinnan, Bonder Prodigy\nDeck\n98 Island\n1 Sol Ring\nSideboard\n1 Lightning Bolt\nMaybeboard\n1 Sol Ring"
    preview = repo.preview_text_import(text)
    deck = repo.get(owner, repo.import_text(owner, text))
    for validation in (preview["validation"], deck["validation"]):
        assert validation["library_count"] == 99
        assert validation["total_count"] == 100
        assert validation["legal"] is True
    assert sum(e["quantity"] for e in deck["entries"]) == 102
