"""Coordinator checks for private library image authorization and caching."""

from io import BytesIO

from PIL import Image

from sabermetrics import db
from sabermetrics.account_playmats import AccountPlaymatRepo
from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database


def test_private_images_recheck_identity_and_cannot_be_shared_cached(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SABER_AUTH_MODE", "password")
    monkeypatch.setenv("SABER_DECK_LAB_BUILDER", "true")
    monkeypatch.setenv("SABER_DECK_LAB_PLAYMAT", "true")
    path = tmp_path / "app.db"
    setup_database(path)
    users = db.UsersRepo(path)
    owner = users.create(
        email="owner@example.test", display_name="Owner", role="user", status="active"
    )
    other = users.create(
        email="other@example.test", display_name="Other", role="user", status="active"
    )
    image = BytesIO()
    Image.new("RGB", (8, 8), "#345678").save(image, "PNG")
    assets = tmp_path / "assets"
    asset = AccountPlaymatRepo(path).add_upload(
        owner, title="Private mat", content=image.getvalue(), asset_dir=assets
    )
    repo = DeckDocumentRepo(path)
    deck = repo.create(owner)
    repo.assign_library_playmat(owner, deck, asset["id"])
    app = create_app(db_path=path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, DECK_LAB_ASSET_DIR=assets)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = owner
        session["_fresh"] = True
    for route in (f'/api/playmats/{asset["id"]}', f"/api/decks/{deck}/playmat"):
        response = client.get(route)
        assert response.status_code == 200
        assert response.data == image.getvalue()
        assert "no-store" in response.headers["Cache-Control"]
        assert "public" not in response.headers["Cache-Control"]
    share = repo.create_share(owner, deck)
    assert client.get(f"/shared/deck/{share}/playmat").status_code == 404
    with client.session_transaction() as session:
        session["_user_id"] = other
    assert client.get(f'/api/playmats/{asset["id"]}').status_code == 404
    assert client.get(f"/api/decks/{deck}/playmat").status_code == 404
    assert client.get("/api/playmats").get_json()["results"] == []
