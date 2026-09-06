"""The redesigned routes are feature-gated and work on disposable data."""

from io import BytesIO

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
        monthly_deck_quota=20,
    )
    with db.connect(path) as conn:
        conn.execute("""INSERT INTO cards
            (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99,image_uri)
            VALUES('kinnan','oracle-kinnan','Kinnan Test','{G}{U}',2,
             'Legendary Creature — Human Druid','Mana text','["G","U"]',1,1,NULL)""")
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

    assert b"Research the format" in client.get("/").data
    assert b"Build" in client.get("/build").data
    response = client.get("/research?q=Kinnan")
    assert response.status_code == 200
    assert b"Kinnan Test" in response.data
    assert (
        b"Compare commanders"
        in client.get("/research/compare?left=kinnan&right=kinnan").data
    )
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
    assert b"deck-document-data" in client.get(f"/build/deck/{deck_id}").data

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
    served = client.get(f"/api/decks/{deck_id}/playmat")
    assert served.status_code == 200
    assert served.content_type == "image/png"


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
