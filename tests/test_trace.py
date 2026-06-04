"""Tests for the generation trace logging system."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from sabermetrics.pipeline.trace import GenerationTracer, TraceEvent, get_trace


@pytest.fixture
def db_path():
    """Create a temporary database for trace tests."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = Path(tmp.name)
    tmp.close()
    return path


class TestTraceEvent:
    """Tests for the TraceEvent model."""

    def test_minimal_event(self):
        event = TraceEvent(
            card_name="Sol Ring",
            stage="pareto",
            action="protected",
        )
        assert event.card_name == "Sol Ring"
        assert event.card_id is None
        assert event.score is None
        assert event.score_components is None
        assert event.reason == ""
        assert event.timestamp > 0

    def test_full_event(self):
        event = TraceEvent(
            card_name="Sol Ring",
            card_id="abc-123",
            stage="greedy_fill",
            action="placed",
            score=0.75,
            score_components={"cvar": 0.35, "synergy": 0.2},
            reason="role=ramp, price=$0.50",
        )
        assert event.card_id == "abc-123"
        assert event.score == 0.75
        assert event.score_components == {"cvar": 0.35, "synergy": 0.2}


class TestGenerationTracer:
    """Tests for the GenerationTracer class."""

    def test_record_watchlisted_card(self):
        tracer = GenerationTracer("gen-1", watchlist={"Sol Ring", "Lightning Bolt"})
        tracer.record(
            card_name="Sol Ring",
            stage="pareto",
            action="protected",
            reason="auto-include exempt",
        )
        assert len(tracer.events) == 1
        assert tracer.events[0].card_name == "Sol Ring"

    def test_record_non_watchlisted_card_filtered(self):
        tracer = GenerationTracer("gen-1", watchlist={"Sol Ring"})
        tracer.record(
            card_name="Random Card",
            stage="pareto",
            action="rejected",
            reason="dominated",
        )
        assert len(tracer.events) == 0

    def test_record_force_bypasses_watchlist(self):
        tracer = GenerationTracer("gen-1", watchlist={"Sol Ring"})
        tracer.record(
            card_name="Random Card",
            stage="swap_refine",
            action="swapped_out",
            reason="obj delta +0.02",
            force=True,
        )
        assert len(tracer.events) == 1
        assert tracer.events[0].card_name == "Random Card"

    def test_record_empty_watchlist_records_nothing(self):
        tracer = GenerationTracer("gen-1", watchlist=set())
        tracer.record(
            card_name="Sol Ring",
            stage="pareto",
            action="protected",
        )
        assert len(tracer.events) == 0

    def test_set_generation_id(self):
        tracer = GenerationTracer("pending")
        assert tracer.generation_id == "pending"
        tracer.set_generation_id("real-id-123")
        assert tracer.generation_id == "real-id-123"

    def test_events_returns_copy(self):
        tracer = GenerationTracer("gen-1", watchlist={"Sol Ring"})
        tracer.record(card_name="Sol Ring", stage="pareto", action="protected")
        events = tracer.events
        events.clear()
        assert len(tracer.events) == 1  # Original unaffected

    def test_flush_creates_table_and_inserts(self, db_path):
        tracer = GenerationTracer("gen-1", watchlist={"Sol Ring", "Lightning Bolt"})
        tracer.record(
            card_name="Sol Ring",
            stage="pareto",
            action="protected",
            card_id="sol-ring-id",
            score=0.9,
            reason="auto-include exempt",
        )
        tracer.record(
            card_name="Lightning Bolt",
            stage="greedy_fill",
            action="placed",
            card_id="bolt-id",
            score=0.7,
            score_components={"cvar": 0.4, "synergy": 0.3},
            reason="role=removal, price=$0.25",
        )

        count = tracer.flush(db_path)
        assert count == 2

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT * FROM generation_traces WHERE generation_id = ?",
            ("gen-1",),
        ).fetchall()
        conn.close()
        assert len(rows) == 2

    def test_flush_empty_tracer(self, db_path):
        tracer = GenerationTracer("gen-1", watchlist=set())
        count = tracer.flush(db_path)
        assert count == 0

    def test_flush_preserves_score_components_json(self, db_path):
        tracer = GenerationTracer("gen-1", watchlist={"Test Card"})
        components = {"cvar": 0.4, "synergy": 0.3, "role_mult": 1.2}
        tracer.record(
            card_name="Test Card",
            stage="greedy_fill",
            action="placed",
            score=0.75,
            score_components=components,
        )
        tracer.flush(db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT score_components_json FROM generation_traces "
            "WHERE card_name = 'Test Card'"
        ).fetchone()
        conn.close()

        import json
        parsed = json.loads(row["score_components_json"])
        assert parsed["cvar"] == 0.4
        assert parsed["synergy"] == 0.3

    def test_multiple_flushes_accumulate(self, db_path):
        tracer1 = GenerationTracer("gen-1", watchlist={"Card A"})
        tracer1.record(card_name="Card A", stage="pareto", action="protected")
        tracer1.flush(db_path)

        tracer2 = GenerationTracer("gen-2", watchlist={"Card B"})
        tracer2.record(card_name="Card B", stage="pareto", action="rejected")
        tracer2.flush(db_path)

        conn = sqlite3.connect(str(db_path))
        total = conn.execute(
            "SELECT COUNT(*) FROM generation_traces"
        ).fetchone()[0]
        conn.close()
        assert total == 2


class TestGetTrace:
    """Tests for the get_trace() query function."""

    def _populate(self, db_path):
        """Helper to populate trace data for query tests."""
        tracer = GenerationTracer("gen-abc", watchlist={"Sol Ring", "Mind Stone", "Bolt"})
        tracer.record(
            card_name="Sol Ring",
            stage="pareto",
            action="protected",
            card_id="sol-ring-id",
            score=0.9,
            reason="auto-include exempt",
        )
        tracer.record(
            card_name="Sol Ring",
            stage="infra_ramp",
            action="placed",
            card_id="sol-ring-id",
            score=0.9,
            reason="infrastructure ramp",
        )
        tracer.record(
            card_name="Mind Stone",
            stage="pareto",
            action="considered",
            card_id="mind-stone-id",
            score=0.6,
            reason="survived Pareto",
        )
        tracer.record(
            card_name="Bolt",
            stage="swap_refine",
            action="swapped_out",
            card_id="bolt-id",
            score=0.3,
            reason="pass 1, obj delta +0.01",
            force=True,
        )
        tracer.flush(db_path)

    def test_get_all_events(self, db_path):
        self._populate(db_path)
        df = get_trace(db_path, "gen-abc")
        assert len(df) == 4
        assert list(df.columns) == [
            "card_name", "card_id", "stage", "action",
            "score", "score_components_json", "reason", "timestamp",
        ]

    def test_get_filtered_by_card_names(self, db_path):
        self._populate(db_path)
        df = get_trace(db_path, "gen-abc", card_names=["Sol Ring"])
        assert len(df) == 2
        assert all(df["card_name"] == "Sol Ring")

    def test_get_multiple_card_names(self, db_path):
        self._populate(db_path)
        df = get_trace(db_path, "gen-abc", card_names=["Sol Ring", "Bolt"])
        assert len(df) == 3

    def test_get_nonexistent_generation(self, db_path):
        self._populate(db_path)
        df = get_trace(db_path, "gen-nonexistent")
        assert len(df) == 0

    def test_get_nonexistent_card_name(self, db_path):
        self._populate(db_path)
        df = get_trace(db_path, "gen-abc", card_names=["Nonexistent Card"])
        assert len(df) == 0

    def test_results_ordered_by_timestamp(self, db_path):
        self._populate(db_path)
        df = get_trace(db_path, "gen-abc")
        timestamps = df["timestamp"].tolist()
        assert timestamps == sorted(timestamps)


class TestDeckBuildRequestTraceCards:
    """Test that DeckBuildRequest accepts trace_cards."""

    def test_trace_cards_default_none(self):
        from sabermetrics.pipeline.deck_builder import DeckBuildRequest

        req = DeckBuildRequest(commander_id="test-id")
        assert req.trace_cards is None

    def test_trace_cards_set(self):
        from sabermetrics.pipeline.deck_builder import DeckBuildRequest

        req = DeckBuildRequest(
            commander_id="test-id",
            trace_cards=["Sol Ring", "Mind Stone"],
        )
        assert req.trace_cards == ["Sol Ring", "Mind Stone"]
