"""Versioned static assets and cookie-safe cache headers."""

import re
from collections.abc import Callable, Iterable
from typing import Any

from flask import Flask, Response, request


def _downgrade_shared_cache(
    status: str,
    headers: Iterable[tuple[str, str]],
    start_response: Callable[..., Any],
    exc_info: Any = None,
) -> Any:
    """Do not let a public immutable cache store a Set-Cookie response."""
    items = list(headers)
    if any(name.lower() == "set-cookie" for name, _ in items):
        cleaned: list[tuple[str, str]] = []
        for name, value in items:
            if name.lower() == "cache-control" and (
                "public" in value.lower() or "immutable" in value.lower()
            ):
                cleaned.append((name, "private, no-store"))
            else:
                cleaned.append((name, value))
        items = cleaned
    return start_response(status, items, exc_info)


def configure_navigation(app: Flask, build_sha: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{40}", build_sha):
        return  # Development assets must remain immediately revalidated.

    inner = app.wsgi_app

    def reject_cookie_cache(environ: dict[str, Any], start_response: Any) -> Any:
        def wrapped(
            status: str, headers: Iterable[tuple[str, str]], exc_info: Any = None
        ) -> Any:
            return _downgrade_shared_cache(status, headers, start_response, exc_info)

        return inner(environ, wrapped)

    app.wsgi_app = reject_cookie_cache  # type: ignore[method-assign]

    @app.url_defaults
    def version_static(endpoint: str, values: dict[str, Any]) -> None:
        if endpoint == "static":
            values.setdefault("v", build_sha)

    @app.after_request
    def cache_versioned_static(response: Response) -> Response:
        cache_control = response.headers.get("Cache-Control", "").lower()
        if (
            request.endpoint == "static"
            and request.args.get("v") == build_sha
            and response.status_code in {200, 304}
            and "Set-Cookie" not in response.headers
            and "no-store" not in cache_control
            and "private" not in cache_control
        ):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
