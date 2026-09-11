"""The redesigned routes are feature-gated and work on disposable data."""

import re
from io import BytesIO

import pytest
from PIL import Image

from sabermetrics import db
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True


def _database(tmp_path):
    path = tmp_path / "redesign.db"
    setup_database(path)
    user = db.UsersRepo(path).create(
        email="builder@example.test",
        display_name="Builder",
        role="admin",
        status="active",
    )
    with db.connect(path) as conn:
        conn.execute("""INSERT INTO cards
            (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99,image_uri)
            VALUES('kinnan','oracle-kinnan','Kinnan Test','{G}{U}',2,
             'Legendary Creature — Human Druid','Mana text','["G","U"]',1,1,
             'https://images.example.test/kinnan.jpg')""")
        conn.execute(
            """INSERT INTO cards
            (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99,image_uri)
            VALUES('ring','oracle-ring','Sol Ring','{1}',1,'Artifact','Mana text','[]',0,1,NULL)"""
        )
        conn.execute("""INSERT INTO cards
            (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99,image_uri)
            VALUES('bolt','oracle-bolt','Lightning Bolt','{R}',1,'Instant',
             'Deal 3 damage','["R"]',0,1,NULL)""")
        conn.commit()
    return path, user


def test_routes_are_off_by_default(tmp_path):
    path, user = _database(tmp_path)
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    _login(client, user)
    assert client.get("/build").status_code == 404
    assert client.get("/research").status_code == 404


def test_builder_research_and_admin_vertical_slice(tmp_path, monkeypatch):
    path, user = _database(tmp_path)
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    _login(client, user)

    home = client.get("/").data
    assert b"Research the format" in home
    assert b"Invite-only beta" not in home
    assert b"players" not in home.lower()
    assert b"commanders tracked" not in home.lower()
    assert b"of your decks" not in home.lower()
    assert home.count(b'class="dl-door-actions"') == 2
    assert "© 2026 Deck Lab".encode() in home
    assert b"Tournament data" not in home
    empty_library = client.get("/build").data
    assert b"Build" in empty_library
    assert b"All 0" in empty_library
    assert b"Favorites 0" in empty_library
    assert b"Recently edited 0" in empty_library
    assert b'class="dl-new-deck-card"' in empty_library
    assert b">Filter</button>" not in empty_library
    response = client.get("/research?q=Kinnan")
    assert response.status_code == 200
    assert b"Kinnan Test" in response.data
    assert b"Tournament evidence:" not in response.data
    assert b"Plays this card" not in response.data
    assert b"data-research-sort-menu" not in response.data
    assert b"data-research-sort-menu" in client.get("/research?tab=metagame").data
    assert b'id="research-sort"' not in response.data
    card_results = client.get("/research?tab=cards").data
    assert b'class="dl-card-result-grid"' in card_results
    assert b"commander-legal cards" in card_results
    assert b"Card corpus results" not in card_results
    assert b">Meta</a>" in card_results
    assert card_results.count(b"PAGE 1") == 1
    assert b"https://images.example.test/kinnan.jpg" in card_results
    assert b"api.scryfall.com/cards/named?fuzzy=Sol%20Ring" in card_results
    searched_cards = client.get("/research?tab=cards&q=Sol").data
    for label in (b"Cards", b"Commanders", b"Meta"):
        tab_href = re.search(
            rb'href="([^"]+)"[^>]*>' + label + rb"</a>", searched_cards
        )
        assert tab_href is not None
        assert b"q=" not in tab_href.group(1)
        assert b"window=" not in tab_href.group(1)
    assert b"data-scope-window" not in searched_cards
    commander_results = client.get("/research?tab=commanders").data
    assert b"data-scope-window" not in commander_results
    meta_results = client.get("/research?tab=metagame&window=60").data
    assert b"data-scope-window" in meta_results
    assert b"Last 30 days" in meta_results
    assert b"Last 60 days" in meta_results
    assert b"Last 90 days" in meta_results
    assert b"Last 180 days" in meta_results
    assert b"All time" in meta_results
    all_time_meta = client.get("/research?tab=metagame&window=0").data
    assert b"Field view" not in all_time_meta
    assert b'<option value="0" selected>All time</option>' in all_time_meta
    retired = client.get("/research/compare?left=kinnan&right=kinnan")
    assert retired.status_code == 302
    assert retired.headers["Location"] == "/research/"
    assert client.get("/admin/").status_code == 200
    assert client.get("/admin/users").status_code == 200

    created = client.post(
        "/build/new",
        json={"title": "Kinnan draft", "commander_card_id": "kinnan"},
    )
    assert created.status_code == 201
    payload = created.get_json()
    deck_id = payload["id"]
    document = client.get(f"/api/decks/{deck_id}").get_json()
    scoped_cards = client.get(f"/api/cards?deck_id={deck_id}").get_json()
    assert scoped_cards["scope"] == "Commander identity"
    assert "Lightning Bolt" not in {item["name"] for item in scoped_cards["results"]}
    unsorted = document["zones"][0]["id"]
    edited = client.post(
        f"/api/decks/{deck_id}/commands",
        json={
            "expected_revision": 0,
            "mutation_id": "route-edit",
            "commands": [{"type": "add_card", "card_id": "ring", "zone_id": unsorted}],
        },
    )
    assert edited.status_code == 200
    assert edited.get_json()["validation"]["library_count"] == 1
    tagged = client.post(
        f"/api/decks/{deck_id}/commands",
        json={
            "expected_revision": 1,
            "mutation_id": "route-tag",
            "commands": [{"type": "add_tag", "name": "Turbo"}],
        },
    )
    assert tagged.status_code == 200
    assert tagged.get_json()["tags"][0]["name"] == "Turbo"
    builder = client.get(f"/build/deck/{deck_id}").data
    assert b"deck-document-data" in builder
    assert b"data-card-search" in builder
    assert b">Decklist</button>" in builder
    assert b'placeholder="Add or find a card"' in builder
    assert b'aria-label="Decklist display"' in builder
    assert b'data-density="compact"' in builder
    assert b"data-bulk-controls" in builder
    assert b'data-toggle-rail="left"' not in builder
    assert b'data-toggle-rail="right"' in builder
    assert b"dl-add-panel" not in builder
    assert b'aria-label="Add zone"' in builder
    assert b"Workspace size" in builder
    assert b"Space + drag to pan" not in builder
    assert b"deck-validation" not in builder
    assert b"99 + 1" not in builder
    assert b"11 + 1" not in builder
    assert b"data-tags-open" in builder
    assert b'aria-label="Deck options"' in builder
    assert b"Choose commanders / partner" in builder
    assert b"Find or create a tag" in builder
    assert b"My playmats" in builder
    assert b'data-surface="night-ritual"' not in builder
    assert b"playmats/night-ritual.jpg" not in builder
    tag_results = client.get("/api/deck-tags?q=tur").get_json()["results"]
    assert tag_results[0]["name"] == "Turbo"
    assert tag_results[0]["usage_count"] == 1
    library = client.get("/build").data
    assert b"1 deck" in library
    assert b"edited this week" in library
    assert b"All 1" in library
    assert b"Favorites 0" in library
    assert b"Recently edited 1" in library
    assert b"data-deck-sort-menu" in library
    assert b"deck-sort-options" in library
    assert b"<select" not in library
    assert b'class="dl-deck-stack"' in library
    assert b"https://images.example.test/kinnan.jpg" in library
    assert b'class="dl-deck-card-hit-area"' in library
    assert b"data-deck-actions" in library
    assert b"dl-deck-favorite" in library
    assert b"Add Kinnan draft to favorites" in library
    assert b'role="menuitem">Add favorite' not in library
    assert b"Manage tags" in library
    assert b"Research cards" in library
    assert b"Research commander" in library
    assert b"Copy share link" in library
    assert b"Export decklist" in library
    assert b"Delete deck" in library
    assert b"Strategy pack builds" not in library
    assert b"Ready to edit" not in library
    assert b"mana-U" in library
    assert b"mana-G" in library
    assert b"Turbo" in library
    assert b"11 + 1" not in library
    assert b"RAMP" not in library
    assert b'class="dl-new-deck-card"' in library
    assert b'class="dl-button dl-button-primary"' in library
    favorite_view = client.get("/build?filter=favorites").data
    assert b"All 1" in favorite_view
    assert b"Favorites 0" in favorite_view
    assert b"No decks match this view" in favorite_view
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE deck_documents SET updated_at=? WHERE id=?",
            ("2024-01-02T15:04:05", deck_id),
        )
        conn.commit()
    home = client.get("/").data
    assert b"2 cards" in home
    assert b"Updated Jan 2, 2024" in home
    assert b"2024-01-02T15:04:05" not in home.replace(
        b'datetime="2024-01-02T15:04:05"', b""
    )

    image = BytesIO()
    Image.new("RGB", (16, 16), "#24324a").save(image, "PNG")
    image.seek(0)
    app.config["DECK_LAB_ASSET_DIR"] = tmp_path / "assets"
    uploaded = client.post(
        f"/api/decks/{deck_id}/playmat",
        data={"playmat": (image, "surface.png")},
        content_type="multipart/form-data",
    )
    assert uploaded.status_code == 200
    playmat_id = uploaded.get_json()["playmat_id"]
    served = client.get(f"/api/decks/{deck_id}/playmat")
    assert served.status_code == 200
    assert served.content_type == "image/png"
    assert client.get(f"/api/playmats/{playmat_id}").status_code == 200
    assert any((tmp_path / "assets").rglob("*.png"))

    deleted = client.post(f"/build/deck/{deck_id}/delete")
    assert deleted.status_code == 302
    assert client.get(f"/api/decks/{deck_id}").status_code == 404
    listed = client.get("/api/playmats").get_json()["results"]
    assert listed[0]["id"] == playmat_id
    assert client.get(f"/api/playmats/{playmat_id}").status_code == 200
    assert any((tmp_path / "assets").rglob("*.png"))
    assert b"All 0" in client.get("/build").data


def test_rollout_flags_can_hold_back_research_and_playmat(tmp_path, monkeypatch):
    path, user = _database(tmp_path)
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    monkeypatch.setenv("SABER_DECK_LAB_RESEARCH", "0")
    monkeypatch.setenv("SABER_DECK_LAB_PLAYMAT", "0")
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    _login(client, user)
    created = client.post("/build/new", json={"title": "Table-only"}).get_json()
    builder = client.get(f"/build/deck/{created['id']}")
    assert builder.status_code == 200
    assert b'data-view="playmat"' not in builder.data
    assert client.get("/research").status_code == 404
    assert client.get("/explore").status_code == 200
    assert client.get(f"/api/decks/{created['id']}/playmat").status_code == 404


def test_local_dev_guard_rejects_repository_database(monkeypatch):
    monkeypatch.setenv("SABER_DECK_LAB_DEV", "1")
    try:
        create_app()
    except ValueError as exc:
        assert "disposable database" in str(exc)
    else:
        raise AssertionError("Development guard accepted the repository database")


@pytest.mark.parametrize("role", ["user", "admin"])
@pytest.mark.parametrize("retired_quota", [None, 0, 20])
def test_builder_has_no_monthly_limit(tmp_path, monkeypatch, role, retired_quota):
    path, user = _database(tmp_path)
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE users SET role = ?, monthly_deck_quota = ? WHERE id = ?",
            (role, retired_quota, user),
        )
        conn.commit()
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    _login(client, user)
    for index in range(22):
        response = client.post("/build/new", json={"title": f"Deck {index}"})
        assert response.status_code == 201
    for route in ["/", "/build", "/profile"] + (
        ["/admin/users", f"/admin/users/{user}"] if role == "admin" else []
    ):
        response = client.get(route)
        assert response.status_code == 200
        body = response.data.lower()
        assert b"quota" not in body
        assert b"builds this month" not in body
        assert b"remaining this month" not in body
    assert b"22 decks" in client.get("/build").data
