"""Bounded persistent snapshot of the public Research default cohort.

The default 90-day, page-1, unfiltered Commanders/Meta result is expensive to
compute. This module stores one public snapshot next to the app database,
invalidates it from a durable corpus revision (not WAL stamps), and refreshes
it in the background. Favorites, session data, and HTML never enter the file.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import Any, Literal

from sabermetrics.research import ResearchRepo

logger = logging.getLogger(__name__)

CALCULATION_VERSION = 2
SNAPSHOT_SCHEMA = "research-default-cohort.v1"
SNAPSHOT_FILENAME = "research-default-cohort.json"
DEFAULT_WINDOW_DAYS = 90
STALE_SERVE_SECONDS = 15 * 60
MAX_REFRESH_FAILURES = 5
MAX_BACKOFF_SECONDS = 30.0
KEY_CHANGE_SLEEP_SECONDS = 0.25
Freshness = Literal["fresh", "stale"]

_CORPUS_TABLES = (
    "cards",
    "card_prices",
    "tournament_results",
    "research_commander_pairs",
)
_PRIVATE_RESULT_KEYS = frozenset(
    {
        "favorited",
        "email",
        "user_id",
        "csrf",
        "csrf_token",
        "owner_id",
        "session",
        "html",
    }
)


def as_of_date() -> date:
    """Return the calendar date that bounds the default Research window."""
    return date.today()


def db_identity(path: Path) -> str:
    """Return a stable identity for one SQLite file on this machine."""
    resolved = str(path.resolve())
    try:
        return f"{resolved}:{path.stat().st_ino}"
    except OSError:
        return resolved


def apply_favorites(data: dict[str, Any], favorite_ids: set[str]) -> dict[str, Any]:
    """Return a request-local copy with the current user's favorites applied."""
    copied = deepcopy(data)
    for row in copied.get("results", []):
        row["favorited"] = row.get("id") in favorite_ids
    return copied


def public_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Copy a commanders() result and drop any request-local fields."""
    copied = json.loads(json.dumps(data, default=str))
    if not isinstance(copied, dict):
        raise TypeError("research default snapshot payload is not an object")

    def remove_private(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: remove_private(v)
                for k, v in value.items()
                if k not in _PRIVATE_RESULT_KEYS
            }
        if isinstance(value, list):
            return [remove_private(v) for v in value]
        return value

    fields = ("results", "total", "recorded_entries", "window_days", "page", "has_next")
    return {key: remove_private(copied[key]) for key in fields if key in copied}


@dataclass(frozen=True)
class CohortView:
    """A public default-cohort result plus truthful freshness."""

    data: dict[str, Any]
    freshness: Freshness
    corpus_revision: int
    as_of_date: str
    stale_for_seconds: float | None = None
    computed_at: str = ""


class ResearchDefaultCache:
    """One durable public snapshot, one refresh at a time, no request-context I/O."""

    def __init__(self, db_path: Path, data_dir: Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.data_dir = Path(data_dir) if data_dir is not None else self.db_path.parent
        self.snapshot_path = self.data_dir / (
            self.db_path.name + "." + SNAPSHOT_FILENAME
        )
        self._state_lock = threading.Lock()
        self._refreshing = False
        self._pending = False
        self._closed = False
        self._failures = 0
        self._retry_after = 0.0
        self._stop = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._memory: dict[str, Any] | None = None
        self._connections = threading.local()
        disk = self._read_disk()
        if disk is not None:
            self._memory = disk

    def close(self) -> None:
        """Stop publishing after the in-flight refresh finishes."""
        self._closed = True
        self._stop.set()

    def install_schema(self) -> None:
        """Create the single revision row and corpus-write triggers."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS research_corpus_revision (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    revision INTEGER NOT NULL DEFAULT 0,
                    dirty INTEGER NOT NULL DEFAULT 0,
                    changed_at INTEGER NOT NULL DEFAULT 0,
                    invalidated_at INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO research_corpus_revision(
                    id, revision, dirty, changed_at
                ) VALUES (1, 0, 0, CAST(strftime('%s','now') AS INTEGER));
                """)
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(research_corpus_revision)")
            }
            if "invalidated_at" not in columns:
                conn.execute(
                    "ALTER TABLE research_corpus_revision ADD COLUMN invalidated_at INTEGER NOT NULL DEFAULT 0"
                )
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for table in _CORPUS_TABLES:
                if table not in tables:
                    continue
                for event, suffix in (
                    ("INSERT", "ai"),
                    ("UPDATE", "au"),
                    ("DELETE", "ad"),
                ):
                    name = f"research_corpus_{table}_{suffix}_v2"
                    conn.execute(f"""
                        CREATE TRIGGER IF NOT EXISTS {name}
                        AFTER {event} ON {table}
                        BEGIN
                          UPDATE research_corpus_revision
                             SET dirty = 1,
                                 invalidated_at = CASE WHEN invalidated_at=0 THEN CAST(strftime('%s','now') AS INTEGER) ELSE invalidated_at END,
                                 changed_at = CAST(strftime('%s','now') AS INTEGER)
                           WHERE id = 1 AND dirty = 0;
                        END;
                        """)
            conn.commit()

    def corpus_state(self, conn: sqlite3.Connection | None = None) -> tuple[int, int]:
        """Return ``(revision, changed_at)``, compacting a dirty epoch once."""
        if conn is None:
            with self._connect() as owned:
                return self._corpus_state(owned)
        return self._corpus_state(conn)

    def peek_corpus_state(self) -> tuple[int, int]:
        """Read freshness without contending for the SQLite writer lock on a request."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT revision, dirty, changed_at, invalidated_at FROM research_corpus_revision WHERE id=1"
            ).fetchone()
        invalidated = int(row["invalidated_at"] or row["changed_at"] or 0)
        return int(row["revision"]) + bool(row["dirty"]), min(
            invalidated, int(row["changed_at"] or invalidated)
        )

    def current_key(
        self, conn: sqlite3.Connection | None = None
    ) -> tuple[str, int, int, str, int]:
        """Identity, revision, calculator, date, and window for the default cohort."""
        revision, _changed = self.corpus_state(conn)
        return (
            db_identity(self.db_path),
            revision,
            CALCULATION_VERSION,
            as_of_date().isoformat(),
            DEFAULT_WINDOW_DAYS,
        )

    def try_serve(self) -> CohortView | None:
        """Return the last good snapshot if it is fresh or still within the stale limit."""
        doc = self._memory if self._memory is not None else self._read_disk()
        if doc is None:
            return None
        if self._memory is None:
            self._memory = doc
        if not self._compatible(doc):
            return None
        revision, changed_at = self.peek_corpus_state()
        today = as_of_date().isoformat()
        payload = deepcopy(doc["payload"])
        if int(doc["corpus_revision"]) == revision and str(doc["as_of_date"]) == today:
            return CohortView(
                data=payload,
                freshness="fresh",
                corpus_revision=revision,
                as_of_date=today,
                computed_at=str(doc["computed_at"]),
            )
        became_stale = self._became_stale_at(doc, revision, changed_at, today)
        stale_for = max(0.0, time.time() - became_stale)
        if stale_for > STALE_SERVE_SECONDS:
            return None
        return CohortView(
            data=payload,
            freshness="stale",
            corpus_revision=int(doc["corpus_revision"]),
            as_of_date=str(doc["as_of_date"]),
            stale_for_seconds=stale_for,
            computed_at=str(doc["computed_at"]),
        )

    def request_refresh(self) -> None:
        """Start at most one background refresh; extra callers set a single pending flag."""
        if self._closed:
            return
        with self._state_lock:
            if time.monotonic() < self._retry_after:
                return
            if self._refreshing:
                self._pending = True
                return
            self._refreshing = True
            self._idle.clear()
        threading.Thread(
            target=self._worker,
            name="research-default-cache",
            daemon=True,
        ).start()

    def warm_in_background(self) -> None:
        """Refresh if needed without blocking the caller or app health."""
        view = self.try_serve()
        if view is not None and view.freshness == "fresh":
            return
        self.request_refresh()

    def start_monitor(self) -> None:
        """Recheck corpus/date every 30 seconds without retaining dead app caches."""
        reference = weakref.ref(self)
        stop = self._stop
        weakref.finalize(self, stop.set)

        def monitor() -> None:
            while not stop.wait(30):
                cache = reference()
                if cache is None:
                    return
                try:
                    cache.warm_in_background()
                except Exception as exc:
                    logger.warning(
                        "Research cache monitor failed: %s", type(exc).__name__
                    )
                finally:
                    del cache

        threading.Thread(
            target=monitor, name="research-cache-monitor", daemon=True
        ).start()

    def wait_for_idle(self, timeout: float) -> bool:
        """Block until no refresh is running, or until ``timeout`` seconds elapse."""
        return self._idle.wait(timeout)

    def compute_blocking(self, timeout: float = 15.0) -> CohortView:
        """Wait for a usable snapshot, computing inline only after a failed refresh.

        Used by the explicit no-JS full-results request. The default HTML path
        must not call this.
        """
        view = self.try_serve()
        if view is not None:
            return view
        self.request_refresh()
        self.wait_for_idle(timeout)
        view = self.try_serve()
        if view is not None:
            return view
        with self._state_lock:
            busy = self._refreshing
            if not busy:
                self._refreshing = True
                self._idle.clear()
        if busy:
            self.wait_for_idle(timeout)
            view = self.try_serve()
            if view is not None:
                return view
            raise RuntimeError("research default snapshot is still refreshing")
        try:
            self._refresh_once()
        finally:
            self._close_thread_connection()
            with self._state_lock:
                self._refreshing = False
                self._idle.set()
        view = self.try_serve()
        if view is None:
            raise RuntimeError("research default snapshot is unavailable")
        return view

    def _worker(self) -> None:
        key_misses = 0
        try:
            while not self._closed:
                try:
                    ready = self._refresh_once()
                    self._failures = 0
                except Exception as exc:
                    self._failures += 1
                    logger.warning(
                        "research default snapshot refresh failed: %s",
                        type(exc).__name__,
                    )
                    if self._failures >= MAX_REFRESH_FAILURES:
                        self._retry_after = time.monotonic() + MAX_BACKOFF_SECONDS
                        break
                    time.sleep(min(MAX_BACKOFF_SECONDS, 2**self._failures))
                    continue
                with self._state_lock:
                    pending = self._pending
                    self._pending = False
                if pending and not self._closed:
                    key_misses = 0
                    continue
                if ready:
                    break
                key_misses += 1
                if key_misses >= 8:
                    break
                time.sleep(KEY_CHANGE_SLEEP_SECONDS)
        finally:
            self._close_thread_connection()
            with self._state_lock:
                self._refreshing = False
                self._pending = False
                self._idle.set()

    def _refresh_once(self) -> bool:
        conn = self._thread_connection()
        existing = self.try_serve()
        if existing is not None and existing.freshness == "fresh":
            return True
        started = self.current_key(conn)
        payload = public_payload(self._load_public_cohort())
        finished = self.current_key(conn)
        if started != finished:
            logger.info(
                "research default snapshot discarded after corpus or date change"
            )
            return False
        self._publish(payload, finished)
        return True

    def _load_public_cohort(self) -> dict[str, Any]:
        """Run the default commanders() query off any Flask request context."""
        return ResearchRepo(self.db_path).commanders(observed_only=True)

    def _publish(
        self, payload: dict[str, Any], key: tuple[str, int, int, str, int]
    ) -> None:
        if self._closed:
            return
        identity, revision, calc, day, window = key
        doc = {
            "schema": SNAPSHOT_SCHEMA,
            "db_identity": identity,
            "corpus_revision": revision,
            "calculation_version": calc,
            "as_of_date": day,
            "window_days": window,
            "computed_at": datetime.now(UTC).isoformat(),
            "payload": payload,
        }
        encoded = json.dumps(doc, default=str)
        json.loads(encoded)  # refuse a document we cannot read back
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.snapshot_path.with_name(self.snapshot_path.name + ".tmp")
        fd = os.open(
            tmp_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o644,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.snapshot_path)
        except Exception:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            raise
        self._memory = json.loads(encoded)
        with self._connect() as conn:
            conn.execute(
                "UPDATE research_corpus_revision SET invalidated_at=0 WHERE id=1 AND revision=? AND dirty=0",
                (revision,),
            )
            conn.commit()

    def _compatible(self, doc: dict[str, Any]) -> bool:
        try:
            payload = doc["payload"]
            if (
                not isinstance(doc["corpus_revision"], int)
                or doc["corpus_revision"] < 0
            ):
                return False
            date.fromisoformat(doc["as_of_date"])
            datetime.fromisoformat(doc["computed_at"])
            if not all(
                key in payload
                for key in (
                    "total",
                    "recorded_entries",
                    "window_days",
                    "page",
                    "has_next",
                    "results",
                )
            ):
                return False
            calc = doc.get("calculation_version")
            window = doc.get("window_days")
            if calc is None or window is None:
                return False
            return (
                doc.get("schema") == SNAPSHOT_SCHEMA
                and str(doc.get("db_identity")) == db_identity(self.db_path)
                and int(calc) == CALCULATION_VERSION
                and int(window) == DEFAULT_WINDOW_DAYS
                and isinstance(payload, dict)
                and isinstance(payload.get("results"), list)
            )
        except (TypeError, ValueError, KeyError):
            return False

    def _became_stale_at(
        self,
        doc: dict[str, Any],
        revision: int,
        changed_at: int,
        today: str,
    ) -> float:
        stamps: list[float] = []
        if int(doc["corpus_revision"]) != revision:
            stamps.append(float(changed_at or 0))
        if str(doc["as_of_date"]) != today:
            stamps.append(
                datetime.combine(
                    date.fromisoformat(doc["as_of_date"]) + timedelta(days=1),
                    dt_time.min,
                ).timestamp()
            )
        return min(stamps) if stamps else time.time()

    def _read_disk(self) -> dict[str, Any] | None:
        try:
            with self.snapshot_path.open(encoding="utf-8") as handle:
                raw = handle.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                return None
            doc = json.loads(raw)
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError, TypeError, UnicodeError) as exc:
            logger.warning(
                "research default snapshot unreadable: %s", type(exc).__name__
            )
            return None
        if not isinstance(doc, dict) or not self._compatible(doc):
            logger.warning("research default snapshot unreadable: ValueError")
            return None
        return doc

    def _corpus_state(self, conn: sqlite3.Connection) -> tuple[int, int]:
        row = conn.execute(
            "SELECT revision, dirty, changed_at, invalidated_at FROM research_corpus_revision WHERE id=1"
        ).fetchone()
        if row is None:
            return 0, 0
        if int(row["dirty"]):
            conn.execute("""
                UPDATE research_corpus_revision
                   SET revision = revision + 1, dirty = 0
                 WHERE id = 1 AND dirty = 1
                """)
            conn.commit()
            row = conn.execute(
                "SELECT revision, changed_at, invalidated_at FROM research_corpus_revision WHERE id=1"
            ).fetchone()
            if row is None:
                return 0, 0
        invalidated = int(row["invalidated_at"] or row["changed_at"] or 0)
        return int(row["revision"]), min(
            invalidated, int(row["changed_at"] or invalidated)
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
        finally:
            conn.close()

    def _thread_connection(self) -> sqlite3.Connection:
        conn = getattr(self._connections, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            self._connections.conn = conn
        return conn

    def _close_thread_connection(self) -> None:
        conn = getattr(self._connections, "conn", None)
        if conn is not None:
            conn.close()
            self._connections.conn = None


def configure_research_cache(app: Any) -> ResearchDefaultCache:
    """Install schema, expose the cache, and warm without delaying startup."""
    db_path = Path(app.config["DB_PATH"])
    cache = ResearchDefaultCache(db_path)
    app.extensions["research_default_cache"] = cache
    if app.config.get("DECK_LAB_RESEARCH_ENABLED") and db_path.exists():
        cache.install_schema()
        cache.warm_in_background()
        cache.start_monitor()
    return cache
