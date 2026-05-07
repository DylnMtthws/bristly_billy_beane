"""Flask application factory (D7.1).

Creates the Flask app bound to 127.0.0.1 only (per security model).
"""

import logging
from pathlib import Path

from flask import Flask

logger = logging.getLogger(__name__)


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
    app.config["SECRET_KEY"] = "sabermetrics-local-only"

    # Register routes
    from sabermetrics.ui.routes import bp

    app.register_blueprint(bp)

    logger.info("Flask app created, DB: %s", db_path)
    return app


def run_server(host: str = "127.0.0.1", port: int = 5000, db_path: Path | None = None) -> None:
    """Start the Flask development server.

    Args:
        host: Bind address (127.0.0.1 only for security).
        port: Server port.
        db_path: Optional database path override.
    """
    if host != "127.0.0.1":
        logger.warning(
            "Security: overriding host to 127.0.0.1 (local-only access)"
        )
        host = "127.0.0.1"

    app = create_app(db_path)
    print(f"Sabermetrics UI running at http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
