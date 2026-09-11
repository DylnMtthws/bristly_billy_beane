"""Coordinator checks of authenticated workspace behavior across save/reload."""

import uuid

from sabermetrics import db
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database


def test_workspace_layout_commands_preserve_ownership_and_revision(
    tmp_path, monkeypatch
):
    path = tmp_path / "workspace.db"
    setup_database(path)
    users = db.UsersRepo(path)
    owner = users.create(
        email="owner@example.test", display_name="Owner", status="active"
    )
    other = users.create(
        email="other@example.test", display_name="Other", status="active"
    )
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    monkeypatch.setenv("SABER_DECK_LAB_PLAYMAT", "1")
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = owner
        session["_fresh"] = True
    created = client.post("/build/new", json={"title": "Grid persistence"})
    assert created.status_code == 201
    deck_id = created.json["id"]
    endpoint = f"/api/decks/{deck_id}"
    original = client.get(endpoint).json
    zone_id = original["zones"][0]["id"]
    packet = {
        "expected_revision": original["revision"],
        "mutation_id": str(uuid.uuid4()),
        "commands": [
            {"type": "set_zone_layout", "zone_id": zone_id, "layout": "grid"},
            {"type": "move_zone", "zone_id": zone_id, "x": 300, "y": 200, "layer": 9},
        ],
    }
    changed = client.post(endpoint + "/commands", json=packet)
    assert changed.status_code == 200
    saved = client.get(endpoint).json
    assert saved["zones"][0]["layout_mode"] == "grid"
    assert saved["zones"][0]["layer"] == 9
    assert saved["zones"][0]["sort_order"] == original["zones"][0]["sort_order"]
    assert saved["zones"][0]["x"] == 300
    assert client.post(endpoint + "/commands", json=packet).status_code == 200
    packet["mutation_id"] = str(uuid.uuid4())
    assert client.post(endpoint + "/commands", json=packet).status_code == 409
    with client.session_transaction() as session:
        session["_user_id"] = other
    assert client.get(endpoint).status_code == 404
    packet["expected_revision"] = saved["revision"]
    assert client.post(endpoint + "/commands", json=packet).status_code == 404
