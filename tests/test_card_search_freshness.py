"""Search must reflect catalog commits across fresh request connections."""

import sqlite3
from pathlib import Path

from sabermetrics.card_search import (
    _CATALOG_LOCK,
    _CATALOGS,
    _MAX_CATALOGS,
    reset_search_catalog_cache,
)
from sabermetrics.deck_documents import DeckDocumentRepo
from scripts.setup_db import setup_database


def insert_card(path, key, name, legal=1, colors='["U"]'):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,is_legal_in_99,color_identity) "
            "VALUES(?,?,?,?,?)",
            (key, key, name, legal, colors),
        )


def names(path, **kwargs):
    return [row["name"] for row in DeckDocumentRepo(path).search_cards(**kwargs)]


def wipe_sqlite(path):
    path = Path(path)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        candidate.unlink(missing_ok=True)


def test_catalog_updates_are_visible_to_new_repository_connections(tmp_path):
    path = tmp_path / "catalog.db"
    setup_database(path)
    insert_card(path, "a", "Alpha")
    assert names(path, query="Alpha") == ["Alpha"]
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE cards SET name='Beta',color_identity='[\"R\"]' WHERE id='a'"
        )
    assert names(path, query="Alpha") == []
    assert names(path, query="Beta", allowed_colors={"U"}) == []
    assert names(path, query="Beta", allowed_colors={"R"}) == ["Beta"]
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE cards SET is_legal_in_99=0 WHERE id='a'")
    assert names(path, query="Beta") == []
    insert_card(path, "b", "Gamma")
    assert names(path, query="Gamma") == ["Gamma"]
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM cards WHERE id='b'")
    assert names(path, query="Gamma") == []


def test_repeated_setup_preserves_populated_catalog(tmp_path):
    path = tmp_path / "catalog.db"
    setup_database(path)
    insert_card(path, "a", "Urza's Example")
    setup_database(path)
    setup_database(path)
    assert names(path, query="Urzas") == ["Urza's Example"]


def test_path_reuse_does_not_serve_stale_catalog(tmp_path):
    path = tmp_path / "reuse.db"
    setup_database(path)
    insert_card(path, "a", "Alpha Probe")
    assert names(path, query="Alpha Probe") == ["Alpha Probe"]
    wipe_sqlite(path)
    setup_database(path)
    insert_card(path, "b", "Beta Probe")
    assert names(path, query="Alpha Probe") == []
    assert names(path, query="Beta Probe") == ["Beta Probe"]


def test_catalog_cache_evicts_when_bounded(tmp_path):
    reset_search_catalog_cache()
    for index in range(_MAX_CATALOGS + 2):
        path = tmp_path / f"cache-{index}.db"
        setup_database(path)
        insert_card(path, f"c{index}", f"Cache Card {index}")
        assert names(path, query=f"Cache Card {index}") == [f"Cache Card {index}"]
    with _CATALOG_LOCK:
        assert len(_CATALOGS) <= _MAX_CATALOGS
    first = tmp_path / "cache-0.db"
    insert_card(first, "fresh", "Brand New Cache Card")
    assert names(first, query="Brand New Cache Card") == ["Brand New Cache Card"]


def test_cold_search_is_read_only_after_schema_setup(tmp_path):
    from sabermetrics.card_search import search_cards

    path = tmp_path / "readonly.db"
    setup_database(path)
    insert_card(path, "a", "Alpha")
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA query_only=ON")
        assert len(search_cards(conn, db_key=str(path), query="Alpha")) == 1


def test_unknown_identity_symbols_do_not_become_colorless(tmp_path):
    path = tmp_path / "invalid-colors.db"
    setup_database(path)
    insert_card(path, "a", "Alpha", colors='["X"]')
    assert (
        DeckDocumentRepo(path).search_cards(query="Alpha", allowed_colors={"U"}) == []
    )


def test_server_warms_catalog_before_accepting_requests(tmp_path, monkeypatch):
    from sabermetrics import card_search
    from sabermetrics.ui.app import run_server

    path = tmp_path / "startup.db"
    setup_database(path)
    insert_card(path, "a", "Alpha")
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    monkeypatch.setenv("SABER_RESEARCH_SYNC", "0")
    loads = []
    original = card_search._load_catalog

    def counted(conn):
        loads.append(True)
        return original(conn)

    monkeypatch.setattr(card_search, "_load_catalog", counted)
    served = []

    def accept(app, **kwargs):
        assert len(loads) == 1
        assert len(DeckDocumentRepo(path).search_cards(query="Alpha")) == 1
        assert len(loads) == 1
        served.append(True)

    monkeypatch.setattr("waitress.serve", accept)
    run_server(db_path=path)
    assert served == [True]
