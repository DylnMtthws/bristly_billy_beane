"""End-to-end build guarantees (Option A criteria 2, 4, 5).

One hermetic build (no API, on a DB copy) that asserts the properties several
criteria share, so the expensive full build runs once:
  * legality invariant (exactly 99, singleton, in color identity)  [crit 2]
  * zero per-card card_fit LLM calls during selection               [crit 4]
  * degradation is observable (signals recorded + persisted)        [crit 5]

Embeddings are patched off, which forces the embedding signal unavailable *and*
avoids loading the sentence-transformers model (keeping the build fast).
"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

DB = Path("data/sabermetrics.db")


@pytest.mark.skipif(not DB.exists(), reason="needs populated card DB")
def test_end_to_end_build_is_legal_llm_free_and_observable(
    build_db, canned_profile
) -> None:
    from sabermetrics.pipeline.deck_builder import (
        DeckBuilder,
        DeckBuildRequest,
        _BASIC_LAND_NAMES,
    )

    conn = sqlite3.connect(str(build_db))
    row = conn.execute(
        "SELECT id, color_identity FROM cards WHERE is_legal_commander = 1 "
        "AND color_identity != '[]' LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None, "no legal commander in DB"
    commander_id, colors = row[0], json.loads(row[1])
    canned = canned_profile(commander_id, colors)

    with (
        patch(
            "sabermetrics.reasoning.profiler.ProfileManager.generate_profile",
            return_value=canned,
        ),
        patch(
            "sabermetrics.reasoning.synthesis.DeckSynthesizer.synthesize",
            side_effect=RuntimeError("narrative disabled for test"),
        ),
        patch(
            "sabermetrics.analytics.synergy_matrix._compute_embedding_matrix",
            side_effect=lambda c: (np.zeros((len(c), len(c)), np.float32), False),
        ),
        patch(
            "sabermetrics.reasoning.fit.FitScorer.score_cards",
            side_effect=AssertionError("card_fit must not be called in selection"),
        ) as fit_mock,
    ):
        result = DeckBuilder(build_db).build(
            DeckBuildRequest(commander_id=commander_id, budget_usd=200.0)
        )

    # crit 4: no per-card LLM in selection
    assert fit_mock.call_count == 0

    # crit 2: legality invariant
    cards = result.deck.cards
    assert len(cards) == 99, f"expected 99 cards, got {len(cards)}"
    ci = set(result.deck.commander.color_identity)
    seen: set[str] = set()
    for dc in cards:
        nm = dc.card.name
        if nm in _BASIC_LAND_NAMES:
            continue
        assert nm not in seen, f"duplicate nonbasic: {nm}"
        seen.add(nm)
        assert set(dc.card.color_identity) <= ci

    # crit 5: observable degradation, recorded on the deck and persisted
    meta = result.deck.meta
    assert "rules" in meta.signals_used
    assert "embeddings" in meta.signals_unavailable
    assert "narrative" in meta.signals_unavailable

    conn = sqlite3.connect(str(build_db))
    rat = conn.execute(
        "SELECT rationale FROM generated_decks WHERE id = ?", (result.deck.id,)
    ).fetchone()[0]
    conn.close()
    stored = json.loads(rat)
    assert "rules" in stored["signals_used"]
    assert "embeddings" in stored["signals_unavailable"]
