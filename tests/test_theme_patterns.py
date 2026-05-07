"""Tests for theme pattern detection engine."""

from sabermetrics.analytics.theme_patterns import (
    THEME_PATTERNS,
    classify_dominant_theme,
    compute_deck_theme_vector,
    count_theme_cards,
)


def _make_card(
    name: str, oracle_text: str, type_line: str = "Creature"
) -> dict:
    return {"name": name, "oracle_text": oracle_text, "type_line": type_line}


def test_count_theme_cards_sacrifice() -> None:
    """Detects sacrifice/dies patterns in oracle text."""
    cards = [
        _make_card("Viscera Seer", "Sacrifice a creature: Scry 1."),
        _make_card("Blood Artist", "Whenever Blood Artist or another creature dies, ..."),
        _make_card("Grizzly Bears", "Vanilla 2/2"),
    ]
    assert count_theme_cards(cards, "sacrifice") == 2


def test_count_theme_cards_skips_lands() -> None:
    """Lands are excluded from theme counting."""
    cards = [
        _make_card(
            "High Market",
            "Sacrifice a creature: You gain 1 life.",
            type_line="Land",
        ),
        _make_card("Viscera Seer", "Sacrifice a creature: Scry 1."),
    ]
    assert count_theme_cards(cards, "sacrifice") == 1


def test_compute_deck_theme_vector() -> None:
    """Returns a dict with all 15 themes as keys."""
    cards = [
        _make_card("Viscera Seer", "Sacrifice a creature: Scry 1."),
    ]
    vector = compute_deck_theme_vector(cards)
    assert len(vector) == len(THEME_PATTERNS)
    assert vector["sacrifice"] == 1
    assert vector["mill"] == 0


def test_classify_dominant_theme() -> None:
    """Picks highest theme above threshold."""
    vector = {
        "sacrifice": 8,
        "token_generation": 6,
        "mill": 2,
        "etb_triggers": 0,
    }
    assert classify_dominant_theme(vector, min_threshold=5) == "sacrifice"


def test_classify_dominant_theme_none_below_threshold() -> None:
    """Returns None when no theme meets the threshold."""
    vector = {
        "sacrifice": 3,
        "token_generation": 2,
        "mill": 1,
    }
    assert classify_dominant_theme(vector, min_threshold=5) is None
