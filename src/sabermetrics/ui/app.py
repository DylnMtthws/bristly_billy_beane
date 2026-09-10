"""Flask application factory and waitress server configuration."""

import logging
import os
import secrets
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from sabermetrics.config import resolve_db_path

logger = logging.getLogger(__name__)


def _short_date(value: object) -> str:
    """Turn an ISO timestamp into compact, consumer-facing calendar text."""
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return "recently"
    label = f"{stamp.strftime('%b')} {stamp.day}"
    if stamp.year != datetime.now(stamp.tzinfo).year:
        label += f", {stamp.year}"
    return label


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
    app.jinja_env.filters["short_date"] = _short_date

    from sabermetrics.ui.navigation import configure_navigation

    configure_navigation(app, os.environ.get("SABER_BUILD_SHA", ""))

    db_path = resolve_db_path(db_path)
    app.config["DB_PATH"] = db_path
    app.config["DECK_LAB_ASSET_DIR"] = Path(
        os.environ.get(
            "SABER_DECK_LAB_ASSET_DIR", str(db_path.parent / "deck-lab-assets")
        )
    )
    redesign_enabled = _env_bool("SABER_DECK_LAB_REDESIGN", False)
    app.config["DECK_LAB_REDESIGN_ENABLED"] = redesign_enabled
    app.config["DECK_LAB_BUILDER_ENABLED"] = _env_bool(
        "SABER_DECK_LAB_BUILDER", redesign_enabled
    )
    app.config["DECK_LAB_RESEARCH_ENABLED"] = _env_bool(
        "SABER_DECK_LAB_RESEARCH", redesign_enabled
    )
    app.config["DECK_LAB_PLAYMAT_ENABLED"] = _env_bool(
        "SABER_DECK_LAB_PLAYMAT", redesign_enabled
    )
    app.config["DECK_LAB_DEV_MODE"] = _env_bool("SABER_DECK_LAB_DEV", False)

    # --- Auth mode ---
    # `tailscale`: identity comes from the tailscale serve proxy headers.
    # `password`:  email + argon2id with invite links (default; local dev).
    from sabermetrics.ui.auth import ALL_MODES, MODE_PASSWORD

    mode = os.environ.get("SABER_AUTH_MODE", MODE_PASSWORD).strip().lower()
    if mode not in ALL_MODES:
        raise ValueError(f"SABER_AUTH_MODE must be one of {ALL_MODES}, got {mode!r}")
    app.config["AUTH_MODE"] = mode

    # `public` means the app is reachable from the internet through a proxy.
    # It does not change routing; it tightens the posture, and it is opt-in so
    # that a private deployment is never accidentally held to a public policy
    # nor a public one to a private policy.
    public = _env_bool("SABER_PUBLIC", False)
    app.config["PUBLIC_DEPLOYMENT"] = public
    logger.info("Auth mode: %s (public=%s)", mode, public)

    if app.config["DECK_LAB_DEV_MODE"]:
        resolved = db_path.expanduser().resolve()
        protected = Path("/data/sabermetrics.db")
        repository_db = (Path.cwd() / "data" / "sabermetrics.db").resolve()
        if public or resolved == protected or resolved == repository_db:
            raise ValueError(
                "SABER_DECK_LAB_DEV requires a non-public deployment and an "
                "explicit disposable database outside data/sabermetrics.db."
            )

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

    if public:
        if not os.environ.get("MTG_V1_DSN", "").strip():
            raise ValueError(
                "MTG_V1_DSN must be set when SABER_PUBLIC=1. Set it to the "
                "mtg_consumer Postgres DSN with sslmode=require in the "
                "deployment environment."
            )
        if not os.environ.get("CEDH_SIMULATOR_URL", "").strip():
            raise ValueError(
                "CEDH_SIMULATOR_URL must be set when SABER_PUBLIC=1. Set it "
                "to the private simulator base URL in the deployment environment."
            )

    # --- Session cookie hardening ---
    # Secure defaults to on (the app is fronted by HTTPS via a proxy). For
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
    from sabermetrics.ui.issue_feedback import init_feedback

    init_feedback(app)

    @app.before_request
    def _limit_avatar_upload():
        from flask import request

        if request.endpoint == "main.profile_avatar" and request.method == "POST":
            # Bound the multipart body before CSRF processing parses the form.
            request.max_content_length = 10_000_000 + 65_536

    csrf.init_app(app)
    limiter.init_app(app)

    # --- Blueprints ---
    from sabermetrics.ui.admin_routes import bp as admin_bp
    from sabermetrics.ui.builder_routes import bp as builder_bp
    from sabermetrics.ui.cedh_routes import bp as cedh_bp
    from sabermetrics.ui.research_routes import bp as research_bp
    from sabermetrics.ui.routes import bp as main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(cedh_bp)
    app.register_blueprint(builder_bp)
    app.register_blueprint(research_bp)
    app.register_blueprint(main_bp)
    from sabermetrics.ui.recovery import init_recovery

    init_recovery(app)

    from sabermetrics import db
    from sabermetrics.avatars import (
        DEFAULT_EMOJI,
        EMOJIS,
        ICONS,
        avatar_for,
        ensure_avatar_schema,
    )

    if db_path.exists():
        with db.connect(db_path) as conn:
            ensure_avatar_schema(conn)
            conn.commit()

    @app.context_processor
    def _avatar_context():
        from flask_login import current_user

        avatar = {"kind": "emoji", "value": DEFAULT_EMOJI}
        if current_user.is_authenticated:
            avatar = avatar_for(db_path, current_user.id, current_user.avatar_emoji)
        return {
            "account_avatar": avatar,
            "avatar_icons": ICONS,
            "avatar_emojis": EMOJIS,
        }

    # A thread-pool job cannot survive a process restart. Make that state
    # explicit on boot instead of leaving a status page polling forever.
    interrupted = (
        db.BuildJobsRepo(db_path).fail_interrupted() if db_path.exists() else 0
    )
    if interrupted:
        logger.warning("Marked %s interrupted cEDH build jobs failed", interrupted)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Return process health without touching external services."""
        return {
            "status": "ok",
            "version": version("sabermetrics"),
            "build_sha": os.environ.get("SABER_BUILD_SHA", "unknown"),
        }

    from sabermetrics.research_cache import configure_research_cache

    configure_research_cache(app)

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
    host: str | None = None, port: int | None = None, db_path: Path | None = None
) -> None:
    """Start the UI server via waitress.

    Args:
        host: Bind address; defaults to ``SABER_BIND_HOST`` or localhost.
        port: Server port; defaults to ``SABER_PORT`` or 5000.
        db_path: Optional database path override.
    """
    host = host or os.environ.get("SABER_BIND_HOST", "127.0.0.1")
    port = port if port is not None else int(os.environ.get("SABER_PORT", "5000"))
    trusted_proxy = os.environ.get("SABER_TRUSTED_PROXY", "127.0.0.1")
    mode = os.environ.get("SABER_AUTH_MODE", "password").strip().lower()
    if _env_bool("SABER_PUBLIC", False) and host == "0.0.0.0" and mode == "tailscale":
        raise ValueError(
            "SABER_AUTH_MODE=tailscale is unsafe with SABER_PUBLIC=1 and "
            "SABER_BIND_HOST=0.0.0.0; use hybrid auth"
        )
    if _env_bool("SABER_DECK_LAB_DEV", False) and host not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("SABER_DECK_LAB_DEV previews must bind to localhost.")

    app = create_app(db_path)
    if _env_bool("SABER_RESEARCH_SYNC", False):
        if app.config["DECK_LAB_DEV_MODE"] or not os.environ.get("MTG_V1_DSN"):
            raise ValueError("Research sync requires MTG_V1_DSN and dev mode disabled")
        from sabermetrics.research_sync import start_refresh_worker

        start_refresh_worker(Path(app.config["DB_PATH"]))
    logger.info("Effective bind: %s:%s (trusted proxy: %s)", host, port, trusted_proxy)
    print(f"Sabermetrics UI running at http://{host}:{port}")

    try:
        from waitress import serve as waitress_serve

        # trusted_proxy is load-bearing, not tuning. Waitress defaults
        # `clear_untrusted_proxy_headers` to True, so it STRIPS X-Forwarded-*
        # from every request unless the sender is named here — and ProxyFix
        # then has nothing to read, leaving remote_addr as 127.0.0.1.
        #
        # That silently breaks tailnet auth: `tailscale serve` proxies from
        # localhost and sets X-Forwarded-For to the caller's tailnet address,
        # which is exactly what the identity source check verifies. Found by
        # deploying it; the test suite could not catch it because the Flask
        # test client is not waitress.
        #
        # Trusting 127.0.0.1 is the trust boundary already documented in
        # tailscale_auth: a process on this host is trusted, because it could
        # read the database directly anyway.
        waitress_serve(
            app,
            host=host,
            port=port,
            threads=8,
            trusted_proxy=trusted_proxy,
            trusted_proxy_count=1,
            trusted_proxy_headers={"x-forwarded-for", "x-forwarded-host"},
        )
    except ImportError:
        logger.warning("waitress not installed; falling back to the Flask dev server")
        app.run(host=host, port=port, debug=False)
