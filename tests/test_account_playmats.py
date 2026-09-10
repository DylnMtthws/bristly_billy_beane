"""Account-owned playmat library, privacy, and Night Ritual seeding."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from sabermetrics.account_playmats import (
    CUSTOM_SURFACE,
    DEFAULT_SURFACE,
    LIBRARY_SURFACE,
    AccountPlaymatRepo,
    PlaymatNotFound,
    orphan_custom_path,
    owner_presentation,
    public_presentation,
)


def _png_bytes() -> bytes:
    image = BytesIO()
    Image.new("RGB", (8, 8), "#24324a").save(image, "PNG")
    return image.getvalue()


def _write_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_png_bytes())
    return path


def _connect(db_path: Path):
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _prepare(tmp_path: Path) -> tuple[Path, str, str, Path]:
    from scripts.setup_db import setup_database

    db_path = tmp_path / "playmats.db"
    asset_dir = tmp_path / "assets"
    setup_database(db_path)
    with _connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO users(id,email,display_name) VALUES(?,?,?)",
            [("owner-a", "a@example.test", "A"), ("owner-b", "b@example.test", "B")],
        )
    return db_path, "owner-a", "owner-b", asset_dir


def _add_deck(
    db_path: Path, owner_id: str, deck_id: str, surface: str = "slate-grid"
) -> None:
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO deck_documents(id, owner_id, title) VALUES(?,?,?)",
        (deck_id, owner_id, "Draft"),
    )
    conn.execute(
        "INSERT INTO deck_presentations(deck_id, surface) VALUES(?,?)",
        (deck_id, surface),
    )
    conn.commit()
    conn.close()


def test_public_presentation_strips_private_mats():
    raw = {
        "surface": "library",
        "playmat_id": "stolen",
        "custom_surface_path": "/secret/mat.png",
        "zoom": 1.2,
    }
    view = public_presentation(raw)
    assert view["surface"] == DEFAULT_SURFACE
    assert view["playmat_id"] is None
    assert view["has_custom_surface"] is False
    assert "custom_surface_path" not in view
    assert view["zoom"] == 1.2
    ritual = public_presentation({"surface": "night-ritual"})
    assert ritual["surface"] == DEFAULT_SURFACE


def test_owner_presentation_falls_back_for_legacy_and_forged_ids():
    fallback = owner_presentation(
        {"surface": "night-ritual", "playmat_id": "x"}, owned_ids=set()
    )
    assert fallback["surface"] == DEFAULT_SURFACE
    assert fallback["playmat_id"] is None
    forged = owner_presentation(
        {"surface": LIBRARY_SURFACE, "playmat_id": "other-mat"},
        owned_ids={"mine"},
    )
    assert forged["surface"] == DEFAULT_SURFACE
    owned = owner_presentation(
        {"surface": LIBRARY_SURFACE, "playmat_id": "mine"},
        owned_ids={"mine"},
    )
    assert owned["surface"] == LIBRARY_SURFACE
    assert owned["playmat_id"] == "mine"
    custom = owner_presentation(
        {"surface": CUSTOM_SURFACE, "custom_surface_path": "/tmp/deck.png"},
        owned_ids=set(),
    )
    assert custom["has_custom_surface"] is True
    assert custom["playmat_id"] is None


def test_orphan_custom_path_never_returns_library_files(tmp_path):
    asset_dir = tmp_path / "assets"
    library = asset_dir / "playmats" / "owner-a" / "mat.png"
    _write_png(library)
    legacy = _write_png(asset_dir / "deck-old.png")
    assert orphan_custom_path(str(legacy), asset_dir) == legacy.resolve()
    assert orphan_custom_path(str(library), asset_dir) is None
    assert orphan_custom_path(str(tmp_path / "outside.png"), asset_dir) is None


def test_two_users_cannot_list_or_open_foreign_playmats(tmp_path):
    db_path, owner_a, owner_b, asset_dir = _prepare(tmp_path)
    repo = AccountPlaymatRepo(db_path)
    saved = repo.add_upload(
        owner_a, title="Night Ritual", content=_png_bytes(), asset_dir=asset_dir
    )
    assert [item["id"] for item in repo.list_for_owner(owner_a)] == [saved["id"]]
    assert repo.list_for_owner(owner_b) == []
    assert "file_path" not in repo.public_summaries(owner_a)[0]
    path, mime = repo.open_owned(owner_a, saved["id"], asset_dir)
    assert path.is_file()
    assert mime == "image/png"
    with pytest.raises(PlaymatNotFound):
        repo.open_owned(owner_b, saved["id"], asset_dir)
    with pytest.raises(PlaymatNotFound):
        repo.open_owned(owner_a, "forged-id", asset_dir)
    _add_deck(db_path, owner_a, "deck-a")
    conn = _connect(db_path)
    conn.execute(
        "UPDATE deck_presentations SET surface=?, playmat_id=? WHERE deck_id='deck-a'",
        (LIBRARY_SURFACE, saved["id"]),
    )
    conn.commit()
    conn.close()
    served, mime = repo.open_deck_playmat(owner_a, "deck-a", asset_dir)
    assert served.is_file()
    assert mime == "image/png"
    with pytest.raises(PlaymatNotFound):
        repo.open_deck_playmat(owner_b, "deck-a", asset_dir)


def test_seed_night_ritual_is_idempotent_and_owner_scoped(tmp_path):
    db_path, owner_a, owner_b, asset_dir = _prepare(tmp_path)
    _add_deck(db_path, owner_a, "deck-a", surface="night-ritual")
    _add_deck(db_path, owner_b, "deck-b", surface="night-ritual")
    image = _write_png(tmp_path / "night-ritual.png")
    repo = AccountPlaymatRepo(db_path)
    first = repo.seed_night_ritual(owner_a, image, asset_dir)
    second = repo.seed_night_ritual(owner_a, image, asset_dir)
    assert first["id"] == second["id"]
    assert first["migrated"] == 1
    assert second["migrated"] == 0
    assert len(repo.list_for_owner(owner_a)) == 1
    assert repo.list_for_owner(owner_b) == []
    conn = _connect(db_path)
    owner_row = conn.execute(
        "SELECT surface, playmat_id FROM deck_presentations WHERE deck_id='deck-a'"
    ).fetchone()
    other_row = conn.execute(
        "SELECT surface, playmat_id FROM deck_presentations WHERE deck_id='deck-b'"
    ).fetchone()
    conn.close()
    assert owner_row["surface"] == LIBRARY_SURFACE
    assert owner_row["playmat_id"] == first["id"]
    assert other_row["surface"] == "night-ritual"
    assert other_row["playmat_id"] is None
    with pytest.raises(ValueError, match="Unknown owner id"):
        repo.seed_night_ritual("no-such-user", image, asset_dir)


def test_deck_delete_and_replacement_keep_library_images(tmp_path):
    db_path, owner_a, owner_b, asset_dir = _prepare(tmp_path)
    repo = AccountPlaymatRepo(db_path)
    first = repo.add_upload(
        owner_a, title="One", content=_png_bytes(), asset_dir=asset_dir
    )
    second = repo.add_upload(
        owner_a, title="Two", content=_png_bytes(), asset_dir=asset_dir
    )
    _add_deck(db_path, owner_a, "deck-a")
    conn = _connect(db_path)
    conn.execute(
        "UPDATE deck_presentations SET surface=?, playmat_id=? WHERE deck_id='deck-a'",
        (LIBRARY_SURFACE, first["id"]),
    )
    conn.commit()
    leftover = conn.execute(
        "SELECT custom_surface_path FROM deck_presentations WHERE deck_id='deck-a'"
    ).fetchone()[0]
    conn.execute("DELETE FROM deck_documents WHERE id='deck-a'")
    conn.commit()
    still = conn.execute(
        "SELECT id FROM account_playmats WHERE owner_id=? ORDER BY title",
        (owner_a,),
    ).fetchall()
    conn.close()
    assert leftover is None
    assert [row["id"] for row in still] == [first["id"], second["id"]]
    assert repo.open_owned(owner_a, first["id"], asset_dir)[0].is_file()
    assert repo.open_owned(owner_a, second["id"], asset_dir)[0].is_file()
    with pytest.raises(PlaymatNotFound):
        repo.open_deck_playmat(owner_b, "deck-a", asset_dir)


def test_selecting_saved_mat_rejects_forged_and_legacy_commands(tmp_path):
    from sabermetrics.deck_documents import DeckDocumentRepo, InvalidCommand

    db_path, owner_a, owner_b, asset_dir = _prepare(tmp_path)
    conn = _connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cards (
            id TEXT PRIMARY KEY, oracle_id TEXT, name TEXT, mana_cost TEXT,
            cmc REAL, type_line TEXT, oracle_text TEXT, color_identity TEXT,
            is_legal_commander INTEGER, is_legal_in_99 INTEGER, image_uri TEXT
        );
        CREATE TABLE IF NOT EXISTS deck_zones (
            id TEXT PRIMARY KEY, deck_id TEXT NOT NULL,
            name TEXT NOT NULL, sort_order INTEGER NOT NULL,
            layout_mode TEXT NOT NULL DEFAULT 'spread', x REAL, y REAL
        );
        CREATE TABLE IF NOT EXISTS deck_entries (
            id TEXT PRIMARY KEY, deck_id TEXT NOT NULL, zone_id TEXT,
            card_id TEXT, oracle_id TEXT, name TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1, is_commander INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0, role TEXT, type_line TEXT,
            mana_cost TEXT, mana_value REAL, oracle_text TEXT, color_identity TEXT,
            image_uri TEXT
        );
        CREATE TABLE IF NOT EXISTS deck_view_preferences (
            owner_id TEXT NOT NULL, deck_id TEXT NOT NULL,
            view_mode TEXT NOT NULL DEFAULT 'playmat',
            display_mode TEXT NOT NULL DEFAULT 'text',
            group_mode TEXT NOT NULL DEFAULT 'zone',
            sort_mode TEXT NOT NULL DEFAULT 'manual',
            density TEXT NOT NULL DEFAULT 'compact',
            collapsed_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY(owner_id, deck_id)
        );
        CREATE TABLE IF NOT EXISTS deck_mutations (
            deck_id TEXT NOT NULL, owner_id TEXT NOT NULL, mutation_id TEXT NOT NULL,
            response_revision INTEGER NOT NULL,
            PRIMARY KEY(deck_id, owner_id, mutation_id)
        );
        CREATE TABLE IF NOT EXISTS activity_events (
            id TEXT PRIMARY KEY, actor_id TEXT, action TEXT NOT NULL,
            subject_kind TEXT NOT NULL, subject_id TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS deck_tags (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, normalized_name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS deck_tag_assignments (
            deck_id TEXT NOT NULL, tag_id TEXT NOT NULL, created_by TEXT,
            PRIMARY KEY(deck_id, tag_id)
        );
        """)
    conn.commit()
    conn.close()
    decks = DeckDocumentRepo(db_path)
    deck_id = decks.create(owner_a, title="Owned")
    playmats = AccountPlaymatRepo(db_path)
    saved = playmats.add_upload(
        owner_a, title="Mine", content=_png_bytes(), asset_dir=asset_dir
    )
    other = playmats.add_upload(
        owner_b, title="Theirs", content=_png_bytes(), asset_dir=asset_dir
    )
    with pytest.raises(InvalidCommand, match="Unknown playmat surface"):
        decks.apply_commands(
            owner_a,
            deck_id,
            expected_revision=0,
            mutation_id="legacy-ritual",
            commands=[{"type": "update_presentation", "surface": "night-ritual"}],
        )
    with pytest.raises(InvalidCommand, match="Unknown playmat surface"):
        decks.apply_commands(
            owner_a,
            deck_id,
            expected_revision=0,
            mutation_id="forged-mat",
            commands=[{"type": "update_presentation", "playmat_id": other["id"]}],
        )
    document = decks.apply_commands(
        owner_a,
        deck_id,
        expected_revision=0,
        mutation_id="select-mine",
        commands=[{"type": "update_presentation", "playmat_id": saved["id"]}],
    )
    assert document["presentation"]["surface"] == LIBRARY_SURFACE
    assert document["presentation"]["playmat_id"] == saved["id"]
    assert saved["id"] in {item["id"] for item in document["playmats"]}
    leftover = decks.delete(owner_a, deck_id)
    assert leftover is None
    assert playmats.open_owned(owner_a, saved["id"], asset_dir)[0].is_file()


def test_two_user_playmat_routes(tmp_path, monkeypatch):
    from sabermetrics import db
    from sabermetrics.ui.app import create_app
    from scripts.setup_db import setup_database

    path = tmp_path / "routes.db"
    setup_database(path)
    owner_a = db.UsersRepo(path).create(
        email="a@example.test", display_name="A", status="active"
    )
    owner_b = db.UsersRepo(path).create(
        email="b@example.test", display_name="B", status="active"
    )
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    app = create_app(path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
        DECK_LAB_ASSET_DIR=tmp_path / "assets",
    )
    client = app.test_client()

    def login(user_id: str) -> None:
        with client.session_transaction() as session:
            session["_user_id"] = user_id
            session["_fresh"] = True

    login(owner_a)
    deck_a = client.post("/build/new", json={"title": "A"}).get_json()["id"]
    image = BytesIO(_png_bytes())
    uploaded = client.post(
        f"/api/decks/{deck_a}/playmat",
        data={"playmat": (image, "mat.png")},
        content_type="multipart/form-data",
    )
    assert uploaded.status_code == 200
    playmat_id = uploaded.get_json()["playmat_id"]
    assert client.get("/api/playmats").get_json()["results"][0]["id"] == playmat_id
    assert client.get(f"/api/playmats/{playmat_id}").status_code == 200
    share = client.post(f"/api/decks/{deck_a}/share").get_json()["url"]
    token = share.rstrip("/").split("/")[-1]
    shared = client.get(f"/shared/deck/{token}")
    assert shared.status_code == 200
    assert b"slate-grid" in shared.data
    assert playmat_id.encode() not in shared.data
    assert client.get(f"/shared/deck/{token}/playmat").status_code == 404

    login(owner_b)
    assert client.get("/api/playmats").get_json()["results"] == []
    assert client.get(f"/api/playmats/{playmat_id}").status_code == 404
    assert client.get(f"/api/decks/{deck_a}/playmat").status_code == 404
    deck_b = client.post("/build/new", json={"title": "B"}).get_json()["id"]
    stolen = client.post(
        f"/api/decks/{deck_b}/commands",
        json={
            "expected_revision": 0,
            "mutation_id": "steal",
            "commands": [{"type": "update_presentation", "playmat_id": playmat_id}],
        },
    )
    assert stolen.status_code == 400
    ritual = client.post(
        f"/api/decks/{deck_b}/commands",
        json={
            "expected_revision": 0,
            "mutation_id": "ritual",
            "commands": [{"type": "update_presentation", "surface": "night-ritual"}],
        },
    )
    assert ritual.status_code == 400
    builder = client.get(f"/build/deck/{deck_b}").data
    assert b"My playmats" in builder
    assert b'data-surface="night-ritual"' not in builder
