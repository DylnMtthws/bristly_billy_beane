"""Account avatars stored atomically alongside the user's other SQLite data."""

from __future__ import annotations

import hashlib
import sqlite3
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps
from werkzeug.datastructures import FileStorage

from sabermetrics import db
from sabermetrics.ui.feedback_images import sanitize_image

AVATAR_SIZE = 256
DEFAULT_EMOJI = "🎣"
EMOJIS = ("🎣", "🧙", "🐉", "🦊", "🐸", "🌙", "🔥", "🌿", "⚡", "🎲", "👑", "💎")
ICONS = {
    "person": (
        "Person",
        "M20 21v-2a7 7 0 0 0-14 0v2 M17 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
    ),
    "star": (
        "Star",
        "m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z",
    ),
    "bolt": ("Lightning", "m13 2-9 12h7l-1 8 10-12h-7Z"),
    "shield": ("Shield", "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"),
    "cards": ("Cards", "M8 3h12v16H8Z M5 6H3v15h12"),
}


def ensure_avatar_schema(conn: sqlite3.Connection) -> None:
    """Add avatar storage without changing existing account rows."""
    conn.execute("""CREATE TABLE IF NOT EXISTS user_avatars (
        user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK(kind IN ('emoji', 'icon', 'image')),
        value TEXT NOT NULL,
        image BLOB,
        CHECK((kind = 'image' AND image IS NOT NULL) OR
              (kind != 'image' AND image IS NULL))
    )""")


def avatar_for(db_path: str | Path, user_id: str, emoji: str | None) -> dict[str, str]:
    """Read the selection without loading image bytes into every page request."""
    with db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT kind, value FROM user_avatars WHERE user_id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else {"kind": "emoji", "value": emoji or DEFAULT_EMOJI}


def public_selection(
    kind: str | None,
    value: str | None,
    emoji: str | None,
    *,
    has_author: bool,
) -> dict[str, str]:
    """Return public-safe avatar fields only: kind plus a display value."""
    if not has_author:
        return {"kind": "icon", "value": "person"}
    if kind == "image" and value:
        return {"kind": "image", "value": value}
    if kind == "icon" and value in ICONS:
        return {"kind": "icon", "value": value}
    if kind == "emoji" and value:
        return {"kind": "emoji", "value": value}
    return {"kind": "emoji", "value": emoji or DEFAULT_EMOJI}


def public_image_for_deck_author(db_path: str | Path, user_id: str) -> bytes | None:
    """Return image bytes only for authors of currently public decks."""
    with db.connect(db_path) as conn:
        row = conn.execute(
            """SELECT a.image FROM user_avatars a
               WHERE a.user_id=? AND a.kind='image'
                 AND EXISTS (
                   SELECT 1 FROM deck_documents d
                   WHERE d.owner_id=a.user_id AND d.visibility='public'
                 )""",
            (user_id,),
        ).fetchone()
    if row is None or row["image"] is None:
        return None
    return bytes(row["image"])


def image_bytes(upload: FileStorage) -> bytes:
    """Validate a still raster image, strip metadata, and center-crop to 256px."""
    sanitized = sanitize_image(upload)
    with (
        Image.open(BytesIO(sanitized.content)) as source,
        ImageOps.fit(
            source, (AVATAR_SIZE, AVATAR_SIZE), method=Image.Resampling.LANCZOS
        ) as cropped,
    ):
        output = BytesIO()
        cropped.save(output, format="PNG")
        return output.getvalue()


def save_avatar(
    db_path: str | Path, user_id: str, kind: str, value: str, image: bytes | None = None
) -> None:
    """Replace one account's avatar and discard its previous image atomically."""
    if kind == "emoji":
        if not value or len(value) > 16:
            raise ValueError("Choose an emoji or enter up to 16 characters.")
    elif kind == "icon":
        if value not in ICONS:
            raise ValueError("Choose one of the available icons.")
    elif kind == "image":
        if not image:
            raise ValueError("Choose a photo to upload.")
        value = hashlib.sha256(image).hexdigest()
    else:
        raise ValueError("Choose a photo, emoji, or icon.")
    with db.connect(db_path, foreign_keys=True) as conn:
        conn.execute(
            "INSERT INTO user_avatars (user_id, kind, value, image) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET kind=excluded.kind, value=excluded.value, image=excluded.image",
            (user_id, kind, value, image if kind == "image" else None),
        )
        if kind == "emoji":
            conn.execute(
                "UPDATE users SET avatar_emoji = ? WHERE id = ?", (value, user_id)
            )
        conn.commit()
