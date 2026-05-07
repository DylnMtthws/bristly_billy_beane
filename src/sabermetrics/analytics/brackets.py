"""Bracket classifier for WotC 5-tier framework (D4.7).

Classifies a deck's power level based on:
- Game changers present
- Fast mana density
- Tutor density
- Combo potential
- Average CMC
"""

import json
import logging
import sqlite3
from pathlib import Path

import yaml

from sabermetrics.analytics.components import (
    count_board_wipes,
    count_removal,
    count_ramp_spells,
    count_tutors,
)

logger = logging.getLogger(__name__)


class BracketResult:
    """Result of bracket classification."""

    def __init__(self, bracket: int, reasoning: list[str], signals: dict) -> None:
        self.bracket = bracket
        self.reasoning = reasoning
        self.signals = signals

    def __repr__(self) -> str:
        return f"BracketResult(bracket={self.bracket}, signals={self.signals})"


def _load_game_changers(config_dir: Path | None = None) -> dict[str, int]:
    """Load game changer cards with their bracket thresholds.

    Returns:
        Dict mapping card_name (lowered) to bracket_threshold.
    """
    if config_dir is None:
        config_dir = Path(__file__).resolve().parent.parent.parent.parent / "config"

    gc_path = config_dir / "game_changers.yaml"
    if not gc_path.exists():
        return {}

    with open(gc_path) as f:
        data = yaml.safe_load(f) or {}

    return {
        gc["card_name"].lower(): gc.get("bracket_threshold", 4)
        for gc in data.get("game_changers", [])
    }


def _detect_combos(
    cards: list[dict], db_path: Path | None = None
) -> list[dict]:
    """Check if any known combos are present in the card list.

    Args:
        cards: Deck card dicts.
        db_path: Path to database with combos table.

    Returns:
        List of combo dicts found in the deck.
    """
    if db_path is None:
        return []

    card_names = {(c.get("name") or "").lower() for c in cards}

    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute("SELECT id, cards, description, result FROM combos")
        found = []
        for row in cursor:
            combo_cards_json = row[1]
            combo_cards = json.loads(combo_cards_json) if isinstance(combo_cards_json, str) else combo_cards_json
            combo_names = {c.lower() for c in combo_cards}
            if combo_names <= card_names:
                found.append({
                    "id": row[0],
                    "cards": combo_cards,
                    "description": row[2],
                    "result": row[3],
                })
        return found
    finally:
        conn.close()


def classify_bracket(
    cards: list[dict],
    db_path: Path | None = None,
    config_dir: Path | None = None,
) -> BracketResult:
    """Classify a deck into WotC bracket 1-5.

    Bracket guidelines:
    - 1: Precon-level, no optimization
    - 2: Focused but casual, some upgrades
    - 3: Optimized casual, efficient mana/synergies
    - 4: High power, tutors + combos + fast mana
    - 5: cEDH, full optimization

    Args:
        cards: All cards in the deck (including commander).
        db_path: Optional database path for combo lookup.
        config_dir: Optional config directory for game changers.

    Returns:
        BracketResult with bracket, reasoning, and signal breakdown.
    """
    game_changers = _load_game_changers(config_dir)
    reasoning: list[str] = []
    signals: dict[str, float] = {}

    # Non-land cards for analysis
    non_lands = [
        c for c in cards
        if "land" not in (c.get("type_line") or "").lower()
    ]

    # Signal 1: Game changers present
    gc_found = []
    max_gc_bracket = 0
    for card in cards:
        name = (card.get("name") or "").lower()
        if name in game_changers:
            gc_found.append(name)
            max_gc_bracket = max(max_gc_bracket, game_changers[name])

    signals["game_changers_count"] = len(gc_found)
    signals["max_game_changer_bracket"] = max_gc_bracket
    if gc_found:
        reasoning.append(
            f"Game changers present ({len(gc_found)}): "
            + ", ".join(gc_found[:5])
        )

    # Signal 2: Fast mana
    fast_mana_names = {
        "sol ring", "mana crypt", "mana vault", "chrome mox",
        "mox diamond", "mox opal", "lotus petal", "jeweled lotus",
        "dark ritual", "cabal ritual", "simian spirit guide",
        "elvish spirit guide",
    }
    fast_mana = sum(
        1 for c in cards
        if (c.get("name") or "").lower() in fast_mana_names
    )
    signals["fast_mana"] = fast_mana
    if fast_mana > 0:
        reasoning.append(f"Fast mana sources: {fast_mana}")

    # Signal 3: Tutor density
    tutors = count_tutors(non_lands)
    signals["tutors"] = tutors
    if tutors > 0:
        reasoning.append(f"Tutors: {tutors}")

    # Signal 4: Combo potential
    combos = _detect_combos(cards, db_path)
    signals["combos"] = len(combos)
    if combos:
        reasoning.append(
            f"Known combos detected: {len(combos)} "
            f"({', '.join(c['description'][:50] for c in combos[:3])})"
        )

    # Signal 5: Interaction density
    removal = count_removal(non_lands)
    wipes = count_board_wipes(non_lands)
    signals["removal"] = removal
    signals["board_wipes"] = wipes

    # Signal 6: Average CMC (lower = more optimized)
    cmcs = [float(c.get("cmc", 0)) for c in non_lands if c.get("cmc")]
    avg_cmc = sum(cmcs) / len(cmcs) if cmcs else 3.5
    signals["avg_cmc"] = round(avg_cmc, 2)

    # Signal 7: Ramp count
    ramp = count_ramp_spells(non_lands)
    signals["ramp"] = ramp

    # --- Bracket determination ---
    bracket = 2  # Default: focused casual

    # Bracket 5: cEDH indicators
    if (fast_mana >= 4 and tutors >= 5 and len(combos) >= 2 and avg_cmc < 2.5):
        bracket = 5
        reasoning.append("cEDH indicators: heavy fast mana + tutors + combos + low curve")
    # Bracket 4: High power
    elif (max_gc_bracket >= 4 or (fast_mana >= 2 and tutors >= 3) or
          (len(combos) >= 2 and tutors >= 2)):
        bracket = 4
        reasoning.append("High power: game changers and/or significant fast mana + tutors")
    # Bracket 3: Optimized casual
    elif (ramp >= 10 and removal >= 5 and (tutors >= 1 or fast_mana >= 1)):
        bracket = 3
        reasoning.append("Optimized casual: good ramp/removal suite with some optimization")
    # Bracket 1: Precon-like
    elif (fast_mana == 0 and tutors == 0 and len(combos) == 0 and
          avg_cmc >= 3.5 and ramp < 8):
        bracket = 1
        reasoning.append("Precon-level: no optimization, high curve, minimal ramp")
    else:
        reasoning.append("Focused casual: some upgrades but limited power cards")

    return BracketResult(
        bracket=bracket,
        reasoning=reasoning,
        signals=signals,
    )
