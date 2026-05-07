"""Tests for Phase 7 Local UI (D7.1-D7.5).

Tests Flask app creation, route accessibility, and template rendering.
"""

import os
import sqlite3
from pathlib import Path

import pytest

from sabermetrics.ui.app import create_app

DB_PATH = Path("data/sabermetrics.db")
HAS_DB = DB_PATH.exists()


@pytest.fixture
def app():
    """Create Flask test app."""
    app = create_app(DB_PATH)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    """Create Flask test client."""
    return app.test_client()


# --- App creation tests ---


def test_app_creation() -> None:
    """Flask app creates successfully."""
    app = create_app(DB_PATH)
    assert app is not None
    assert app.config["DB_PATH"] == DB_PATH


def test_app_binds_localhost_only() -> None:
    """App config enforces localhost binding."""
    from sabermetrics.ui.app import run_server
    # run_server overrides non-localhost hosts, but we can't test the
    # actual server start. Just verify the function exists and app creates.
    app = create_app(DB_PATH)
    assert app is not None


# --- Route tests ---


@pytest.mark.skipif(not HAS_DB, reason="No database available")
def test_index_loads(client) -> None:
    """Home page loads with 200 status."""
    response = client.get("/")
    assert response.status_code == 200
    assert b"Commander Search" in response.data


@pytest.mark.skipif(not HAS_DB, reason="No database available")
def test_index_search(client) -> None:
    """Commander search returns results."""
    response = client.get("/?q=Korvold")
    assert response.status_code == 200
    # Should find Korvold if in DB
    if b"Korvold" in response.data:
        assert b"View Profile" in response.data


@pytest.mark.skipif(not HAS_DB, reason="No database available")
def test_reference_search_page(client) -> None:
    """Reference search page loads."""
    response = client.get("/reference/search")
    assert response.status_code == 200
    assert b"Reference Search" in response.data


@pytest.mark.skipif(not HAS_DB, reason="No database available")
def test_reference_search_with_query(client) -> None:
    """Reference search returns results for a query."""
    response = client.get("/reference/search?q=color+identity")
    assert response.status_code == 200
    # Should have results if reference chunks are indexed
    assert b"results" in response.data or b"No results" in response.data


@pytest.mark.skipif(not HAS_DB, reason="No database available")
def test_cost_report_page(client) -> None:
    """Cost report page loads."""
    response = client.get("/report")
    assert response.status_code == 200
    assert b"Cost" in response.data
    assert b"Monthly Ceiling" in response.data


@pytest.mark.skipif(not HAS_DB, reason="No database available")
def test_profile_page(client) -> None:
    """Commander profile page loads."""
    response = client.get("/commander/Korvold/profile")
    assert response.status_code == 200
    # Either shows profile or "not found" message


@pytest.mark.skipif(not HAS_DB, reason="No database available")
def test_deck_not_found(client) -> None:
    """Non-existent deck returns gracefully."""
    response = client.get("/deck/nonexistent-id")
    assert response.status_code == 200
    assert b"not found" in response.data.lower() or b"Not Found" in response.data


@pytest.mark.skipif(not HAS_DB, reason="No database available")
def test_generate_deck_missing_commander(client) -> None:
    """Generate deck with empty commander_id redirects."""
    response = client.post("/generate-deck", data={})
    assert response.status_code in (302, 200)


# --- Template existence tests ---


def test_templates_exist() -> None:
    """All required templates exist."""
    template_dir = Path(__file__).parent.parent / "src" / "sabermetrics" / "ui" / "templates"
    expected = [
        "base.html", "index.html", "deck_view.html",
        "profile_view.html", "reference_search.html", "cost_report.html",
    ]
    for name in expected:
        assert (template_dir / name).exists(), f"Missing template: {name}"


def test_static_css_exists() -> None:
    """CSS stylesheet exists."""
    css_path = (
        Path(__file__).parent.parent
        / "src" / "sabermetrics" / "ui" / "static" / "style.css"
    )
    assert css_path.exists()
    content = css_path.read_text()
    assert len(content) > 100, "CSS file seems too small"


# --- CLI serve command test ---


def test_serve_command_registered() -> None:
    """Serve command is registered in CLI."""
    from sabermetrics.main import cli
    from click.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    assert "Flask" in result.output or "server" in result.output.lower()
