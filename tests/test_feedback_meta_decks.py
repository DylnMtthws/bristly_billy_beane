"""DYL-44/47/49 Meta color parity, public deck avatars, and Meta density."""

from __future__ import annotations

import re
import time
from datetime import date
from io import BytesIO

from PIL import Image

from sabermetrics import db
from sabermetrics.avatars import DEFAULT_EMOJI, ensure_avatar_schema, save_avatar
from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.research import ResearchRepo
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database


def _png() -> bytes:
    output = BytesIO()
    with Image.new("RGB", (32, 32), "navy") as image:
        image.save(output, format="PNG")
    return output.getvalue()


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True


def _app_client(path, user_id, monkeypatch):
    monkeypatch.setenv("SABER_SKIP_DOTENV", "1")
    monkeypatch.setenv("SABER_RESEARCH_SYNC", "0")
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    _login(client, user_id)
    return app, client


def _seed_meta(path):
    today = date.today().isoformat()
    commanders = [
        ("red", "Red Solo", '["R"]'),
        ("blue", "Blue Solo", '["U"]'),
        ("green", "Green Solo", '["G"]'),
        ("simic", "Simic Pair", '["G","U"]'),
        ("five", "Five Color", '["W","U","B","R","G"]'),
        ("void", "Void Solo", "[]"),
    ]
    with db.connect(path) as conn:
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,color_identity,is_legal_commander,is_legal_in_99)
            VALUES(?,?,?,?,?,?,?,?)""",
            [
                (key, "o-" + key, name, 2, "Legendary Creature", colors, 1, 1)
                for key, name, colors in commanders
            ],
        )
        conn.executemany(
            "INSERT INTO decks(id,source,source_id,commander_id) VALUES(?,?,?,?)",
            [(f"d-{key}", "test", key, key) for key, _name, _colors in commanders],
        )
        conn.executemany(
            """INSERT INTO tournament_results
            (id,tournament_id,deck_id,commander_id,standing,tournament_date)
            VALUES(?,?,?,?,?,?)""",
            [
                (
                    f"r-{key}",
                    "event",
                    f"d-{key}",
                    key,
                    4 if key != "void" else None,
                    today,
                )
                for key, _name, _colors in commanders
            ],
        )
        conn.commit()
    return {key: name for key, name, _colors in commanders}


def _names(result):
    return {row["name"] for row in result["results"]}


def test_meta_color_modes_cover_mono_multicolor_and_colorless(tmp_path):
    path = tmp_path / "meta-colors.db"
    setup_database(path)
    _seed_meta(path)
    repo = ResearchRepo(path)

    include_ug = _names(
        repo.commanders(
            colors=["U", "G"], color_mode="include", observed_only=True, per_page=50
        )
    )
    assert include_ug == {"Simic Pair", "Five Color"}

    exclude_r = _names(
        repo.commanders(
            colors=["R"], color_mode="exclude", observed_only=True, per_page=50
        )
    )
    assert exclude_r == {"Blue Solo", "Green Solo", "Simic Pair", "Void Solo"}

    exactly_r = _names(
        repo.commanders(colors=["R"], color_mode="exactly", observed_only=True)
    )
    assert exactly_r == {"Red Solo"}

    exactly_ug = _names(
        repo.commanders(colors=["U", "G"], color_mode="exactly", observed_only=True)
    )
    assert exactly_ug == {"Simic Pair"}

    include_r = _names(
        repo.commanders(colors=["R"], color_mode="include", observed_only=True)
    )
    assert include_r == {"Red Solo", "Five Color"}

    colorless = _names(
        repo.commanders(colors=["U"], color_mode="exclude", observed_only=True)
    )
    assert "Void Solo" in colorless
    assert "Blue Solo" not in colorless
    assert "Simic Pair" not in colorless

    unrestricted = _names(repo.commanders(observed_only=True, per_page=50))
    for mode in ("include", "exclude", "exactly", "all", "any", "exact"):
        assert (
            _names(repo.commanders(color_mode=mode, observed_only=True, per_page=50))
            == unrestricted
        )

    legacy_all = _names(
        repo.commanders(colors=["U", "G"], color_mode="all", observed_only=True)
    )
    assert legacy_all == include_ug
    legacy_exact = _names(
        repo.commanders(colors=["U", "G"], color_mode="exact", observed_only=True)
    )
    assert legacy_exact == exactly_ug
    legacy_any = _names(
        repo.commanders(
            colors=["U", "G"], color_mode="any", observed_only=True, per_page=50
        )
    )
    assert legacy_any == {
        "Blue Solo",
        "Green Solo",
        "Simic Pair",
        "Five Color",
    }


def test_meta_ui_uses_canonical_modes_and_keeps_bookmarked_any(tmp_path, monkeypatch):
    path = tmp_path / "meta-ui.db"
    setup_database(path)
    _seed_meta(path)
    user = db.UsersRepo(path).create(
        email="meta-ui@example.test", display_name="Meta UI", status="active"
    )
    _app, client = _app_client(path, user, monkeypatch)

    page = client.get("/research/?tab=metagame&color=U&color_mode=include")
    assert page.status_code == 200
    assert b"Field view" not in page.data
    html = page.get_data(as_text=True)
    select = html.split('aria-label="Commander color match"', 1)[1].split(
        "</select>", 1
    )[0]
    assert ">Include<" in select and ">Exclude<" in select and ">Exactly<" in select
    assert "Includes all selected colors" not in select
    assert "Exact identity" not in select
    assert 'value="include"' in select and "selected" in select
    assert b"Simic Pair" in page.data
    assert b"Five Color" in page.data
    assert b"Red Solo" not in page.data
    assert b"Void Solo" not in page.data

    exclude = client.get("/research/?tab=metagame&color=R&color_mode=exclude")
    assert b"Void Solo" in exclude.data
    assert b"Blue Solo" in exclude.data
    assert b"Red Solo" not in exclude.data
    assert b"Five Color" not in exclude.data

    bookmarked_all = client.get("/research/?tab=metagame&color=R&color_mode=all")
    assert b"Red Solo" in bookmarked_all.data
    assert b"Five Color" in bookmarked_all.data
    assert b"Void Solo" not in bookmarked_all.data

    bookmarked_exact = client.get("/research/?tab=metagame&color=R&color_mode=exact")
    assert b"Red Solo" in bookmarked_exact.data
    assert b"Five Color" not in bookmarked_exact.data
    assert b'value="exactly"' in bookmarked_exact.data
    assert b"selected" in bookmarked_exact.data

    bookmarked_any = client.get(
        "/research/?tab=metagame&color=U&color=G&color_mode=any"
    )
    any_html = bookmarked_any.get_data(as_text=True)
    any_select = any_html.split('aria-label="Commander color match"', 1)[1].split(
        "</select>", 1
    )[0]
    assert 'value="any" selected' in any_select
    assert b"Blue Solo" in bookmarked_any.data
    assert b"Green Solo" in bookmarked_any.data
    assert b"Red Solo" not in bookmarked_any.data
    assert b"color_mode=any" in bookmarked_any.data or 'value="any"' in any_select

    paged = client.get("/research/?tab=metagame&color=U&color_mode=exclude&page=1")
    assert b"color_mode=exclude" in paged.data
    assert b"Field view" not in paged.data


def test_unfiltered_meta_stays_on_default_cache_for_new_and_legacy_modes(
    tmp_path, monkeypatch
):
    path = tmp_path / "meta-cache.db"
    setup_database(path)
    _seed_meta(path)
    user = db.UsersRepo(path).create(
        email="meta-cache@example.test", display_name="Cache", status="active"
    )
    calls: list[dict] = []
    original = ResearchRepo.commanders

    def wrapped(self, **kwargs):
        calls.append(kwargs)
        return original(self, **kwargs)

    monkeypatch.setattr(ResearchRepo, "commanders", wrapped)
    app, client = _app_client(path, user, monkeypatch)
    cache = app.extensions["research_default_cache"]
    assert cache.wait_for_idle(5)
    deadline = time.time() + 5
    while time.time() < deadline:
        fragment = client.get(
            "/research/?tab=metagame",
            headers={"X-Research-Fragment": "1", "X-Requested-With": "XMLHttpRequest"},
        )
        if fragment.status_code == 200 and b"dl-research-table" in fragment.data:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("default Meta snapshot was not ready")
    baseline = len(calls)
    assert client.get("/research/?tab=metagame").status_code == 200
    for mode in ("include", "exclude", "exactly", "all", "any", "exact"):
        response = client.get(f"/research/?tab=metagame&color_mode={mode}")
        assert response.status_code == 200
    assert len(calls) == baseline
    filtered = client.get("/research/?tab=metagame&color=U&color_mode=include")
    assert filtered.status_code == 200
    assert len(calls) == baseline + 1
    legacy = client.get("/research/?tab=metagame&color=U&color_mode=all")
    assert legacy.status_code == 200
    assert len(calls) == baseline + 2


def test_public_deck_avatars_are_joined_and_stay_private(tmp_path, monkeypatch):
    path = tmp_path / "deck-avatars.db"
    setup_database(path)
    with db.connect(path) as conn:
        ensure_avatar_schema(conn)
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,color_identity,oracle_text,
             is_legal_commander,is_legal_in_99)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            [
                (
                    "tymna",
                    "otym",
                    "Tymna the Weaver",
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
                    "Thrasios, Triton Hero",
                    2,
                    "Legendary Creature — Merfolk Wizard",
                    '["G","U"]',
                    "Partner (You can have two commanders if both have partner.)",
                    1,
                    1,
                ),
                (
                    "kinnan",
                    "okin",
                    "Kinnan, Bonder Prodigy",
                    2,
                    "Legendary Creature — Human Druid",
                    '["G","U"]',
                    "",
                    1,
                    1,
                ),
            ],
        )
        conn.commit()
    users = db.UsersRepo(path)
    photo = users.create(
        email="photo@example.test", display_name="Photo Author", status="active"
    )
    emoji = users.create(
        email="emoji@example.test", display_name="Emoji Author", status="active"
    )
    unnamed = users.create(email="blank@example.test", display_name="", status="active")
    private_owner = users.create(
        email="private@example.test", display_name="Hidden", status="active"
    )
    viewer = users.create(
        email="viewer@example.test", display_name="Viewer", status="active"
    )
    save_avatar(path, photo, "image", "", _png())
    save_avatar(path, emoji, "emoji", "🦊")
    repo = DeckDocumentRepo(path)
    partners = repo.create(
        photo, title="Public Partners", commander_card_ids=["tymna", "thrasios"]
    )
    repo.set_visibility(photo, partners, "public")
    imported = repo.create(
        unnamed,
        title="Imported List",
        commander_card_id="kinnan",
        source_kind="text_import",
    )
    repo.set_visibility(unnamed, imported, "public")
    fox = repo.create(emoji, title="Fox List", commander_card_id="kinnan")
    repo.set_visibility(emoji, fox, "public")
    repo.create(private_owner, title="Secret", commander_card_id="kinnan")
    listed = repo.list_public()
    payload = str(listed)
    assert "photo@example.test" not in payload
    assert "email" not in payload
    assert "Secret" not in {item["title"] for item in listed["results"]}
    by_title = {item["title"]: item for item in listed["results"]}
    partners_row = by_title["Public Partners"]
    assert partners_row["avatar"]["kind"] == "image"
    assert partners_row["avatar_user_id"] == photo
    assert "Tymna the Weaver" in partners_row["commander_names"]
    assert "Thrasios, Triton Hero" in partners_row["commander_names"]
    assert " / " in partners_row["commander_names"]
    imported_row = by_title["Imported List"]
    assert imported_row["display_name"] == "Deck Lab player"
    assert imported_row["avatar"]["kind"] == "emoji"
    assert imported_row["avatar"]["value"] == DEFAULT_EMOJI
    assert "avatar_user_id" not in imported_row

    _app, client = _app_client(path, viewer, monkeypatch)
    page = client.get("/research/?tab=decks")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    grid = body.split("dl-public-deck-grid", 1)[1]
    assert "photo@example.test" not in body
    assert "private@example.test" not in body
    assert "/profile/avatar" not in grid
    assert f"/research/avatars/{photo}" in grid
    assert "dl-public-deck-commanders" in grid
    assert "Tymna the Weaver" in grid and "Thrasios, Triton Hero" in grid
    assert " / " in grid
    assert "dl-public-deck-author" in grid
    assert "Deck Lab player" in grid
    assert "🦊" in grid
    served = client.get(f"/research/avatars/{photo}")
    assert served.status_code == 200
    assert served.mimetype == "image/png"
    assert served.headers["Cache-Control"] == "private, no-store"
    assert client.get(f"/research/avatars/{private_owner}").status_code == 404
    assert client.get(f"/research/avatars/{unnamed}").status_code == 404
    assert client.get("/research/avatars/not-a-user-id").status_code == 404
    anon = create_app(path).test_client()
    assert anon.get(f"/research/avatars/{photo}").status_code == 302
    assert b"feedback-meta-decks.css" in page.data
    assert b"Secret" not in page.data


def test_meta_density_classes_and_css_hide_columns_in_order(tmp_path, monkeypatch):
    path = tmp_path / "meta-density.db"
    setup_database(path)
    _seed_meta(path)
    user = db.UsersRepo(path).create(
        email="density@example.test", display_name="Density", status="active"
    )
    _app, client = _app_client(path, user, monkeypatch)
    page = client.get("/research/?tab=metagame&color=U&color_mode=include")
    html = page.get_data(as_text=True)
    assert "Field view" not in html
    assert "qualifying entries across the configured local corpus" not in html
    header = re.search(r"<thead><tr>(.*?)</tr></thead>", html).group(1)
    row = re.search(r"<tbody><tr>(.*?)</tr>", html).group(1)
    for column in (
        "dl-meta-col-commander",
        "dl-meta-col-identity",
        "dl-meta-col-share",
        "dl-meta-col-top16",
        "dl-meta-col-entries",
        "dl-meta-col-trend",
    ):
        assert header.count(column) == 1
        assert row.count(column) == 1
    assert "unavailable" in html or "known" in html
    assert "dl-meta-identity" in html
    assert "dl-meta-commander-name" in html
    assert "data-research-sort-menu" in html
    assert ">Trend<" in html or "trend" in html.lower()
    assert b"feedback-meta-decks.css" in page.data
