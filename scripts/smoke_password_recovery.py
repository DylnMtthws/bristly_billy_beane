"""Run via stdin in the installed image, with networking disabled by Docker."""

import os
import re
import runpy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sabermetrics import db
from sabermetrics.ui.app import create_app
from sabermetrics.ui.recovery import RecoveryMailer

with TemporaryDirectory() as directory:
    path = Path(directory) / "recovery.db"
    runpy.run_path("/app/scripts/setup_db.py")["setup_database"](path)
    uid = db.UsersRepo(path).create(
        email="admin@example.com",
        role="admin",
        status="active",
        password_hash=db.hash_password("installed-old-password"),
    )
    with (
        patch.dict(
            os.environ,
            {
                "RESEND_API_KEY": "ci-email-key",
                "SABER_EMAIL_FROM": "Deck Lab <accounts@example.com>",
                "SABER_PUBLIC_URL": "https://decklab.example.com",
                "SABER_SECRET_KEY": "ci-session-key",
                "SABER_AUTH_MODE": "password",
            },
        ),
        patch.object(RecoveryMailer, "send", return_value=True) as send,
    ):
        app = create_app(path)
        app.config["SESSION_COOKIE_SECURE"] = False
        client = app.test_client()
        worker = app.extensions["recovery_mailer"].executor
        try:
            assert b"Forgot password?" in client.get("/login").data
            page = client.get("/forgot-password")
            csrf = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', page.data)[
                1
            ].decode()
            response = client.post(
                "/forgot-password",
                data={
                    "email": "admin@example.com",
                    "csrf_token": csrf,
                },
            )
            assert response.status_code == 200
            worker.submit(lambda: None).result(timeout=5)
            body = send.call_args.args[2]
            token = body.split("/reset-password#")[1].splitlines()[0]
            page = client.get("/reset-password")
            assert page.status_code == 200 and b"Choose a new password" in page.data
            assert "script-src 'self'" in page.headers["Content-Security-Policy"]
            csrf = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', page.data)[
                1
            ].decode()
            assert b"replaceState" in client.get("/static/password-reset.js").data
            assert client.get("/static/recovery.css").status_code == 200
            response = client.post(
                "/reset-password",
                data={
                    "csrf_token": csrf,
                    "token": token,
                    "password": "installed-new-password",
                    "confirm": "installed-new-password",
                },
            )
            assert response.status_code == 302
            row = db.UsersRepo(path).get(uid)
            assert row["role"] == "admin" and row["session_version"] == 1
            assert db.verify_password(row["password_hash"], "installed-new-password")
            # Exercise automatic invitations through the installed admin UI,
            # including CSRF, configured link origin and visible send failure.
            worker.submit(lambda: None).result(timeout=5)
            page = client.get("/login")
            csrf = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', page.data)[
                1
            ].decode()
            response = client.post(
                "/login",
                data={
                    "email": "admin@example.com",
                    "password": "installed-new-password",
                    "csrf_token": csrf,
                },
            )
            assert response.status_code == 302
            page = client.get("/admin/users")
            assert b"Create &amp; send invite" in page.data
            csrf = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', page.data)[
                1
            ].decode()
            send.reset_mock()
            response = client.post(
                "/admin/users/create",
                data={
                    "email": "player@example.com",
                    "csrf_token": csrf,
                },
                follow_redirects=True,
            )
            assert (
                response.data.count(b"Invitation email sent to player@example.com.")
                == 1
            )
            assert (
                send.call_count == 1 and send.call_args.args[0] == "player@example.com"
            )
            assert "https://decklab.example.com/invite/" in send.call_args.args[2]
            user = db.UsersRepo(path).get_by_email("player@example.com")
            assert user["status"] == "invited"
            send.return_value = False
            response = client.post(
                f"/admin/users/{user['id']}/reinvite",
                data={
                    "csrf_token": csrf,
                },
                follow_redirects=True,
            )
            assert b"couldn&#39;t confirm" in response.data
            assert b"Try Resend invite" in response.data
        finally:
            worker.shutdown(wait=True)
print("Installed recovery, CSRF, admin reset and automatic invitation checks passed")
