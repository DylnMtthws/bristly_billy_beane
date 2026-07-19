"""Tests for mana base land parsing and scoring."""


from sabermetrics.pipeline.mana_base import (
    LandInfo,
    _score_land,
    build_mana_base,
    parse_land_colors,
)


# --- Restricted mana detection ---


def test_restricted_mana_detected():
    """Corrupted Crossroads should be flagged as restricted mana."""
    info = parse_land_colors(
        oracle_text="{T}: Add {C}.\n{T}: Add one mana of any color. Spend this mana only to cast a creature spell of a type that has devoid.",
        type_line="Land",
    )
    assert info.has_mana_restriction is True


def test_restricted_mana_seedcore():
    """The Seedcore should be flagged as restricted mana."""
    info = parse_land_colors(
        oracle_text="{T}: Add {C}.\n{T}: Add one mana of any color. Spend this mana only on legendary spells.",
        type_line="Land",
    )
    assert info.has_mana_restriction is True


def test_unrestricted_mana_not_flagged():
    """Command Tower (no restriction) should not be flagged."""
    info = parse_land_colors(
        oracle_text="{T}: Add one mana of any color in your commander's color identity.",
        type_line="Land",
    )
    assert info.has_mana_restriction is False


def test_restricted_mana_heavily_penalized():
    """A restricted land should score far below an equivalent unrestricted land."""
    commander_colors = ["W", "U", "B"]
    deficit = {"W": 10.0, "U": 10.0, "B": 10.0}

    # Unrestricted 5-color land
    unrestricted = LandInfo(
        card={"oracle_text": ""},
        colors_produced=["B", "U", "W"],
    )
    score_unrestricted = _score_land(unrestricted, deficit, commander_colors)

    # Restricted land with same colors
    restricted = LandInfo(
        card={"oracle_text": "Spend this mana only to cast creature spells."},
        colors_produced=["B", "U", "W"],
        has_mana_restriction=True,
    )
    score_restricted = _score_land(restricted, deficit, commander_colors)

    assert score_restricted < score_unrestricted * 0.3


# --- Conditional tapped land detection ---


def test_checkland_detected():
    """Glacial Fortress should be detected as conditional tapped with check types."""
    info = parse_land_colors(
        oracle_text="Glacial Fortress enters the battlefield tapped unless you control a Plains or an Island.\n{T}: Add {W} or {U}.",
        type_line="Land",
    )
    assert info.is_conditional_tapped is True
    assert info.enters_tapped is False
    assert "Plains" in info.check_basic_types
    assert "Island" in info.check_basic_types


def test_fastland_detected():
    """Seachrome Coast should be detected as a fastland."""
    info = parse_land_colors(
        oracle_text="Seachrome Coast enters the battlefield tapped unless you control two or fewer other lands.\n{T}: Add {W} or {U}.",
        type_line="Land",
    )
    assert info.is_fastland is True
    assert info.is_conditional_tapped is True
    assert info.enters_tapped is False


def test_conditional_tapped_not_marked_always_tapped():
    """Checklands and fastlands should NOT have enters_tapped=True."""
    # Checkland
    check = parse_land_colors(
        oracle_text="Hinterland Harbor enters the battlefield tapped unless you control a Forest or an Island.\n{T}: Add {G} or {U}.",
        type_line="Land",
    )
    assert check.enters_tapped is False
    assert check.is_conditional_tapped is True

    # Fastland
    fast = parse_land_colors(
        oracle_text="Botanical Sanctum enters the battlefield tapped unless you control two or fewer other lands.\n{T}: Add {G} or {U}.",
        type_line="Land",
    )
    assert fast.enters_tapped is False
    assert fast.is_conditional_tapped is True


def test_always_tapped_still_detected():
    """A land with no condition should still be marked enters_tapped=True."""
    info = parse_land_colors(
        oracle_text="Azorius Guildgate enters the battlefield tapped.\n{T}: Add {W} or {U}.",
        type_line="Land — Gate",
    )
    assert info.enters_tapped is True
    assert info.is_conditional_tapped is False


# --- Additive ETB tapped penalty ---


def test_always_tapped_penalty_is_additive():
    """Tapped land with deficit=10 should lose significant absolute score vs untapped."""
    commander_colors = ["W", "U"]
    deficit = {"W": 10.0, "U": 10.0}

    # Untapped dual
    untapped = LandInfo(
        card={"oracle_text": "{T}: Add {W} or {U}."},
        colors_produced=["U", "W"],
    )
    score_untapped = _score_land(untapped, deficit, commander_colors)

    # Always-tapped dual
    tapped = LandInfo(
        card={"oracle_text": "Enters the battlefield tapped.\n{T}: Add {W} or {U}."},
        colors_produced=["U", "W"],
        enters_tapped=True,
    )
    score_tapped = _score_land(tapped, deficit, commander_colors)

    # Additive penalty should be at least 2.0 points
    assert score_untapped - score_tapped >= 2.0
    # The old multiplicative 0.8 would have given 24*0.8=19.2, only 4.8 less
    # The new additive should give at least 2.0 less regardless of deficit magnitude
    assert score_tapped < score_untapped


def test_tapped_penalty_scales_with_avg_cmc():
    """Low avg_cmc decks should penalize tapped lands more heavily."""
    commander_colors = ["W", "U"]
    deficit = {"W": 5.0, "U": 5.0}

    tapped = LandInfo(
        card={"oracle_text": ""},
        colors_produced=["U", "W"],
        enters_tapped=True,
    )

    score_aggro = _score_land(tapped, deficit, commander_colors, avg_cmc=2.0)
    score_midrange = _score_land(tapped, deficit, commander_colors, avg_cmc=3.0)
    score_control = _score_land(tapped, deficit, commander_colors, avg_cmc=4.0)

    # Aggro should penalize more than control
    assert score_aggro < score_midrange < score_control


# --- Minimum basics ---


def test_min_basics_3_color():
    """3-color commander should get 10 minimum basics."""
    result = build_mana_base(
        land_candidates=[],
        spells=[],
        commander_colors=["W", "U", "G"],
        total_lands=36,
    )
    basics = [a for a in result if a.card.get("rarity") == "common" and "Basic" in (a.card.get("type_line") or "")]
    assert len(basics) >= 10


def test_min_basics_5_color():
    """5-color commander should get 5 minimum basics."""
    result = build_mana_base(
        land_candidates=[],
        spells=[],
        commander_colors=["W", "U", "B", "R", "G"],
        total_lands=36,
    )
    basics = [a for a in result if a.card.get("rarity") == "common" and "Basic" in (a.card.get("type_line") or "")]
    assert len(basics) >= 5


def test_min_basics_1_color():
    """1-color commander should get 10 minimum basics."""
    result = build_mana_base(
        land_candidates=[],
        spells=[],
        commander_colors=["R"],
        total_lands=36,
    )
    basics = [a for a in result if a.card.get("rarity") == "common" and "Basic" in (a.card.get("type_line") or "")]
    assert len(basics) >= 10


# --- Checkland bonus ---


def test_checkland_gets_bonus():
    """Checkland should score higher than a comparable always-tapped dual."""
    commander_colors = ["W", "U"]
    deficit = {"W": 10.0, "U": 10.0}

    checkland = LandInfo(
        card={"oracle_text": "Glacial Fortress enters tapped unless you control a Plains or an Island.\n{T}: Add {W} or {U}."},
        colors_produced=["U", "W"],
        is_conditional_tapped=True,
        check_basic_types=["Plains", "Island"],
    )
    score_check = _score_land(checkland, deficit, commander_colors)

    tapped_dual = LandInfo(
        card={"oracle_text": "Enters tapped.\n{T}: Add {W} or {U}."},
        colors_produced=["U", "W"],
        enters_tapped=True,
    )
    score_tapped = _score_land(tapped_dual, deficit, commander_colors)

    assert score_check > score_tapped


# --- Corpus signal + drawback penalties (6-commander sweep fix #1) ---


def _land(oracle="", colors=None, **card_extra):
    card = {"oracle_text": oracle}
    card.update(card_extra)
    return LandInfo(card=card, colors_produced=colors or ["W", "B"])


def test_corpus_staple_land_outranks_equal_trap_land():
    """The sweep failure: same color coverage, but the corpus staple wins.

    Battlefield Forge (in most real decks) must beat Tarnished Citadel
    (0% inclusion, 3 self-damage) -- previously both scored on coverage
    alone and the trap land tied or won via the any-color multiplier.
    """
    colors = ["W", "B"]
    deficit = {"W": 8.0, "B": 8.0}
    staple = _land("{T}: Add {W} or {B}. This land deals 1 damage to you.",
                   _empirical_inclusion=0.75)
    trap = _land("{T}: Add one mana of any color. This land deals 3 damage to you.",
                 _empirical_inclusion=0.0)
    assert _score_land(staple, deficit, colors) > _score_land(trap, deficit, colors)


def test_drawback_penalties_rank_below_clean_duals():
    """Each trap-land drawback class scores below an equivalent clean dual."""
    colors = ["W", "B"]
    deficit = {"W": 8.0, "B": 8.0}
    clean = _score_land(_land("{T}: Add {W} or {B}."), deficit, colors)
    drawbacks = {
        "self-sac": "At the beginning of your upkeep, sacrifice it unless you control an artifact.",
        "untap-tax": "This land doesn't untap during your untap step.",
        "depletion": "{T}, Remove a charge counter from it: Add one mana of any color.",
        "multi-pain": "This land deals 3 damage to you.",
        "bounce": "At the beginning of your upkeep, return it to its owner's hand.",
        "opponent-choice": "When it enters, an opponent chooses a color.",
    }
    for label, oracle in drawbacks.items():
        trapped = _score_land(_land(f"{{T}}: Add {{W}} or {{B}}. {oracle}"), deficit, colors)
        assert trapped < clean, f"{label} drawback not penalized"


def test_empirical_bonus_neutral_without_corpus():
    """No corpus data -> exactly the old score (absence-neutrality)."""
    colors = ["W", "B"]
    deficit = {"W": 8.0, "B": 8.0}
    a = _score_land(_land("{T}: Add {W} or {B}."), deficit, colors)
    b = _score_land(_land("{T}: Add {W} or {B}.", _empirical_inclusion=0.0), deficit, colors)
    assert a == b
