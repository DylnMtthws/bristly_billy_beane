"""Database backup and mounted-volume first-run behavior."""

import sqlite3

from click.testing import CliRunner

from sabermetrics.main import cli


def test_db_backup_creates_consistent_copy(monkeypatch, tmp_path):
    from scripts.setup_db import setup_database

    source = tmp_path / "source.db"
    setup_database(source)
    with sqlite3.connect(source) as conn:
        conn.execute(
            "INSERT INTO source_health (source, consecutive_failures) VALUES ('x', 0)"
        )
        conn.commit()

    monkeypatch.setattr("sabermetrics.main._default_db_path", lambda: source)
    dest = tmp_path / "backups" / "snapshot.db"
    result = CliRunner().invoke(cli, ["db-backup", str(dest)])
    assert result.exit_code == 0, result.output
    with sqlite3.connect(dest) as conn:
        assert conn.execute("SELECT source FROM source_health").fetchone() == ("x",)


def test_serve_initializes_database_in_empty_mounted_directory(monkeypatch, tmp_path):
    db_path = tmp_path / "mounted" / "sabermetrics.db"
    monkeypatch.setenv("SABER_DB_PATH", str(db_path))
    monkeypatch.setattr("sabermetrics.ui.app.run_server", lambda **kwargs: None)
    result = CliRunner().invoke(cli, ["serve"])
    assert result.exit_code == 0, result.output
    assert db_path.exists()
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"users", "cedh_candidates", "card_feedback"} <= tables
