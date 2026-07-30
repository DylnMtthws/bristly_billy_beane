"""Flask application factory (D7.1 + P1 auth hardening).

Creates the Flask app bound to 127.0.0.1 only. Public access is expected to
come through a Cloudflare Tunnel (ADR-016), so the app trusts Cloudflare's
forwarded headers (ProxyFix) and enables the security posture required once the
app is internet-reachable: hashed passwords (in the auth layer), CSRF on POSTs,
hardened session cookies, and login rate-limiting.
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

    # --- Secret key: required for signed session cookies + CSRF ---
    secret = os.environ.get("SABER_SECRET_KEY")
    if not secret:
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
        # Behind the Cloudflare Tunnel the public origin is HTTPS; make
        # url_for(_external=True) (e.g. invite links) emit https.
        PREFERRED_URL_SCHEME="https",
    )

    # --- Trust Cloudflare's forwarded headers (1 proxy hop) ---
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
    from sabermetrics.ui.routes import bp as main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(main_bp)

    logger.info("Flask app created, DB: %s", db_path)
    return app


def run_server(host: str = "127.0.0.1", port: int = 5000, db_path: Path | None = None) -> None:
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
