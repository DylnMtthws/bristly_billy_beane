"""Offline installed-image smoke test; --serve provides a fake local preview."""

import argparse
import io
import json
import os
import re
import runpy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from PIL import Image

from sabermetrics import db
from sabermetrics.ui.app import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    ids = [f"00000000-0000-4000-8000-{i:012d}" for i in range(1, 8)]
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("SABER_", "LINEAR_", "RESEND_", "MTG_", "CEDH_"))
    }
    env.update(
        {
            "SABER_AUTH_MODE": "password",
            "SABER_SECRET_KEY": "fake-preview-session-key",
            "SABER_COOKIE_SECURE": "0",
            "LINEAR_FEEDBACK_ENABLED": "true",
            "LINEAR_API_KEY": "fake-offline-linear-key",
            "LINEAR_TEAM_ID": ids[0],
            "LINEAR_PROJECT_ID": ids[1],
            "LINEAR_TRIAGE_STATE_ID": ids[2],
            "LINEAR_LABEL_IDS": json.dumps(
                dict(
                    zip(
                        ("user-feedback", "bug", "ux", "suggestion"),
                        ids[3:],
                        strict=True,
                    )
                )
            ),
        }
    )
    sent = []
    uploaded = []

    def handler(request):
        if request.method == "PUT":
            uploaded.append(bytes(request.content))
            return httpx.Response(200)
        payload = json.loads(request.content)
        if "FeedbackUpload" in payload["query"]:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "fileUpload": {
                            "success": True,
                            "uploadFile": {
                                "uploadUrl": "https://storage.googleapis.com/offline-upload",
                                "assetUrl": "https://uploads.linear.app/offline/image.png",
                                "headers": [],
                            },
                        }
                    }
                },
            )
        issue = payload["variables"]["input"]
        sent.append(issue)
        return httpx.Response(
            200,
            json={
                "data": {"issueCreate": {"success": True, "issue": {"id": issue["id"]}}}
            },
        )

    real_client = httpx.Client
    with (
        TemporaryDirectory() as directory,
        patch.dict(os.environ, env, clear=True),
        patch.object(
            httpx,
            "Client",
            lambda **kwargs: real_client(
                transport=httpx.MockTransport(handler), **kwargs
            ),
        ),
    ):
        path = Path(directory) / "smoke.db"
        setup = Path("/app/scripts/setup_db.py")
        if not setup.exists():
            setup = Path(__file__).with_name("setup_db.py")
        runpy.run_path(str(setup))["setup_database"](path)
        uid = db.UsersRepo(path).create(
            email="preview@example.com",
            display_name="Preview User",
            role="admin",
            status="active",
            password_hash=db.hash_password("preview-password-only"),
        )
        app = create_app(path)
        if args.serve:
            app.run(host="127.0.0.1", port=5077, use_reloader=False)
            return
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = uid
        page = client.get("/profile")
        assert b'aria-label="Send feedback"' in page.data
        csrf = re.search(rb'name="csrf-token" content="([^"]+)"', page.data)[1].decode()
        for asset in ("issue-feedback.js", "issue-feedback.css"):
            assert client.get("/static/" + asset).status_code == 200
        raw = io.BytesIO()
        Image.effect_noise((600, 600), 100).save(raw, format="PNG")
        response = client.post(
            "/feedback/submit",
            data={
                "csrf_token": csrf,
                "submission_id": ids[0],
                "category": "ux",
                "description": "This is an offline installed-container report.",
                "page_path": "/profile?secret=excluded",
                "screenshot": (
                    io.BytesIO(raw.getvalue()),
                    "screenshot.png",
                    "image/png",
                ),
            },
        )
        assert response.status_code == 200 and response.json["ok"]
        assert len(sent) == len(uploaded) == 1
        assert sent[0]["projectId"] == ids[1] and sent[0]["labelIds"] == [
            ids[3],
            ids[5],
        ]
        assert "secret=excluded" not in sent[0]["description"]
        assert b"linear" not in response.data.lower()
        assert b'id="feedback-launcher"' not in app.test_client().get("/login").data
        print(
            "Installed feedback: authenticated widget, image sanitization, private upload, routing and confirmation passed (mock delivery)"
        )


if __name__ == "__main__":
    main()
