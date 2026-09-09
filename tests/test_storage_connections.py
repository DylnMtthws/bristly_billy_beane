"""Repository transactions must release handles on success and failure."""

import sqlite3

import pytest

from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.research import ResearchRepo


def test_deck_transaction_commits_and_closes(tmp_path):
    repo = DeckDocumentRepo(tmp_path / "db.sqlite")
    with repo._connect() as conn:
        conn.execute("create table test(value text)")
        conn.execute("insert into test values ('saved')")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("select 1")
    with repo._connect() as conn:
        assert conn.execute("select value from test").fetchone()[0] == "saved"


def test_deck_transaction_rolls_back_and_closes_on_error(tmp_path):
    repo = DeckDocumentRepo(tmp_path / "db.sqlite")
    with repo._connect() as conn:
        conn.execute("create table test(value text)")
    with pytest.raises(RuntimeError), repo._connect() as conn:
        conn.execute("insert into test values ('discard')")
        raise RuntimeError("abort")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("select 1")
    with repo._connect() as conn:
        assert conn.execute("select count(*) from test").fetchone()[0] == 0


def test_research_connection_is_read_only_and_closes(tmp_path):
    repo = ResearchRepo(tmp_path / "db.sqlite")
    with (
        repo._connect() as conn,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        conn.execute("create table test(value text)")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("select 1")
