"""Tests for oracle text keyword extraction and CVAR synergy integration."""

from sabermetrics.analytics.oracle_keywords import (
    MTG_KEYWORD_ABILITIES,
    card_matches_referenced_keywords,
    extract_referenced_keywords,
    extract_referenced_mechanics,
)
from sabermetrics.analytics.cvar import ScoringContext, compute_synergy_score


def _make_card(
    name: str,
    oracle_text: str = "",
    type_line: str = "Creature",
    keywords: list[str] | None = None,
    color_identity: list[str] | None = None,
) -> dict:
    return {
        "name": name,
        "oracle_text": oracle_text,
        "type_line": type_line,
        "keywords": keywords or [],
        "color_identity": color_identity or [],
    }


# ---------------------------------------------------------------------------
# Extraction tests
# ---------------------------------------------------------------------------

ARCADES_TEXT = (
    "Flying, vigilance\n"
    "Whenever a creature with defender enters the battlefield under your "
    "control, draw a card.\n"
    "Each creature you control with defender assigns combat damage equal "
    "to its toughness rather than its power and can attack as though it "
    "didn't have defender."
)

ISPERIA_TEXT = (
    "Flying\n"
    "Whenever a creature with flying deals combat damage to one of your "
    "opponents, you may reveal the top card of your library. If it's an "
    "instant or sorcery card, put it into your hand."
)


def test_extract_defender_from_arcades() -> None:
    """Arcades' 'creatures with defender' extracts defender."""
    result = extract_referenced_keywords(ARCADES_TEXT)
    assert "defender" in result


def test_extract_flying_from_isperia() -> None:
    """Isperia's 'creature with flying deals' extracts flying."""
    result = extract_referenced_keywords(ISPERIA_TEXT)
    assert "flying" in result


def test_extract_infect() -> None:
    """Text mentioning 'creatures with infect' extracts infect."""
    text = "Creatures you control with infect get +1/+1."
    result = extract_referenced_keywords(text)
    assert "infect" in result


def test_extract_no_false_positives() -> None:
    """'Sacrifice a permanent' should not extract sacrifice as a keyword."""
    text = "Sacrifice a permanent: Draw a card."
    result = extract_referenced_keywords(text)
    assert "sacrifice" not in result


def test_extract_multiple_keywords() -> None:
    """Text with multiple keyword references extracts all."""
    text = (
        "Creatures you control with flying get +1/+1. "
        "Creatures you control with vigilance have hexproof."
    )
    result = extract_referenced_keywords(text)
    assert "flying" in result
    assert "vigilance" in result


def test_extract_empty_oracle() -> None:
    """Empty or None oracle text returns empty list."""
    assert extract_referenced_keywords("") == []
    assert extract_referenced_keywords(None) == []


def test_extract_toughness_matters() -> None:
    """Arcades toughness text extracts toughness_matters mechanic."""
    result = extract_referenced_mechanics(ARCADES_TEXT)
    assert "toughness_matters" in result


def test_extract_artifact_creature() -> None:
    """'Artifact creature enters' extracts artifact_creature mechanic."""
    text = "Whenever an artifact creature enters the battlefield, draw a card."
    result = extract_referenced_mechanics(text)
    assert "artifact_creature" in result


def test_extract_mechanics_empty() -> None:
    """Empty text returns no mechanics."""
    assert extract_referenced_mechanics("") == []
    assert extract_referenced_mechanics(None) == []


# ---------------------------------------------------------------------------
# Card matching tests
# ---------------------------------------------------------------------------


def test_defender_card_matches() -> None:
    """Wall with Defender keyword matches referenced defender."""
    card = _make_card(
        "Wall of Omens",
        oracle_text="When Wall of Omens enters the battlefield, draw a card.",
        type_line="Creature — Wall",
        keywords=["Defender"],
    )
    assert card_matches_referenced_keywords(card, ["defender"], []) is True


def test_wall_type_matches_toughness_matters() -> None:
    """Wall creature matches toughness_matters mechanic."""
    card = _make_card(
        "Wall of Denial",
        type_line="Creature — Wall",
        keywords=["Defender", "Flying", "Shroud"],
    )
    assert card_matches_referenced_keywords(card, [], ["toughness_matters"]) is True


def test_artifact_creature_matches() -> None:
    """Artifact Creature type line matches artifact_creature mechanic."""
    card = _make_card(
        "Solemn Simulacrum",
        type_line="Artifact Creature — Golem",
    )
    assert card_matches_referenced_keywords(card, [], ["artifact_creature"]) is True


def test_unrelated_card_does_not_match() -> None:
    """Beast with Trample doesn't match defender/toughness_matters."""
    card = _make_card(
        "Charging Badger",
        type_line="Creature — Badger",
        keywords=["Trample"],
    )
    assert card_matches_referenced_keywords(card, ["defender"], ["toughness_matters"]) is False


def test_no_match_when_empty_refs() -> None:
    """No referenced keywords/mechanics always returns False."""
    card = _make_card("Sol Ring", type_line="Artifact", keywords=[])
    assert card_matches_referenced_keywords(card, [], []) is False


# ---------------------------------------------------------------------------
# CVAR integration tests
# ---------------------------------------------------------------------------


def _arcades_context() -> ScoringContext:
    return ScoringContext(
        commander_id="arcades-id",
        commander_name="Arcades, the Strategist",
        commander_colors=["W", "U", "G"],
        commander_keywords=["Flying", "Vigilance"],
        commander_oracle_text=ARCADES_TEXT,
        referenced_keywords=["defender"],
        referenced_mechanics=["toughness_matters"],
    )


def _generic_context() -> ScoringContext:
    return ScoringContext(
        commander_id="generic-id",
        commander_name="Generic Commander",
        commander_colors=["W", "U", "G"],
        commander_keywords=["Flying", "Vigilance"],
        commander_oracle_text="Flying, vigilance",
    )


def test_synergy_score_boost_for_defender() -> None:
    """Defender creature scores >= 0.3 higher with Arcades context."""
    card = _make_card(
        "Wall of Omens",
        oracle_text="When Wall of Omens enters the battlefield, draw a card.",
        type_line="Creature — Wall",
        keywords=["Defender"],
        color_identity=["W"],
    )
    arcades_score = compute_synergy_score(card, _arcades_context())
    generic_score = compute_synergy_score(card, _generic_context())
    assert arcades_score >= generic_score + 0.3


def test_synergy_score_no_regression_for_korvold() -> None:
    """Sacrifice outlet for Korvold scores same — empty referenced_keywords."""
    korvold_context = ScoringContext(
        commander_id="korvold-id",
        commander_name="Korvold, Fae-Cursed King",
        commander_colors=["B", "R", "G"],
        commander_keywords=["Flying"],
        commander_oracle_text=(
            "Flying\n"
            "Whenever Korvold, Fae-Cursed King enters the battlefield or "
            "attacks, sacrifice another permanent.\n"
            "Whenever you sacrifice a permanent, put a +1/+1 counter on "
            "Korvold and draw a card."
        ),
        # Empty — no keywords referenced, just "sacrifice" which is not a keyword
        referenced_keywords=[],
        referenced_mechanics=[],
    )
    card = _make_card(
        "Viscera Seer",
        oracle_text="Sacrifice a creature: Scry 1.",
        type_line="Creature — Vampire Wizard",
        keywords=[],
        color_identity=["B"],
    )

    # Also test without referenced fields at all (default)
    korvold_default = ScoringContext(
        commander_id="korvold-id",
        commander_name="Korvold, Fae-Cursed King",
        commander_colors=["B", "R", "G"],
        commander_keywords=["Flying"],
        commander_oracle_text=korvold_context.commander_oracle_text,
    )

    score_with = compute_synergy_score(card, korvold_context)
    score_default = compute_synergy_score(card, korvold_default)
    assert score_with == score_default
