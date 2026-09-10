"""Freshness and corruption regressions found during coordinator review."""

import json
import sqlite3
import time

import pytest

from sabermetrics.research_cache import ResearchDefaultCache, public_payload
from scripts.setup_db import setup_database


def prepared(tmp_path):
    path = tmp_path / "corpus.db"
    setup_database(path)
    cache = ResearchDefaultCache(path)
    cache.install_schema()
    cache._publish(
        {
            "results": [],
            "total": 0,
            "recorded_entries": 0,
            "window_days": 90,
            "page": 1,
            "has_next": False,
        },
        cache.current_key(),
    )
    return cache


def test_later_changes_cannot_extend_stale_deadline(tmp_path):
    cache = prepared(tmp_path)
    with sqlite3.connect(cache.db_path) as conn:
        conn.execute(
            "UPDATE research_corpus_revision SET revision=revision+1, invalidated_at=?, changed_at=?",
            (int(time.time()) - 1000, int(time.time())),
        )
    assert cache.try_serve() is None
    # A later dirty epoch must not make that already-expired snapshot usable.
    with sqlite3.connect(cache.db_path) as conn:
        conn.execute(
            "UPDATE research_corpus_revision SET dirty=1, changed_at=?",
            (int(time.time()),),
        )
    cache.corpus_state()
    assert cache.try_serve() is None


@pytest.mark.parametrize(
    "missing", ["corpus_revision", "computed_at", "as_of_date", "payload"]
)
def test_structurally_invalid_snapshot_is_ignored(tmp_path, missing):
    cache = prepared(tmp_path)
    doc = json.loads(cache.snapshot_path.read_text())
    del doc[missing]
    cache.snapshot_path.write_text(json.dumps(doc))
    assert ResearchDefaultCache(cache.db_path).try_serve() is None


def test_request_freshness_does_not_require_writer_lock(tmp_path):
    cache = prepared(tmp_path)
    with sqlite3.connect(cache.db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("UPDATE research_corpus_revision SET dirty=1")
    writer = sqlite3.connect(cache.db_path)
    try:
        writer.execute("BEGIN IMMEDIATE")
        start = time.monotonic()
        assert cache.try_serve().freshness == "stale"
        assert time.monotonic() - start < 1
    finally:
        writer.rollback()
        writer.close()


def test_snapshot_removes_private_fields_at_every_depth():
    result = public_payload(
        {
            "email": "private@example.test",
            "results": [
                {
                    "id": "public-card",
                    "favorited": True,
                    "commanders": [{"id": "member", "user_id": "private-user"}],
                }
            ],
        }
    )
    assert result == {
        "results": [{"id": "public-card", "commanders": [{"id": "member"}]}]
    }
