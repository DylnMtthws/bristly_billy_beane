"""Observable degradation (Option A DoD criterion 5).

A build records which scoring/data signals were live and which were unavailable,
on the deck metadata and in the persisted rationale. The end-to-end proof (build
records + persists a forced-missing signal) lives in test_end_to_end_build.py;
these are the fast unit checks.
"""

from pathlib import Path
from unittest.mock import patch

import numpy as np


def _min_card(cid, oracle="draw a card", role="utility"):
    return {
        "id": cid, "name": cid, "oracle_text": oracle, "type_line": "Creature",
        "keywords": "[]", "cmc": 2.0, "role_tags": f'["{role}"]',
    }


def test_synergy_matrix_records_signal_availability() -> None:
    """build_synergy_matrix reports rules live and embeddings unavailable."""
    from sabermetrics.analytics.synergy_matrix import build_synergy_matrix

    cards = [_min_card("a"), _min_card("b", role="ramp")]
    with patch(
        "sabermetrics.analytics.synergy_matrix._compute_embedding_matrix",
        side_effect=lambda c: (np.zeros((len(c), len(c)), np.float32), False),
    ):
        m = build_synergy_matrix(cards, "cmdr", Path("unused.db"))
    assert m.signals == {"rules": True, "embeddings": False}


def test_generation_meta_has_signal_fields() -> None:
    """GenerationMeta carries the signal lists (default empty)."""
    from sabermetrics.models.deck import GenerationMeta

    meta = GenerationMeta(
        generation_time_seconds=1.0, llm_cost_usd=0.0, source_profile_id="x",
        signals_used=["rules"], signals_unavailable=["embeddings"],
    )
    assert meta.signals_used == ["rules"]
    assert meta.signals_unavailable == ["embeddings"]
    assert GenerationMeta(
        generation_time_seconds=1.0, llm_cost_usd=0.0, source_profile_id="x",
    ).signals_used == []
