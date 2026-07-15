"""Tests for pairwise synergy matrix (Step 2 of synergy optimizer)."""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

from sabermetrics.analytics.synergy_matrix import (
    EMBEDDING_WEIGHT,
    RULE_WEIGHT,
    _card_matches_clause,
    _load_synergy_rules,
    _match_rules,
    build_synergy_matrix,
)


def _make_card(
    card_id="card-1",
    name="Test Card",
    oracle_text="",
    type_line="Creature",
    keywords=None,
    cmc=3,
    role_tags=None,
    price=1.0,
    cvar_score=0.5,
) -> dict:
    """Build a minimal card dict for testing."""
    return {
        "id": card_id,
        "name": name,
        "oracle_text": oracle_text,
        "type_line": type_line,
        "keywords": keywords or "[]",
        "cmc": cmc,
        "role_tags": role_tags or '["utility"]',
        "price_usd": price,
        "_cvar_score": cvar_score,
    }


def _make_db(pairs=None):
    """Create a temp DB with card_cooccurrence table."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = Path(tmp.name)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE card_cooccurrence ("
        "card_a_id TEXT, card_b_id TEXT, commander_id TEXT, "
        "cooccurrence_count INTEGER, cooccurrence_rate REAL)"
    )
    if pairs:
        conn.executemany(
            "INSERT INTO card_cooccurrence VALUES (?, ?, ?, ?, ?)",
            pairs,
        )
    conn.commit()
    conn.close()
    return db_path


# --- Rule matching ---

def test_rule_matching_tokens_with_sacrifice() -> None:
    """Token generator + sac outlet should match tokens_with_sacrifice_payoff rule."""
    token_maker = _make_card(
        oracle_text="Create two 1/1 green Saproling creature tokens.",
    )
    sac_outlet = _make_card(
        oracle_text="Sacrifice a creature: Each opponent loses 1 life.",
    )
    rules = _load_synergy_rules()
    score = _match_rules(token_maker, sac_outlet, rules)
    assert score > 0, "Token + sacrifice should match a rule"
    assert score >= 0.7, f"Expected strength >= 0.7, got {score}"


def test_rule_matching_no_false_positive() -> None:
    """Unrelated cards should score 0 from rules."""
    ramp_card = _make_card(
        oracle_text="Search your library for a basic land card, put it onto the battlefield.",
    )
    flyer = _make_card(
        oracle_text="Flying. When this creature enters the battlefield, gain 3 life.",
        keywords='["Flying"]',
    )
    rules = _load_synergy_rules()
    score = _match_rules(ramp_card, flyer, rules)
    assert score == 0.0, f"Unrelated cards should get 0, got {score}"


def test_card_matches_clause_text_contains() -> None:
    """text_contains requires ALL terms to appear."""
    card = _make_card(oracle_text="Create a 1/1 white Soldier creature token")
    assert _card_matches_clause(card, {"text_contains": ["create", "token"]})
    assert not _card_matches_clause(card, {"text_contains": ["create", "sacrifice"]})


def test_card_matches_clause_keywords() -> None:
    """keywords requires ANY to match."""
    card = _make_card(keywords='["Flying", "Vigilance"]')
    assert _card_matches_clause(card, {"keywords": ["Flying"]})
    assert _card_matches_clause(card, {"keywords": ["Trample", "Flying"]})
    assert not _card_matches_clause(card, {"keywords": ["Haste"]})


# --- Co-occurrence signal removed (Option A criterion 3) ---

def test_cooccurrence_data_is_ignored() -> None:
    """Co-occurrence rows in the DB must NOT influence synergy.

    Proves the scoring path no longer reads card_cooccurrence: even with a
    strong co-occurrence rate for A-B, it scores the same as A-C.
    """
    card_a = _make_card(card_id="a", name="Card A")
    card_b = _make_card(card_id="b", name="Card B")
    card_c = _make_card(card_id="c", name="Card C")

    db_path = _make_db(pairs=[
        ("a", "b", "cmdr-1", 10, 0.8),
    ])

    with patch(
        "sabermetrics.analytics.synergy_matrix._compute_embedding_matrix"
    ) as mock_emb:
        mock_emb.return_value = np.zeros((3, 3), dtype=np.float32)
        matrix = build_synergy_matrix(
            [card_a, card_b, card_c], "cmdr-1", db_path,
        )

    # No rules match and embeddings are zeroed, so co-occurrence is the only
    # thing that could differ A-B from A-C. It is ignored, so both are 0.
    assert matrix.get_synergy("a", "b") == matrix.get_synergy("a", "c") == 0.0


# --- Embedding cross-role filtering ---

def test_embedding_same_role_filtered() -> None:
    """Two cards with the same primary role get 0 embedding contribution."""
    removal_a = _make_card(
        card_id="r1", name="Swords to Plowshares",
        oracle_text="Exile target creature",
        role_tags='["removal"]',
    )
    removal_b = _make_card(
        card_id="r2", name="Path to Exile",
        oracle_text="Exile target creature",
        role_tags='["removal"]',
    )
    utility = _make_card(
        card_id="u1", name="Utility Card",
        oracle_text="Draw a card",
        role_tags='["draw"]',
    )

    db_path = _make_db()

    # Use real embedding but we only check the cross-role filtering logic
    with patch(
        "sabermetrics.analytics.synergy_matrix._compute_embedding_matrix"
    ) as mock_emb:
        # Pretend all pairs have 0.5 similarity
        mock_emb.return_value = np.full((3, 3), 0.5, dtype=np.float32)
        np.fill_diagonal(mock_emb.return_value, 0.0)

        matrix = build_synergy_matrix(
            [removal_a, removal_b, utility], "cmdr-1", db_path,
        )

    # Same-role pair should have 0 embedding contribution
    # The matrix zeroes same-role pairs before hybrid combination,
    # so r1-r2 should have less than r1-u1
    r1_r2 = matrix.get_synergy("r1", "r2")
    r1_u1 = matrix.get_synergy("r1", "u1")
    # r1-r2: 0 embedding (same role), r1-u1: 0.25*0.5=0.125 embedding
    assert r1_u1 > r1_r2, f"Cross-role should > same-role: {r1_u1} vs {r1_r2}"


# --- Hybrid weights ---

def test_hybrid_weights_sum_correctly() -> None:
    """RULE + EMBEDDING weights should sum to 1.0 (co-occurrence removed)."""
    total = RULE_WEIGHT + EMBEDDING_WEIGHT
    assert abs(total - 1.0) < 0.001, f"Weights sum to {total}, expected 1.0"


# --- Matrix symmetry ---

def test_matrix_is_symmetric() -> None:
    """synergy(A,B) == synergy(B,A)."""
    card_a = _make_card(
        card_id="a", name="Card A",
        oracle_text="Create a token and sacrifice it",
    )
    card_b = _make_card(
        card_id="b", name="Card B",
        oracle_text="Whenever you sacrifice, draw a card",
    )
    db_path = _make_db()

    with patch(
        "sabermetrics.analytics.synergy_matrix._compute_embedding_matrix"
    ) as mock_emb:
        mock_emb.return_value = np.zeros((2, 2), dtype=np.float32)
        matrix = build_synergy_matrix([card_a, card_b], "cmdr-1", db_path)

    ab = matrix.get_synergy("a", "b")
    ba = matrix.get_synergy("b", "a")
    assert abs(ab - ba) < 0.001, f"Not symmetric: {ab} vs {ba}"


def test_empty_candidates() -> None:
    """Empty candidate list produces empty matrix."""
    db_path = _make_db()
    matrix = build_synergy_matrix([], "cmdr-1", db_path)
    assert matrix.matrix.shape == (0, 0)
