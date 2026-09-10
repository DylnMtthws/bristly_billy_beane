"""Public default-cohort snapshot, corpus revision, and refresh coalescing."""

from __future__ import annotations

import json
import threading
import time
from datetime import date
from pathlib import Path
from unittest.mock import Mock

from sabermetrics import db
from sabermetrics.research import ResearchRepo
from sabermetrics.research_cache import (
    CALCULATION_VERSION,
    SNAPSHOT_SCHEMA,
    STALE_SERVE_SECONDS,
    ResearchDefaultCache,
    apply_favorites,
)
from scripts.setup_db import setup_database


def _cache(path: Path) -> ResearchDefaultCache:
    cache = ResearchDefaultCache(path)
    cache.install_schema()
    return cache


def test_corpus_revision_ignores_unrelated_writes(tmp_path):
    path = tmp_path / "revision.db"
    setup_database(path)
    cache = _cache(path)
    before, _ = cache.corpus_state()
    user = db.UsersRepo(path).create(
        email="cache@example.test",
        display_name="Cache",
        role="user",
        status="active",
    )
    with db.connect(path) as conn:
        conn.execute(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,color_identity,is_legal_commander,is_legal_in_99)
            VALUES('commander','oracle','Cache Commander',2,'Legendary Creature','[]',1,1)"""
        )
        conn.commit()
    after_card, _ = cache.corpus_state()
    assert after_card == before + 1
    db.FavoritesRepo(path).toggle_commander(user, "commander")
    with db.connect(path) as conn:
        conn.execute(
            "INSERT INTO users(id,email,display_name,role,status) VALUES(?,?,?,?,?)",
            ("other", "other@example.test", "Other", "user", "active"),
        )
        conn.execute("""INSERT INTO deck_documents(id,owner_id,title)
               VALUES('doc','other','Private')""")
        conn.commit()
    after_unrelated, _ = cache.corpus_state()
    assert after_unrelated == after_card
    with db.connect(path) as conn:
        conn.execute("""INSERT INTO tournament_results
            (id,tournament_id,deck_id,commander_id,tournament_date)
            VALUES('r1','t1','d1','commander','2026-01-01')""")
        conn.commit()
    after_results, _ = cache.corpus_state()
    assert after_results == after_card + 1
    with db.connect(path) as conn:
        conn.execute(
            """INSERT INTO research_commander_pairs(id,name,color_identity,cmc,card_ids)
               VALUES('pair-1','A + B','[]',4,'[]')"""
        )
        conn.commit()
    after_pairs, _ = cache.corpus_state()
    assert after_pairs == after_results + 1


def test_bulk_card_import_bumps_revision_once(tmp_path):
    path = tmp_path / "bulk.db"
    setup_database(path)
    cache = _cache(path)
    before, _ = cache.corpus_state()
    with db.connect(path) as conn:
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,color_identity,is_legal_commander,is_legal_in_99)
            VALUES(?,?,?,?,?,?,?,?)""",
            [
                (f"c{i}", f"o{i}", f"Card {i}", 1, "Creature", "[]", 0, 1)
                for i in range(8)
            ],
        )
        conn.commit()
    after, _ = cache.corpus_state()
    assert after == before + 1


def test_date_rollover_is_not_fresh(tmp_path, monkeypatch):
    path = tmp_path / "date.db"
    setup_database(path)
    cache = _cache(path)
    monkeypatch.setattr(
        "sabermetrics.research_cache.as_of_date", lambda: date(2026, 9, 10)
    )
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
    assert cache.try_serve() is not None
    assert cache.try_serve().freshness == "fresh"
    monkeypatch.setattr(
        "sabermetrics.research_cache.as_of_date", lambda: date(2026, 9, 11)
    )
    view = cache.try_serve()
    assert view is None or view.freshness == "stale"
    if view is not None:
        assert view.as_of_date == "2026-09-10"
        assert view.freshness != "fresh"


def test_restart_reuses_compatible_fresh_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "restart.db"
    setup_database(path)
    first = _cache(path)
    load = Mock(
        return_value={
            "results": [{"id": "c1", "name": "Cached"}],
            "total": 1,
            "recorded_entries": 0,
            "window_days": 90,
            "page": 1,
            "has_next": False,
        }
    )
    monkeypatch.setattr(ResearchRepo, "commanders", lambda self, **kwargs: load())
    assert first.compute_blocking(timeout=2).data["results"][0]["name"] == "Cached"
    assert load.call_count == 1
    payload = json.loads(first.snapshot_path.read_text())
    assert payload["schema"] == SNAPSHOT_SCHEMA
    assert payload["calculation_version"] == CALCULATION_VERSION
    assert "favorited" not in payload["payload"]["results"][0]
    assert "email" not in json.dumps(payload)
    second = ResearchDefaultCache(path)
    view = second.try_serve()
    assert view is not None and view.freshness == "fresh"
    assert view.data["results"][0]["name"] == "Cached"
    applied = apply_favorites(view.data, {"c1"})
    assert applied["results"][0]["favorited"] is True
    assert view.data["results"][0].get("favorited") is not True


def test_single_refresh_coalesces_callers(tmp_path, monkeypatch):
    path = tmp_path / "coalesce.db"
    setup_database(path)
    cache = _cache(path)
    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def slow(self, **kwargs):
        calls.append(1)
        started.set()
        assert release.wait(2)
        return {
            "results": [{"id": "ok", "name": "Ready"}],
            "total": 1,
            "recorded_entries": 0,
            "window_days": 90,
            "page": 1,
            "has_next": False,
        }

    monkeypatch.setattr(ResearchRepo, "commanders", slow)
    for _ in range(12):
        cache.request_refresh()
    assert started.wait(2)
    named = [
        thread
        for thread in threading.enumerate()
        if thread.name == "research-default-cache"
    ]
    assert len(named) == 1
    assert len(calls) == 1
    release.set()
    assert cache.wait_for_idle(3)
    assert len(calls) == 1


def test_corrupt_snapshot_and_failed_refresh_preserve_good_file(tmp_path, monkeypatch):
    path = tmp_path / "corrupt.db"
    setup_database(path)
    cache = _cache(path)
    good = {
        "results": [{"id": "keep", "name": "Keep"}],
        "total": 1,
        "recorded_entries": 0,
        "window_days": 90,
        "page": 1,
        "has_next": False,
    }
    monkeypatch.setattr(ResearchRepo, "commanders", lambda self, **kwargs: good)
    cache.compute_blocking(timeout=2)
    original = cache.snapshot_path.read_text()
    cache.snapshot_path.write_text("{not-json", encoding="utf-8")
    broken = ResearchDefaultCache(path)
    broken.install_schema()
    assert broken.try_serve() is None
    monkeypatch.setattr(
        ResearchRepo, "commanders", Mock(side_effect=RuntimeError("unavailable"))
    )
    broken.request_refresh()
    time.sleep(0.2)
    assert broken.try_serve() is None
    assert broken.snapshot_path.read_text() == "{not-json"
    broken.close()
    assert broken.wait_for_idle(3)
    cache.snapshot_path.write_text(original, encoding="utf-8")
    restored = ResearchDefaultCache(path)
    restored.install_schema()
    view = restored.try_serve()
    assert view is not None and view.data["results"][0]["name"] == "Keep"
    monkeypatch.setattr(
        ResearchRepo, "commanders", Mock(side_effect=RuntimeError("unavailable"))
    )
    restored.request_refresh()
    assert restored.wait_for_idle(2)
    assert restored.try_serve() is not None
    assert restored.snapshot_path.read_text() == original


def test_stale_snapshot_expires_after_fifteen_minutes(tmp_path):
    path = tmp_path / "stale.db"
    setup_database(path)
    cache = _cache(path)
    cache._publish(
        {
            "results": [{"id": "old", "name": "Old"}],
            "total": 1,
            "recorded_entries": 0,
            "window_days": 90,
            "page": 1,
            "has_next": False,
        },
        cache.current_key(),
    )
    with db.connect(path) as conn:
        conn.execute("""INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,color_identity,is_legal_commander,is_legal_in_99)
            VALUES('n1','on','New',1,'Creature','[]',0,1)""")
        conn.commit()
    recent = cache.try_serve()
    assert recent is not None and recent.freshness == "stale"
    assert recent.data["results"][0]["name"] == "Old"
    stale_since = int(time.time()) - STALE_SERVE_SECONDS - 5
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE research_corpus_revision SET changed_at=?",
            (stale_since,),
        )
        conn.commit()
    assert cache.try_serve() is None


def test_favorites_helper_does_not_mutate_public_payload():
    data = {"results": [{"id": "a", "name": "A"}]}
    copied = apply_favorites(data, {"a"})
    assert copied["results"][0]["favorited"] is True
    assert "favorited" not in data["results"][0]
