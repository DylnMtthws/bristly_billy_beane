"""Profile avatar persistence, bounded image handling, and account isolation."""

from io import BytesIO

import pytest
from PIL import Image

from sabermetrics import db
from sabermetrics.avatars import avatar_for, ensure_avatar_schema
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database


@pytest.fixture
def avatar_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    path = tmp_path / "avatars.db"
    setup_database(path)
    uid = db.UsersRepo(path).create(
        email="avatar@example.test", status="active", avatar_emoji="🐉"
    )
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = uid
        session["_fresh"] = True
    return app, client, path, uid


def picture(kind="PNG"):
    output = BytesIO()
    with Image.new("RGB", (480, 240), "red") as image:
        image.save(output, format=kind)
    output.seek(0)
    return output


def test_existing_emoji_survives_idempotent_schema_setup(avatar_app):
    _app, client, path, uid = avatar_app
    with db.connect(path) as conn:
        ensure_avatar_schema(conn)
        ensure_avatar_schema(conn)
    assert avatar_for(path, uid, "🐉") == {"kind": "emoji", "value": "🐉"}
    body = client.get("/").data
    assert b'aria-label="Open account menu"' in body
    assert "🐉".encode() in body
    summary = body.split(b"<summary", 1)[1].split(b"</summary>", 1)[0]
    assert b"avatar@example.test" not in summary
    assert b"account-chevron" in summary


@pytest.mark.parametrize(
    "kind,filename",
    [("PNG", "photo.png"), ("JPEG", "photo.jpg"), ("WEBP", "photo.webp")],
)
def test_upload_crops_and_persists_across_restart(avatar_app, kind, filename):
    _app, client, path, uid = avatar_app
    response = client.post(
        "/profile/avatar",
        data={"avatar_kind": "image", "avatar_image": (picture(kind), filename)},
    )
    assert response.status_code == 302
    served = client.get("/profile/avatar")
    assert served.status_code == 200 and served.mimetype == "image/png"
    assert served.headers["Cache-Control"] == "private, no-store"
    with Image.open(BytesIO(served.data)) as image:
        assert image.size == (256, 256)
        assert not image.getexif()
    restarted = create_app(path)
    restarted.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    other_client = restarted.test_client()
    with other_client.session_transaction() as session:
        session["_user_id"] = uid
    assert other_client.get("/profile/avatar").data == served.data
    # Updating the name must not discard the selected photo.
    client.post("/profile", data={"display_name": "Renamed"})
    assert avatar_for(path, uid, None)["kind"] == "image"


def test_switch_to_icon_emoji_and_reset_removes_image(avatar_app):
    _app, client, path, uid = avatar_app
    client.post(
        "/profile/avatar",
        data={"avatar_kind": "image", "avatar_image": (picture(), "photo.png")},
    )
    response = client.post(
        "/profile/avatar",
        data={"avatar_kind": "icon", "avatar_icon": "shield"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert avatar_for(path, uid, None) == {"kind": "icon", "value": "shield"}
    assert client.get("/profile/avatar").status_code == 404
    with db.connect(path) as conn:
        assert (
            conn.execute(
                "SELECT image FROM user_avatars WHERE user_id=?", (uid,)
            ).fetchone()[0]
            is None
        )
    client.post("/profile/avatar", data={"avatar_kind": "emoji", "avatar_emoji": "🦊"})
    assert avatar_for(path, uid, None) == {"kind": "emoji", "value": "🦊"}
    client.post("/profile/avatar/remove")
    assert avatar_for(path, uid, None) == {"kind": "emoji", "value": "🎣"}


@pytest.mark.parametrize(
    "data",
    [
        {"avatar_kind": "icon", "avatar_icon": "<script>"},
        {"avatar_kind": "emoji", "avatar_emoji": "x" * 17},
        {"avatar_kind": "image"},
        {"avatar_kind": "bad"},
    ],
)
def test_invalid_selection_preserves_previous_avatar(avatar_app, data):
    _app, client, path, uid = avatar_app
    client.post("/profile/avatar", data={"avatar_kind": "icon", "avatar_icon": "star"})
    response = client.post("/profile/avatar", data=data, follow_redirects=True)
    assert response.status_code == 200
    assert b"dl-flash-error" in response.data
    assert avatar_for(path, uid, None) == {"kind": "icon", "value": "star"}


@pytest.mark.parametrize(
    "content,filename",
    [
        (b"<svg></svg>", "avatar.svg"),
        (b"not an image", "photo.png"),
        (b"x" * 10_000_001, "photo.png"),
    ],
)
def test_invalid_image_is_rejected(avatar_app, content, filename):
    _app, client, path, uid = avatar_app
    response = client.post(
        "/profile/avatar",
        data={"avatar_kind": "image", "avatar_image": (BytesIO(content), filename)},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"dl-flash-error" in response.data
    assert avatar_for(path, uid, "🐉")["kind"] == "emoji"


def test_avatar_is_private_and_posts_cannot_target_another_account(avatar_app):
    app, client, path, uid = avatar_app
    client.post(
        "/profile/avatar",
        data={"avatar_kind": "image", "avatar_image": (picture(), "photo.png")},
    )
    original = client.get("/profile/avatar").data
    other_id = db.UsersRepo(path).create(email="other@example.test", status="active")
    other = app.test_client()
    with other.session_transaction() as session:
        session["_user_id"] = other_id
    assert other.get(f"/profile/avatar?user_id={uid}").status_code == 404
    other.post(
        "/profile/avatar",
        data={"user_id": uid, "avatar_kind": "emoji", "avatar_emoji": "🔥"},
    )
    assert client.get("/profile/avatar").data == original
    assert app.test_client().get("/profile/avatar").status_code == 302
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/profile/avatar/remove").status_code == 400
    assert (
        client.post(
            "/profile/avatar", data={"avatar_kind": "emoji", "avatar_emoji": "🔥"}
        ).status_code
        == 400
    )


def test_upload_request_body_is_bounded(avatar_app):
    _app, client, _path, _uid = avatar_app
    response = client.post(
        "/profile/avatar",
        data=b"x" * 10_100_000,
        content_type="multipart/form-data; boundary=avatar",
    )
    assert response.status_code == 413
