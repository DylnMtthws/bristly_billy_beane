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
    """Rate-limit key: the calling node's address.

    ProxyFix has already rewritten ``remote_addr`` from the single trusted
    ``X-Forwarded-For`` hop that ``tailscale serve`` sets, so this is the
    tailnet address of the caller rather than 127.0.0.1.

    This previously read ``CF-Connecting-IP``, for a Cloudflare Tunnel that was
    never deployed. That header is never present, so every request fell through
    to the same proxy address and the login limiter was effectively global —
    one person fat-fingering a password could throttle everyone.
    """
    return get_remote_address()


csrf = CSRFProtect()
limiter = Limiter(key_func=client_ip, default_limits=[], storage_uri="memory://")
