"""Exercise the installed refactor with CSRF, persistence, and isolated state."""

import io
import json
import os
import re
import runpy
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from sabermetrics import db
from sabermetrics.ui.app import create_app


def main():
    for name in list(os.environ):
        if name.startswith(("SABER_", "MTG_", "CEDH_", "LINEAR_", "RESEND_")):
            os.environ.pop(name)
    os.environ.update(
        SABER_SKIP_DOTENV="1",
        SABER_AUTH_MODE="password",
        SABER_SECRET_KEY="installed-smoke-only",
        SABER_COOKIE_SECURE="0",
        SABER_DECK_LAB_REDESIGN="1",
        SABER_DECK_LAB_DEV="0",
    )
    setup = runpy.run_path("/app/scripts/setup_db.py")["setup_database"]
    with TemporaryDirectory() as directory:
        path = Path(directory) / "app.db"
        setup(path)
        user = db.UsersRepo(path).create(
            email="release@example.test",
            display_name="Release test",
            role="admin",
            status="active",
            password_hash=db.hash_password("test-password"),
        )
        with db.connect(path) as conn:
            conn.execute(
                """INSERT INTO cards(id,oracle_id,name,type_line,cmc,
                color_identity,is_legal_commander,is_legal_in_99)
                VALUES('commander','commander','Test Commander','Legendary Creature',2,'[]',1,1)"""
            )
            conn.execute("""INSERT INTO cards(id,oracle_id,name,type_line,cmc,
                color_identity,is_legal_commander,is_legal_in_99)
                VALUES('ring','ring','Sol Ring','Artifact',1,'[]',0,1)""")
            conn.execute("UPDATE cards SET oracle_text='Partner' WHERE id='commander'")
            conn.execute(
                "INSERT INTO cards(id,oracle_id,name,type_line,oracle_text,cmc,color_identity,is_legal_commander,is_legal_in_99) VALUES('partner','partner','Test Partner','Legendary Creature','Partner',2,'[]',1,1)"
            )
            conn.commit()
        app = create_app(path)
        app.config["TESTING"] = True
        client = app.test_client()
        login = client.get("/login")
        token = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', login.data)[
            1
        ].decode()
        assert (
            client.post(
                "/login",
                data={
                    "email": "release@example.test",
                    "password": "test-password",
                    "csrf_token": token,
                },
            ).status_code
            == 302
        )
        home = client.get("/")
        assert b"Research the format" in home.data
        # CSRF remains enabled throughout this installed-image check.
        with client.session_transaction() as session:
            assert session["_user_id"].split(":")[0] == user
        token = re.search(rb'name="csrf-token" content="([^"]+)"', home.data)[
            1
        ].decode()
        headers = {"X-CSRFToken": token}
        assert client.post("/build/new", json={"title": "Rejected"}).status_code == 400
        response = client.post(
            "/build/new",
            json={
                "title": "Installed release smoke",
                "commander_card_ids": ["commander", "partner"],
            },
            headers=headers,
        )
        assert response.status_code == 201, response.data
        deck_id = response.json["id"]
        document = client.get(f"/api/decks/{deck_id}").json
        edited = client.post(
            f"/api/decks/{deck_id}/commands",
            headers=headers,
            json={
                "expected_revision": 0,
                "mutation_id": "installed-smoke",
                "commands": [
                    {
                        "type": "add_card",
                        "card_id": "ring",
                        "zone_id": document["zones"][0]["id"],
                    }
                ],
            },
        )
        assert edited.status_code == 200
        assert edited.json["validation"]["library_count"] == 1
        assert edited.json["validation"]["commander_count"] == 2
        assert edited.json["validation"]["library_target"] == 98
        for route in (
            "/build",
            f"/build/deck/{deck_id}",
            "/research?q=Test",
            "/research?tab=cards&q=Sol",
            "/profile",
            "/admin/",
            "/admin/users",
        ):
            assert client.get(route).status_code == 200, route
        for asset in (
            "playmats/night-ritual.jpg",
            "avatar.css",
            "profile-avatar.js",
            "deck-lab-builder.js",
            "deck-lab-commanders.js",
            "deck-lab-research.js",
            "deck-lab.css",
        ):
            response = client.get("/static/" + asset)
            assert response.status_code == 200 and response.data, asset

        def picture():
            output = io.BytesIO()
            Image.new("RGB", (480, 240), "navy").save(output, format="PNG")
            output.seek(0)
            return output

        assert (
            client.post(
                "/profile/avatar",
                headers=headers,
                data={
                    "avatar_kind": "image",
                    "avatar_image": (picture(), "avatar.png"),
                },
            ).status_code
            == 302
        )
        assert (
            client.post(
                f"/api/decks/{deck_id}/playmat",
                headers=headers,
                data={"playmat": (picture(), "mat.png")},
            ).status_code
            == 200
        )
        avatar = client.get("/profile/avatar").data
        playmat = client.get(f"/api/decks/{deck_id}/playmat").data
        cookie = client.get_cookie("session")
        setup(path)
        restarted = create_app(path)
        reopened = restarted.test_client()
        reopened.set_cookie("session", cookie.value)
        assert (
            reopened.get(f"/api/decks/{deck_id}").json["validation"]["library_count"]
            == 1
        )
        assert reopened.get("/profile/avatar").data == avatar
        assert reopened.get(f"/api/decks/{deck_id}/playmat").data == playmat
        assert restarted.test_client().get(f"/api/decks/{deck_id}").status_code in (
            302,
            401,
        )
        print(
            json.dumps(
                {
                    "installed_refactor": "passed",
                    "csrf": "enabled",
                    "restart_persistence": "passed",
                    "nested_assets": "passed",
                }
            )
        )


if __name__ == "__main__":
    main()
