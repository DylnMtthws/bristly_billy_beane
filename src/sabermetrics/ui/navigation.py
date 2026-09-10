"""Bounded, process-local reuse of public research data and versioned assets."""

import re
from collections.abc import Callable
from copy import deepcopy
from datetime import date
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any

from flask import Flask, request


class ResearchLandingCache:
    """Reuse the default cohort across Commanders and Meta, never user HTML.

    One entry per app process, at most 30 seconds old. SQLite file/WAL changes
    invalidate it earlier. Favorites are applied by the route after copying.
    Filtered searches bypass this cache, so arbitrary inputs cannot grow it.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._key: tuple[Any, ...] = ()
        self._until = 0.0
        self._data: dict[str, Any] = {}

    @staticmethod
    def _stamp(path: Path) -> tuple[Any, ...]:
        files: list[tuple[int, int, int] | None] = []
        for file in (path, Path(str(path) + "-wal")):
            try:
                stat = file.stat()
                files.append((stat.st_ino, stat.st_size, stat.st_mtime_ns))
            except FileNotFoundError:
                files.append(None)
        return (str(path.resolve()), date.today(), *files)

    def get(self, path: Path, load: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            key = self._stamp(path)
            if key != self._key or monotonic() >= self._until:
                # Publish only a successful result; failed refreshes are retried.
                data = load()
                self._data, self._key = data, key
                self._until = monotonic() + 30
            return deepcopy(self._data)


def configure_navigation(app: Flask, build_sha: str) -> None:
    app.extensions["research_landing_cache"] = ResearchLandingCache()
    if not re.fullmatch(r"[a-f0-9]{40}", build_sha):
        return  # Development assets must remain immediately revalidated.

    @app.url_defaults
    def version_static(endpoint: str, values: dict[str, Any]) -> None:
        if endpoint == "static":
            values.setdefault("v", build_sha)

    @app.after_request
    def cache_versioned_static(response):
        if (
            request.endpoint == "static"
            and request.args.get("v") == build_sha
            and response.status_code in {200, 304}
        ):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
