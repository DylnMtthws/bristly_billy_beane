"""Tests for alternative cost parsing and effective CMC calculation."""

from sabermetrics.analytics.effective_cost import (
    _parse_mana_cost_cmc,
    compute_effective_cmc,
    parse_alternative_costs,
)


# ---------------------------------------------------------------------------
# _parse_mana_cost_cmc tests
# ---------------------------------------------------------------------------


def test_parse_generic_and_colored() -> None:
    """'{2}{B}{B}' parses to CMC 4."""
    assert _parse_mana_cost_cmc("{2}{B}{B}") == 4.0


def test_parse_single_color() -> None:
    """'{R}' parses to CMC 1."""
    assert _parse_mana_cost_cmc("{R}") == 1.0


def test_parse_hybrid() -> None:
    """'{W/U}{W/U}' parses to CMC 2."""
    assert _parse_mana_cost_cmc("{W/U}{W/U}") == 2.0


def test_parse_x_cost() -> None:
    """'{X}{G}' parses to CMC 1 (X=0)."""
    assert _parse_mana_cost_cmc("{X}{G}") == 1.0


def test_parse_empty() -> None:
    """Empty string parses to CMC 0."""
    assert _parse_mana_cost_cmc("") == 0.0


# ---------------------------------------------------------------------------
# parse_alternative_costs tests
# ---------------------------------------------------------------------------


def test_morph_extracts_face_down() -> None:
    """Morph creature extracts face_down at CMC 3."""
    text = "Morph {4}{G}\nWhen this is turned face up, do something."
    costs = parse_alternative_costs(text)
    face_down = [c for c in costs if c["method"] == "face_down"]
    assert len(face_down) == 1
    assert face_down[0]["cmc"] == 3.0


def test_megamorph_extracts_face_down() -> None:
    """Megamorph extracts face_down at CMC 3."""
    text = "Megamorph {5}{G}{G}"
    costs = parse_alternative_costs(text)
    face_down = [c for c in costs if c["method"] == "face_down"]
    assert len(face_down) == 1
    assert face_down[0]["cmc"] == 3.0


def test_disguise_extracts_face_down() -> None:
    """Disguise extracts face_down at CMC 3."""
    text = "Disguise {2}{W}\nWard {2}"
    costs = parse_alternative_costs(text)
    face_down = [c for c in costs if c["method"] == "face_down"]
    assert len(face_down) == 1
    assert face_down[0]["cmc"] == 3.0


def test_evoke_extracts_cost() -> None:
    """Evoke {1}{B}{B} extracts evoke at CMC 3."""
    text = "Flying\nEvoke {1}{B}{B}"
    costs = parse_alternative_costs(text)
    evoke = [c for c in costs if c["method"] == "evoke"]
    assert len(evoke) == 1
    assert evoke[0]["cmc"] == 3.0


def test_dash_extracts_cost() -> None:
    """Dash {1}{R} extracts dash at CMC 2."""
    text = "Dash {1}{R}\nWhen this creature enters the battlefield, deal 2 damage."
    costs = parse_alternative_costs(text)
    dash = [c for c in costs if c["method"] == "dash"]
    assert len(dash) == 1
    assert dash[0]["cmc"] == 2.0


def test_madness_extracts_cost() -> None:
    """Madness {1}{B} extracts madness at CMC 2."""
    text = "Madness {1}{B}"
    costs = parse_alternative_costs(text)
    madness = [c for c in costs if c["method"] == "madness"]
    assert len(madness) == 1
    assert madness[0]["cmc"] == 2.0


def test_unearth_extracts_cost() -> None:
    """Unearth {2}{B} extracts unearth at CMC 3."""
    text = "Unearth {2}{B}"
    costs = parse_alternative_costs(text)
    unearth = [c for c in costs if c["method"] == "unearth"]
    assert len(unearth) == 1
    assert unearth[0]["cmc"] == 3.0


def test_no_alternative_costs() -> None:
    """Card without alternative costs returns empty list."""
    text = "Flying, vigilance\nWhen this enters the battlefield, draw a card."
    assert parse_alternative_costs(text) == []


def test_none_oracle_text() -> None:
    """None oracle text returns empty list."""
    assert parse_alternative_costs(None) == []


# ---------------------------------------------------------------------------
# compute_effective_cmc tests
# ---------------------------------------------------------------------------


def test_effective_cmc_morph_creature() -> None:
    """6-CMC morph creature has effective CMC 3."""
    card = {
        "cmc": 6,
        "oracle_text": "Morph {4}{G}\nWhen this is turned face up, destroy target artifact.",
    }
    assert compute_effective_cmc(card) == 3.0


def test_effective_cmc_returns_printed_when_no_alternatives() -> None:
    """Card without alternative costs returns printed CMC."""
    card = {
        "cmc": 5,
        "oracle_text": "Flying\nWhen this enters the battlefield, draw two cards.",
    }
    assert compute_effective_cmc(card) == 5.0


def test_effective_cmc_evoke_cheaper() -> None:
    """Card where evoke is cheaper than printed CMC uses evoke."""
    card = {
        "cmc": 5,
        "oracle_text": "Flying\nEvoke {1}{B}",
    }
    assert compute_effective_cmc(card) == 2.0


def test_effective_cmc_printed_cheaper_than_alt() -> None:
    """Card where printed CMC is lower than alternative uses printed."""
    card = {
        "cmc": 2,
        "oracle_text": "Morph {3}{G}{G}\nSome ability.",
    }
    assert compute_effective_cmc(card) == 2.0


def test_effective_cmc_missing_oracle() -> None:
    """Card with no oracle text returns printed CMC."""
    card = {"cmc": 4}
    assert compute_effective_cmc(card) == 4.0


def test_effective_cmc_zero_printed() -> None:
    """Card with CMC 0 returns 0."""
    card = {"cmc": 0, "oracle_text": "Something."}
    assert compute_effective_cmc(card) == 0.0
