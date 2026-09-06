"""Report delivery, private images, retry identity and authenticated UI."""

import io
import json
import logging
import re
import time
import uuid

import httpx
import pytest
from PIL import Image, PngImagePlugin
from werkzeug.datastructures import FileStorage, MultiDict

from sabermetrics import db
from sabermetrics.ui.app import create_app
from sabermetrics.ui.extensions import limiter
from sabermetrics.ui.feedback_images import sanitize_image
from sabermetrics.ui.feedback_linear import LinearConfig
from sabermetrics.ui.feedback_store import (
    FeedbackReceipts,
    ReceiptBusy,
    ReceiptConflict,
)
from scripts.setup_db import setup_database

TEAM, PROJECT, TRIAGE = [str(uuid.uuid4()) for _ in range(3)]
LABELS = {
    name: str(uuid.uuid4()) for name in ("user-feedback", "bug", "ux", "suggestion")
}
ASSET = "https://uploads.linear.app/test/feedback.png"
SIGNED = "https://storage.googleapis.com/linear-test/image?signature=private-upload-signature"


def configure(monkeypatch):
    for name, value in {
        "LINEAR_FEEDBACK_ENABLED": "true",
        "LINEAR_API_KEY": "lin_api_fake-test-key",
        "LINEAR_TEAM_ID": TEAM,
        "LINEAR_PROJECT_ID": PROJECT,
        "LINEAR_TRIAGE_STATE_ID": TRIAGE,
        "LINEAR_LABEL_IDS": json.dumps(LABELS),
        "SABER_AUTH_MODE": "password",
        "SABER_SECRET_KEY": "test-only-feedback-session",
        "SABER_BUILD_SHA": "test-build-123",
    }.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def app(tmp_path, monkeypatch):
    configure(monkeypatch)
    path = tmp_path / "feedback.db"
    setup_database(path)
    app = create_app(path)
    app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    with app.app_context():
        limiter.reset()
    return app


@pytest.fixture
def user(app):
    return db.UsersRepo(app.config["DB_PATH"]).create(
        email="reporter@example.com", display_name="Reporter", status="active"
    )


@pytest.fixture
def client(app, user):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = user
    return client


def image_bytes(kind="PNG"):
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(out, format=kind)
    return out.getvalue()


def form_data(client, **changes):
    page = client.get("/profile")
    csrf = re.search(rb'name="csrf-token" content="([^"]+)"', page.data)[1].decode()
    data = {
        "csrf_token": csrf,
        "category": "bug",
        "description": "The deck button is not responding.",
        "submission_id": str(uuid.uuid4()),
        "page_path": "/lab/?private=query#secret-fragment",
        "viewport_width": "390",
        "viewport_height": "844",
    }
    data.update(changes)
    return data


@pytest.fixture
def provider(monkeypatch):
    state = {
        "calls": [],
        "issues": {},
        "uploads": [],
        "failure": None,
        "put_status": 200,
        "upload_url": SIGNED,
        "asset_url": ASSET,
    }
    real_client = httpx.Client

    def handler(request):
        if request.method == "PUT":
            assert "authorization" not in request.headers
            state["uploads"].append(bytes(request.content))
            return httpx.Response(state["put_status"])
        assert str(request.url) == "https://api.linear.app/graphql"
        assert request.headers["Authorization"] == "lin_api_fake-test-key"
        body = json.loads(request.content)
        state["calls"].append(body)
        failure = state["failure"]
        if isinstance(failure, int):
            return httpx.Response(
                failure, json={"errors": [{"message": "sensitive provider detail"}]}
            )
        if failure == "graphql":
            return httpx.Response(
                200,
                json={"errors": [{"extensions": {"code": "RATELIMITED"}}], "data": {}},
            )
        if "FeedbackUpload" in body["query"]:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "fileUpload": {
                            "success": True,
                            "uploadFile": {
                                "uploadUrl": state["upload_url"],
                                "assetUrl": state["asset_url"],
                                "headers": [
                                    {
                                        "key": "x-goog-meta-test",
                                        "value": "required-header",
                                    }
                                ],
                            },
                        }
                    }
                },
            )
        if "FeedbackReceipt" in body["query"]:
            identity = body["variables"]["id"]
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issues": {
                            "nodes": (
                                [{"id": identity}]
                                if identity in state["issues"]
                                else []
                            )
                        }
                    }
                },
            )
        issue = body["variables"]["input"]
        if failure == "before_create":
            raise httpx.ReadTimeout("lin_api_fake-test-key private-upload-signature")
        assert issue["id"] not in state["issues"], "Duplicate issue creation"
        state["issues"][issue["id"]] = issue
        if failure == "after_create":
            raise httpx.ReadTimeout("lin_api_fake-test-key private-upload-signature")
        return httpx.Response(
            200,
            json={
                "data": {"issueCreate": {"success": True, "issue": {"id": issue["id"]}}}
            },
        )

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return state


@pytest.mark.parametrize("category", ["bug", "ux", "suggestion"])
def test_report_routing_context_and_confirmation_only(client, provider, category):
    data = form_data(client, category=category)
    response = client.post(
        "/feedback/submit",
        data=data,
        headers={"User-Agent": "Mozilla/5.0 (iPhone) Version/18.1 Mobile Safari/604.1"},
    )
    assert response.status_code == 200 and response.json == {
        "ok": True,
        "message": "Thanks—your feedback was sent.",
    }
    assert len(provider["issues"]) == 1
    issue = next(iter(provider["issues"].values()))
    assert (
        issue["teamId"] == TEAM
        and issue["projectId"] == PROJECT
        and issue["stateId"] == TRIAGE
    )
    assert issue["priority"] == 0 and issue["labelIds"] == [
        LABELS["user-feedback"],
        LABELS[category],
    ]
    for expected in (
        "reporter@example.com",
        "test-build-123",
        '"path": "/lab/"',
        "Safari 18",
        "iOS",
        "390 × 844",
    ):
        assert expected in issue["description"]
    for excluded in (
        "private=query",
        "secret-fragment",
        "Mozilla/5.0",
        data["csrf_token"],
    ):
        assert excluded not in issue["description"]
    assert "linear" not in response.get_data(as_text=True).lower() and issue[
        "id"
    ] not in response.get_data(as_text=True)


def test_screenshot_sanitized_private_and_not_stored(client, app, provider, caplog):
    raw = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("gps", "private-location")
    Image.new("RGB", (20, 30)).save(raw, format="PNG", pnginfo=metadata)
    data = form_data(
        client,
        screenshot=(io.BytesIO(raw.getvalue()), "my-private-filename.png", "image/png"),
    )
    with caplog.at_level(logging.INFO):
        response = client.post("/feedback/submit", data=data)
    assert response.status_code == 200
    upload = provider["uploads"][0]
    assert b"private-location" not in upload
    with Image.open(io.BytesIO(upload)) as image:
        assert not image.info and image.size == (20, 30)
    assert (
        "private-upload-signature" not in caplog.text
        and "lin_api_fake-test-key" not in caplog.text
    )
    assert "my-private-filename" not in json.dumps(provider["calls"])
    assert "makePublic: false" in provider["calls"][0]["query"]
    assert ASSET in next(iter(provider["issues"].values()))["description"]
    with db.connect(app.config["DB_PATH"]) as conn:
        row = dict(conn.execute("SELECT * FROM issue_feedback_receipts").fetchone())
    assert row["status"] == "sent" and row["asset_url"] == ASSET
    assert "reporter@example.com" not in json.dumps(row) and "button" not in json.dumps(
        row
    )


@pytest.mark.parametrize(
    "failure", ["after_create", "before_create", "graphql", 401, 403, 429, 500]
)
def test_failure_retry_never_duplicates(client, app, provider, failure, caplog):
    data = form_data(client)
    provider["failure"] = failure
    failed = client.post("/feedback/submit", data=data)
    assert failed.status_code == 503 and failed.json["retry_same"]
    provider["failure"] = None
    succeeded = client.post("/feedback/submit", data=data)
    assert succeeded.status_code == 200 and len(provider["issues"]) == 1
    calls = len(provider["calls"])
    # Re-created app proves receipts survive process restart.
    other = create_app(app.config["DB_PATH"])
    other.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    with client.session_transaction() as session:
        uid = session["_user_id"]
    other_client = other.test_client()
    with other_client.session_transaction() as session:
        session["_user_id"] = uid
    data["csrf_token"] = form_data(other_client)["csrf_token"]
    repeated = other_client.post("/feedback/submit", data=data)
    assert repeated.status_code == 200 and len(provider["calls"]) == calls
    assert "sensitive provider detail" not in caplog.text
    assert (
        "lin_api_fake-test-key" not in caplog.text
        and "private-upload-signature" not in caplog.text
    )


def test_uploaded_asset_reused_after_creation_failure(client, provider):
    data = form_data(client)
    provider["failure"] = "before_create"
    first = client.post(
        "/feedback/submit",
        data={
            **data,
            "screenshot": (io.BytesIO(image_bytes()), "screen.png", "image/png"),
        },
    )
    assert first.status_code == 503
    provider["failure"] = None
    second = client.post(
        "/feedback/submit",
        data={
            **data,
            "screenshot": (io.BytesIO(image_bytes()), "screen.png", "image/png"),
        },
    )
    assert second.status_code == 200 and len(provider["uploads"]) == 1


def test_upload_failure_creates_no_issue(client, provider):
    provider["put_status"] = 403
    response = client.post(
        "/feedback/submit",
        data=form_data(
            client, screenshot=(io.BytesIO(image_bytes()), "image.png", "image/png")
        ),
    )
    assert response.status_code == 503 and not provider["issues"]


@pytest.mark.parametrize(
    "url",
    [
        "http://storage.googleapis.com/file",
        "https://evil.example/file",
        "https://storage.googleapis.com.evil.example/file",
        "https://127.0.0.1/file",
    ],
)
def test_untrusted_upload_destinations_rejected(client, provider, url):
    provider["upload_url"] = url
    response = client.post(
        "/feedback/submit",
        data=form_data(
            client, screenshot=(io.BytesIO(image_bytes()), "image.png", "image/png")
        ),
    )
    assert response.status_code == 503 and not provider["uploads"]


def test_auth_csrf_disabled_accounts_and_disabled_feature(app, client, user, provider):
    assert app.test_client().post("/feedback/submit").status_code == 401
    assert (
        client.post(
            "/feedback/submit", data={"description": "Valid report"}
        ).status_code
        == 400
    )
    data = form_data(client)
    db.UsersRepo(app.config["DB_PATH"]).set_status(user, "disabled")
    assert client.post("/feedback/submit", data=data).status_code == 401
    db.UsersRepo(app.config["DB_PATH"]).set_status(user, "active")
    app.config["LINEAR_FEEDBACK_ENABLED"] = False
    assert client.post("/feedback/submit", data=data).status_code == 503
    assert provider["calls"] == []


def test_ui_only_for_signed_in_users(client, app):
    assert b'id="feedback-launcher"' in client.get("/profile").data
    assert b'id="feedback-launcher"' not in app.test_client().get("/login").data
    app.config["LINEAR_FEEDBACK_ENABLED"] = False
    assert b'id="feedback-launcher"' not in client.get("/profile").data


@pytest.mark.parametrize(
    "name,mime,raw",
    [
        ("image.svg", "image/svg+xml", b'<svg onload="alert(1)"></svg>'),
        ("image.png", "image/png", b"not an image"),
        ("image.jpg", "image/jpeg", image_bytes()),
        ("image.png", "application/octet-stream", image_bytes()),
    ],
)
def test_invalid_images(client, provider, name, mime, raw):
    response = client.post(
        "/feedback/submit",
        data=form_data(client, screenshot=(io.BytesIO(raw), name, mime)),
    )
    assert response.status_code == 400 and not provider["calls"]


@pytest.mark.parametrize(
    "kind,filename,mime",
    [
        ("PNG", "image.png", "image/png"),
        ("JPEG", "image.jpeg", "image/jpeg"),
        ("WEBP", "image.webp", "image/webp"),
    ],
)
def test_supported_formats_reencoded(kind, filename, mime):
    result = sanitize_image(
        FileStorage(io.BytesIO(image_bytes(kind)), filename, content_type=mime)
    )
    assert result.content_type == "image/png"
    with Image.open(io.BytesIO(result.content)) as output:
        assert output.size == (40, 30) and output.info == {}


def test_exif_orientation_stripped():
    out = io.BytesIO()
    image = Image.new("RGB", (20, 30))
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private-location"
    image.save(out, "JPEG", exif=exif)
    result = sanitize_image(
        FileStorage(io.BytesIO(out.getvalue()), "photo.jpg", content_type="image/jpeg")
    )
    with Image.open(io.BytesIO(result.content)) as clean:
        assert clean.size == (30, 20) and not clean.getexif()
    assert b"private-location" not in result.content


def test_realistic_screenshot_exceeds_multipart_buffer(client, provider):
    raw = io.BytesIO()
    Image.effect_noise((600, 600), 100).save(raw, format="PNG")
    assert len(raw.getvalue()) > 131072
    response = client.post(
        "/feedback/submit",
        data=form_data(
            client, screenshot=(io.BytesIO(raw.getvalue()), "screen.png", "image/png")
        ),
    )
    assert response.status_code == 200 and len(provider["uploads"]) == 1


def test_size_and_pixel_limits(client, provider, monkeypatch):
    response = client.post(
        "/feedback/submit",
        data=form_data(
            client, screenshot=(io.BytesIO(b"x" * 10_000_001), "image.png", "image/png")
        ),
    )
    assert response.status_code == 400
    response = client.post(
        "/feedback/submit",
        data=form_data(
            client, screenshot=(io.BytesIO(b"x" * 10_100_000), "image.png", "image/png")
        ),
    )
    assert response.status_code == 413
    monkeypatch.setattr("sabermetrics.ui.feedback_images.MAX_IMAGE_PIXELS", 100)
    response = client.post(
        "/feedback/submit",
        data=form_data(
            client, screenshot=(io.BytesIO(image_bytes()), "image.png", "image/png")
        ),
    )
    assert response.status_code == 400 and not provider["calls"]


@pytest.mark.parametrize(
    "path",
    [
        "/invite/private-token",
        "/reset-password#secret",
        "https://evil.example/path",
        "//evil.example/path",
        "/unknown/private-token",
    ],
)
def test_sensitive_or_unknown_paths_excluded(client, provider, path):
    response = client.post("/feedback/submit", data=form_data(client, page_path=path))
    assert response.status_code == 200
    body = next(iter(provider["issues"].values()))["description"]
    assert '"path": "Unavailable"' in body and "private-token" not in body


def test_redesigned_routes_are_valid_feedback_context(client, provider):
    response = client.post(
        "/feedback/submit", data=form_data(client, page_path="/research")
    )
    assert response.status_code == 200
    body = next(iter(provider["issues"].values()))["description"]
    assert '"path": "/research"' in body
    assert '"page": "Index"' in body


def test_duplicate_fields_and_multiple_images_rejected(client, provider):
    data = MultiDict(form_data(client))
    data.add("category", "ux")
    assert client.post("/feedback/submit", data=data).status_code == 400
    data = MultiDict(form_data(client))
    for _ in range(2):
        data.add("screenshot", (io.BytesIO(image_bytes()), "image.png", "image/png"))
    assert client.post("/feedback/submit", data=data).status_code == 400
    assert not provider["calls"]


def test_changed_payload_and_other_user_cannot_reuse_receipt(client, app, provider):
    data = form_data(client)
    assert client.post("/feedback/submit", data=data).status_code == 200
    assert (
        client.post(
            "/feedback/submit",
            data={**data, "description": "An entirely different report"},
        ).status_code
        == 409
    )
    other = db.UsersRepo(app.config["DB_PATH"]).create(
        email="other@example.com", status="active"
    )
    with client.session_transaction() as session:
        session["_user_id"] = other
    assert client.post("/feedback/submit", data=data).status_code == 409
    assert len(provider["issues"]) == 1


def test_receipt_lease_and_restart_recovery(app, user):
    receipts = FeedbackReceipts(app.config["DB_PATH"])
    identity = str(uuid.uuid4())
    row = receipts.claim(identity, user, "digest")
    with pytest.raises(ReceiptBusy):
        receipts.claim(identity, user, "digest")
    with pytest.raises(ReceiptConflict):
        receipts.claim(identity, "another-user", "digest")
    receipts.attempting(identity)
    with db.connect(app.config["DB_PATH"]) as conn:
        conn.execute(
            "UPDATE issue_feedback_receipts SET lease_until=?", (time.time() - 1,)
        )
        conn.commit()
    retry = receipts.claim(identity, user, "digest")
    assert retry["attempted"] and retry["issue_id"] == row["issue_id"]


def test_rate_limits_are_per_account(client, app, provider):
    data = form_data(client)
    for _ in range(5):
        assert client.post("/feedback/submit", data=data).status_code == 200
    assert client.post("/feedback/submit", data=data).status_code == 429
    other = db.UsersRepo(app.config["DB_PATH"]).create(
        email="other@example.com", status="active"
    )
    with client.session_transaction() as session:
        session["_user_id"] = other
    assert client.post("/feedback/submit", data=form_data(client)).status_code == 200


def test_configuration_requires_complete_routing(monkeypatch):
    configure(monkeypatch)
    assert LinearConfig.from_env().project_id == PROJECT
    monkeypatch.delenv("LINEAR_TRIAGE_STATE_ID")
    with pytest.raises(ValueError, match="LINEAR_TRIAGE_STATE_ID"):
        LinearConfig.from_env()
    monkeypatch.setenv("LINEAR_FEEDBACK_ENABLED", "false")
    assert LinearConfig.from_env() is None


def test_database_upgrade_is_idempotent(app, user):
    setup_database(app.config["DB_PATH"])
    setup_database(app.config["DB_PATH"])
    assert db.UsersRepo(app.config["DB_PATH"]).get(user)["status"] == "active"


@pytest.mark.parametrize(
    "changes",
    [
        {"category": "other"},
        {"description": "short"},
        {"description": "x" * 5001},
        {"submission_id": "invalid"},
    ],
)
def test_invalid_form_never_calls_linear(client, provider, changes):
    response = client.post("/feedback/submit", data=form_data(client, **changes))
    assert response.status_code == 400 and not provider["calls"]


def test_daily_limit_survives_restart(app, user):
    receipts = FeedbackReceipts(app.config["DB_PATH"])
    for index in range(20):
        identity = str(uuid.uuid4())
        receipts.claim(identity, user, str(index))
        receipts.finish(identity, True)
    from sabermetrics.ui.feedback_store import ReceiptLimit

    with pytest.raises(ReceiptLimit):
        FeedbackReceipts(app.config["DB_PATH"]).claim(str(uuid.uuid4()), user, "new")


def test_busy_processing_returns_retry_without_provider_call(app, client, provider):
    slot = app.extensions["feedback_slots"]
    assert slot.acquire(blocking=False)
    try:
        response = client.post("/feedback/submit", data=form_data(client))
        assert response.status_code == 429 and not provider["calls"]
    finally:
        slot.release()


def test_animated_image_rejected():
    out = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(
        out,
        format="PNG",
        save_all=True,
        append_images=[Image.new("RGB", (10, 10), "blue")],
    )
    with pytest.raises(ValueError, match="still"):
        sanitize_image(
            FileStorage(
                io.BytesIO(out.getvalue()), "animated.png", content_type="image/png"
            )
        )


def test_user_markdown_remains_literal(client, provider):
    message = "Unexpected output\n```\n![remote](https://example.invalid/tracker.png)\n@somebody"
    response = client.post(
        "/feedback/submit", data=form_data(client, description=message)
    )
    assert response.status_code == 200
    body = next(iter(provider["issues"].values()))["description"]
    assert "````\n" + message + "\n````" in body


def test_session_renewal_preserves_report_identity(client, app, user, provider):
    data = form_data(client)
    with client.session_transaction() as session:
        session.clear()
    assert client.post("/feedback/submit", data=data).status_code == 401
    assert client.get("/feedback/session").status_code == 401
    # Simulate a new tab signing in with a fresh session.
    with client.session_transaction() as session:
        session["_user_id"] = user
    assert client.post("/feedback/submit", data=data).json["code"] == "session_expired"
    renewed = client.get("/feedback/session")
    assert renewed.status_code == 200 and renewed.headers["Cache-Control"] == "no-store"
    data["csrf_token"] = renewed.json["csrf_token"]
    assert client.post("/feedback/submit", data=data).status_code == 200
    assert len(provider["issues"]) == 1
