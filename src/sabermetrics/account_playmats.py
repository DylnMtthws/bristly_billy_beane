"""Owner-scoped playmat library, private files, and the Night Ritual seed helper.

Night Ritual is not a global surface. Built-in mats stay public CSS patterns;
saved images live under the configured Deck Lab asset directory and are only
visible to the owning account. The operator seed helper takes an explicit
owner id and image path — it does not look up accounts or grant by role.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

BUILTIN_SURFACES = frozenset(
    {"slate-grid", "felt-weave", "deep-field", "graph-paper", "void"}
)
DEFAULT_SURFACE = "slate-grid"
LIBRARY_SURFACE = "library"
CUSTOM_SURFACE = "custom"
LEGACY_NIGHT_RITUAL = "night-ritual"
NIGHT_RITUAL_TITLE = "Night Ritual"
NIGHT_RITUAL_SOURCE = "seeded"
UPLOAD_SOURCE = "upload"
LIBRARY_DIRNAME = "playmats"
_OWNER_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_IMAGE_SUFFIXES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class PlaymatNotFound(Exception):
    """The playmat does not exist or is not owned by the caller."""


def ensure_account_playmat_schema(conn: sqlite3.Connection) -> None:
    """Additive owner-library table and presentation pointer. Idempotent."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS _schema_version (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            description TEXT
        );
        CREATE TABLE IF NOT EXISTS account_playmats (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            file_path TEXT NOT NULL,
            source_kind TEXT NOT NULL DEFAULT 'upload',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_account_playmats_owner
            ON account_playmats(owner_id, created_at DESC);
        """)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(deck_presentations)")}
    if columns and "playmat_id" not in columns:
        conn.execute("ALTER TABLE deck_presentations ADD COLUMN playmat_id TEXT")
    conn.execute(
        "INSERT OR IGNORE INTO _schema_version(version, description) "
        "VALUES ('account-playmats-v1', "
        "'Owner-scoped playmat library and private Night Ritual')"
    )


def library_root(asset_dir: Path) -> Path:
    return Path(asset_dir).resolve() / LIBRARY_DIRNAME


def is_library_file(path: Path, asset_dir: Path) -> bool:
    """True when ``path`` sits under the reusable library tree."""
    try:
        path.resolve().relative_to(library_root(asset_dir))
    except (OSError, ValueError):
        return False
    return True


def orphan_custom_path(raw: str | None, asset_dir: Path) -> Path | None:
    """Return a deck-scoped custom file that is safe to delete.

    Library images are reusable across decks and are never returned.
    """
    if not raw:
        return None
    asset_dir = Path(asset_dir).resolve()
    try:
        resolved = Path(raw).resolve()
    except OSError:
        return None
    if resolved.parent != asset_dir:
        return None
    if is_library_file(resolved, asset_dir):
        return None
    return resolved


def mime_for(path: Path) -> str:
    return _IMAGE_SUFFIXES.get(path.suffix.lower(), "image/png")


def public_presentation(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Shared and unauthorized views: built-in surfaces only, no asset paths."""
    presentation = dict(raw or {})
    presentation.pop("custom_surface_path", None)
    surface = str(presentation.get("surface") or DEFAULT_SURFACE)
    if surface not in BUILTIN_SURFACES:
        surface = DEFAULT_SURFACE
    presentation["surface"] = surface
    presentation["playmat_id"] = None
    presentation["has_custom_surface"] = False
    return presentation


def owner_presentation(
    raw: dict[str, Any] | None, *, owned_ids: set[str]
) -> dict[str, Any]:
    """Owner-facing presentation. Legacy night-ritual falls back until seeded."""
    presentation = dict(raw or {})
    custom_path = presentation.pop("custom_surface_path", None)
    playmat_id = presentation.get("playmat_id")
    playmat_id = str(playmat_id) if playmat_id else None
    surface = str(presentation.get("surface") or DEFAULT_SURFACE)
    if surface == LEGACY_NIGHT_RITUAL:
        surface = DEFAULT_SURFACE
        playmat_id = None
    elif surface == LIBRARY_SURFACE:
        if not playmat_id or playmat_id not in owned_ids:
            surface = DEFAULT_SURFACE
            playmat_id = None
    elif surface == CUSTOM_SURFACE:
        if not custom_path:
            surface = DEFAULT_SURFACE
        playmat_id = None
    elif surface not in BUILTIN_SURFACES:
        surface = DEFAULT_SURFACE
        playmat_id = None
    if surface in BUILTIN_SURFACES:
        playmat_id = None
    presentation["surface"] = surface
    presentation["playmat_id"] = playmat_id
    presentation["has_custom_surface"] = bool(custom_path) and surface == CUSTOM_SURFACE
    return presentation


def _safe_owner_id(owner_id: str) -> str:
    if not _OWNER_SEGMENT.fullmatch(str(owner_id or "")):
        raise ValueError("Invalid owner id.")
    return owner_id


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class AccountPlaymatRepo:
    """Owner-scoped saved playmats and the local Night Ritual seed helper."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def list_for_owner_conn(
        conn: sqlite3.Connection, owner_id: str
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT id, owner_id, title, source_kind, created_at "
            "FROM account_playmats WHERE owner_id=? "
            "ORDER BY created_at DESC, title COLLATE NOCASE",
            (owner_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def get_owned_conn(
        conn: sqlite3.Connection, owner_id: str, playmat_id: str
    ) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT * FROM account_playmats WHERE id=? AND owner_id=?",
            (playmat_id, owner_id),
        ).fetchone()
        return dict(row) if row else None

    def list_for_owner(self, owner_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return self.list_for_owner_conn(conn, owner_id)

    def get_owned(self, owner_id: str, playmat_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return self.get_owned_conn(conn, owner_id, playmat_id)

    def public_summaries(self, owner_id: str) -> list[dict[str, str]]:
        """Picker payload: ids and titles only, never file paths."""
        return [
            {"id": item["id"], "title": item["title"]}
            for item in self.list_for_owner(owner_id)
        ]

    def add_upload(
        self,
        owner_id: str,
        *,
        title: str,
        content: bytes,
        asset_dir: Path,
        suffix: str = ".png",
    ) -> dict[str, Any]:
        """Store a new reusable library image for ``owner_id``."""
        owner_id = _safe_owner_id(owner_id)
        label = " ".join(str(title or "").split())[:60] or "Custom playmat"
        suffix = suffix.lower() if suffix.startswith(".") else f".{suffix.lower()}"
        if suffix not in _IMAGE_SUFFIXES:
            suffix = ".png"
        playmat_id = uuid.uuid4().hex
        target = self._owner_dir(asset_dir, owner_id) / f"{playmat_id}{suffix}"
        target.write_bytes(content)
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO account_playmats "
                    "(id, owner_id, title, file_path, source_kind, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        playmat_id,
                        owner_id,
                        label,
                        str(target),
                        UPLOAD_SOURCE,
                        _now(),
                    ),
                )
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return {"id": playmat_id, "title": label, "source_kind": UPLOAD_SOURCE}

    def open_owned(
        self, owner_id: str, playmat_id: str, asset_dir: Path
    ) -> tuple[Path, str]:
        """Return ``(path, mime)`` for an owned library file, else raise."""
        owner_id = _safe_owner_id(owner_id)
        row = self.get_owned(owner_id, playmat_id)
        if row is None:
            raise PlaymatNotFound()
        return self._resolve_owned_file(row, owner_id, asset_dir)

    def open_deck_playmat(
        self, owner_id: str, deck_id: str, asset_dir: Path
    ) -> tuple[Path, str]:
        """Serve the mat selected on an owned deck (library or legacy custom)."""
        owner_id = _safe_owner_id(owner_id)
        asset_dir = Path(asset_dir).resolve()
        with self._connect() as conn:
            owned = conn.execute(
                "SELECT 1 FROM deck_documents WHERE id=? AND owner_id=?",
                (deck_id, owner_id),
            ).fetchone()
            if owned is None:
                raise PlaymatNotFound()
            presentation = conn.execute(
                "SELECT surface, custom_surface_path, playmat_id "
                "FROM deck_presentations WHERE deck_id=?",
                (deck_id,),
            ).fetchone()
            if presentation is None:
                raise PlaymatNotFound()
            playmat_id = presentation["playmat_id"]
            if playmat_id:
                row = self.get_owned_conn(conn, owner_id, str(playmat_id))
                if row is None:
                    raise PlaymatNotFound()
                return self._resolve_owned_file(row, owner_id, asset_dir)
            raw_path = presentation["custom_surface_path"]
        orphan = orphan_custom_path(raw_path, asset_dir)
        if orphan is None or not orphan.is_file():
            raise PlaymatNotFound()
        return orphan, mime_for(orphan)

    def seed_night_ritual(
        self,
        owner_id: str,
        image_path: str | Path,
        asset_dir: Path,
    ) -> dict[str, Any]:
        """Idempotently attach Night Ritual to one owner and migrate their decks.

        ``owner_id`` and ``image_path`` are explicit operator inputs. This does
        not look up email, grant admins, or touch other accounts' files.
        """
        owner_id = _safe_owner_id(owner_id)
        source = Path(image_path).expanduser()
        if not source.is_file():
            raise ValueError("Night Ritual image path is not a file.")
        suffix = source.suffix.lower()
        if suffix not in _IMAGE_SUFFIXES:
            raise ValueError("Night Ritual image must be a PNG, JPEG, or WebP file.")
        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(source) as preview:
                preview.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("Night Ritual image is not a valid still image.") from exc
        payload = source.read_bytes()
        if not payload:
            raise ValueError("Night Ritual image is empty.")
        with self._connect() as conn:
            user = conn.execute(
                "SELECT id FROM users WHERE id=?", (owner_id,)
            ).fetchone()
            if user is None:
                raise ValueError("Unknown owner id.")
            existing = conn.execute(
                "SELECT * FROM account_playmats "
                "WHERE owner_id=? AND source_kind=? AND title=?",
                (owner_id, NIGHT_RITUAL_SOURCE, NIGHT_RITUAL_TITLE),
            ).fetchone()
            if existing:
                playmat_id = str(existing["id"])
                target = Path(existing["file_path"])
                if not target.is_file():
                    target = (
                        self._owner_dir(asset_dir, owner_id) / f"{playmat_id}{suffix}"
                    )
                    target.write_bytes(payload)
                    conn.execute(
                        "UPDATE account_playmats SET file_path=? WHERE id=? AND owner_id=?",
                        (str(target), playmat_id, owner_id),
                    )
            else:
                playmat_id = uuid.uuid4().hex
                target = self._owner_dir(asset_dir, owner_id) / f"{playmat_id}{suffix}"
                target.write_bytes(payload)
                conn.execute(
                    "INSERT INTO account_playmats "
                    "(id, owner_id, title, file_path, source_kind, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        playmat_id,
                        owner_id,
                        NIGHT_RITUAL_TITLE,
                        str(target),
                        NIGHT_RITUAL_SOURCE,
                        _now(),
                    ),
                )
            migrated = conn.execute(
                "UPDATE deck_presentations SET surface=?, playmat_id=? "
                "WHERE surface=? AND deck_id IN "
                "(SELECT id FROM deck_documents WHERE owner_id=?)",
                (LIBRARY_SURFACE, playmat_id, LEGACY_NIGHT_RITUAL, owner_id),
            ).rowcount
        return {
            "id": playmat_id,
            "title": NIGHT_RITUAL_TITLE,
            "owner_id": owner_id,
            "migrated": int(migrated or 0),
        }

    def _owner_dir(self, asset_dir: Path, owner_id: str) -> Path:
        folder = library_root(asset_dir) / owner_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _resolve_owned_file(
        self, row: dict[str, Any], owner_id: str, asset_dir: Path
    ) -> tuple[Path, str]:
        try:
            resolved = Path(str(row["file_path"])).resolve(strict=True)
        except FileNotFoundError as exc:
            raise PlaymatNotFound() from exc
        owner_root = (library_root(asset_dir) / owner_id).resolve()
        try:
            resolved.relative_to(owner_root)
        except ValueError as exc:
            raise PlaymatNotFound() from exc
        return resolved, mime_for(resolved)
