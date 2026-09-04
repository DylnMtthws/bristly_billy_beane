"""Flask application factory.

Creates the Flask app bound to 127.0.0.1 only. Access comes through
``tailscale serve``, which terminates TLS on the tailnet and proxies to that
local port (ADR-026), so the app trusts one hop of forwarded headers via
ProxyFix and keeps the rest of the hardening: CSRF on POSTs, hardened session
cookies, and login rate-limiting.

The app is never bound to a public interface and never exposed by a port
forward. On a tailnet there is no public surface to attack at all, which is a
stronger position than the Cloudflare Tunnel this replaced — and one command
instead of a tunnel daemon, a config file and a DNS record.
"""

import logging
import os
import secrets
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def create_app(db_path: Path | None = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        db_path: Path to SQLite database. Defaults to data/sabermetrics.db.

    Returns:
        Configured Flask app instance.
    """
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )

    if db_path is None:
        db_path = Path("data/sabermetrics.db")
    app.config["DB_PATH"] = db_path

    # --- Auth mode ---
    # `tailscale`: identity comes from the tailscale serve proxy headers.
    # `password`:  email + argon2id with invite links (default; local dev).
    from sabermetrics.ui.auth import ALL_MODES, MODE_PASSWORD

    mode = os.environ.get("SABER_AUTH_MODE", MODE_PASSWORD).strip().lower()
    if mode not in ALL_MODES:
        raise ValueError(f"SABER_AUTH_MODE must be one of {ALL_MODES}, got {mode!r}")
    app.config["AUTH_MODE"] = mode

    # `public` means the app is reachable from the internet (Tailscale Funnel).
    # It does not change routing; it tightens the posture, and it is opt-in so
    # that a private deployment is never accidentally held to a public policy
    # nor a public one to a private policy.
    public = _env_bool("SABER_PUBLIC", False)
    app.config["PUBLIC_DEPLOYMENT"] = public
    logger.info("Auth mode: %s (public=%s)", mode, public)

    # --- Secret key: required for signed session cookies + CSRF ---
    secret = os.environ.get("SABER_SECRET_KEY")
    if not secret:
        if _env_bool("SABER_PUBLIC", False):
            # A random per-process key on a public deployment means CSRF tokens
            # and sessions break on every restart, and it is the kind of thing
            # that gets noticed as "flaky logins" rather than as a
            # misconfiguration. Refuse to start instead.
            raise ValueError(
                "SABER_SECRET_KEY must be set when SABER_PUBLIC=1. Generate "
                "one with: python -c 'import secrets; "
                "print(secrets.token_hex(32))'"
            )
        secret = secrets.token_hex(32)
        logger.warning(
            "SABER_SECRET_KEY not set — using a random key. Sessions will not "
            "survive a restart; set SABER_SECRET_KEY in .env for production."
        )
    app.config["SECRET_KEY"] = secret

    # --- Session cookie hardening ---
    # Secure defaults to on (the app is fronted by HTTPS via the tunnel). For
    # local http previews, set SABER_COOKIE_SECURE=0 so the cookie is sent.
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_env_bool("SABER_COOKIE_SECURE", True),
        WTF_CSRF_ENABLED=True,
        WTF_CSRF_TIME_LIMIT=None,  # tie CSRF validity to the session
    )

    # --- Trust exactly one proxy hop (tailscale serve) ---
    # x_for=1 is what makes request.remote_addr the *tailnet* address of the
    # calling node rather than 127.0.0.1, which is what the identity-source
    # check in tailscale_auth verifies against. More than one hop would let a
    # caller prepend its own X-Forwarded-For entry; there is only one proxy.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]

    # --- Extensions ---
    from sabermetrics.ui.auth import bp as auth_bp
    from sabermetrics.ui.auth import login_manager
    from sabermetrics.ui.extensions import csrf, limiter

    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    # --- Blueprints ---
    from sabermetrics.ui.admin_routes import bp as admin_bp
    from sabermetrics.ui.cedh_routes import bp as cedh_bp
    from sabermetrics.ui.routes import bp as main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(cedh_bp)
    app.register_blueprint(main_bp)

    @app.after_request
    def _security_headers(response):
        """Baseline response hardening.

        Cheap, and none of it is conditional on the deployment being public —
        a header that is only correct sometimes is a header nobody can reason
        about. HSTS is the exception: it is meaningless over plain http and
        would pin a stale policy on a local preview, so it is public-only.
        """
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        if app.config.get("PUBLIC_DEPLOYMENT"):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    logger.info("Flask app created, DB: %s", db_path)
    return app


def run_server(
    host: str = "127.0.0.1", port: int = 5000, db_path: Path | None = None
) -> None:
    """Start the UI server via waitress (production WSGI, macOS-friendly).

    The app always binds to 127.0.0.1; public access is via the Cloudflare
    Tunnel (ADR-016), never a direct 0.0.0.0 bind.

    Args:
        host: Bind address (forced to 127.0.0.1 for security).
        port: Server port.
        db_path: Optional database path override.
    """
    if host != "127.0.0.1":
        logger.warning(
            "Security: overriding host to 127.0.0.1 (local-only bind; expose "
            "via Cloudflare Tunnel, not a direct port)"
        )
        host = "127.0.0.1"

    app = create_app(db_path)
    print(f"Sabermetrics UI running at http://{host}:{port}")

    try:
        from waitress import serve as waitress_serve

        waitress_serve(app, host=host, port=port, threads=8)
    except ImportError:
        logger.warning("waitress not installed; falling back to the Flask dev server")
        app.run(host=host, port=port, debug=False)
