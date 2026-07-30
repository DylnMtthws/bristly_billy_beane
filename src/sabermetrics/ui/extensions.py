"""Shared Flask extension singletons, initialized in the app factory (P1).

Kept in a separate module so blueprints can import and use the extensions
(e.g. ``@limiter.limit(...)``) without importing the app factory, avoiding
circular imports.
"""

from __future__ import annotations

from flask import request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect


def client_ip() -> str:
    """Rate-limit key: the real client IP behind the Cloudflare Tunnel.

    Cloudflare forwards the origin IP in ``CF-Connecting-IP``; fall back to the
    peer address for direct/local access.
    """
    return request.headers.get("CF-Connecting-IP") or get_remote_address()


csrf = CSRFProtect()
limiter = Limiter(key_func=client_ip, default_limits=[], storage_uri="memory://")
