"""Tests for commander exclusion from the 99 (all printings).

The commander was excluded from the candidate pool by printing id only, but
the pool dedupes each name to its cheapest printing -- so a commander with
multiple printings (Eriette has 5) could survive as a different printing and
be placed in its own 99. That violates the format's core rule.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from sabermetrics.analytics.filters import apply_hard_filters
from sabermetrics.errors import FatalError
from sabermetrics.models.card import Card
from sabermetrics.pipeline.deck_builder import DeckBuilder
from sabermetrics.pipeline.slot_assigner import SlotAssignment


def _commander(oracle_id="oracle-eriette", name="Eriette of the Charmed Apple"):
    return Card(
        id="printing-1", oracle_id=oracle_id, name=name, cmc=3.0,
        type_line="Legendary Creature — Human Warlock",
        color_identity=["B", "W"], is_legal_commander=True,
        is_legal_in_99=True, set_code="woe", rarity="rare",
        last_updated=datetime(2026, 1, 1),
    )


def _assignment(card: dict) -> SlotAssignment:
    return SlotAssignment(card=card, slot_role="utility", score=0.5)


def test_validator_rejects_other_printing_of_commander():
    """A different printing (different id, same oracle_id) must hard-fail."""
    leaked = {"id": "printing-2", "oracle_id": "oracle-eriette",
              "name": "Eriette of the Charmed Apple"}
    with pytest.raises(FatalError, match="leaked into the 99"):
        DeckBuilder._validate_no_commander_in_99(
            [_assignment(leaked)], _commander()
        )


def test_validator_rejects_by_name_when_oracle_id_missing():
    """Cards without an oracle_id still can't share the commander's name."""
    leaked = {"id": "printing-2", "name": "Eriette of the Charmed Apple"}
    with pytest.raises(FatalError, match="leaked into the 99"):
        DeckBuilder._validate_no_commander_in_99(
            [_assignment(leaked)], _commander()
        )


def test_validator_passes_a_clean_deck():
    clean = {"id": "x", "oracle_id": "oracle-other", "name": "Kor Spiritdancer"}
    DeckBuilder._validate_no_commander_in_99([_assignment(clean)], _commander())


def test_hard_filters_exclude_every_printing_of_the_commander():
    """Integration: the pool contains no printing of the commander.

    Eriette is the reproduction case -- 5 printings sharing one oracle_id,
    where the commander's own printing has no price so the name-dedupe kept a
    cheaper one.
    """
    db_path = Path("data/sabermetrics.db")
    if not db_path.exists():
        pytest.skip("no local DB")

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT id, oracle_id, name FROM cards "
        "WHERE name = 'Eriette of the Charmed Apple' "
        "AND is_legal_commander = 1 LIMIT 1"
    ).fetchone()
    conn.close()
    if row is None:
        pytest.skip("Eriette not in DB")

    commander_id, oracle_id, name = row
    candidates = apply_hard_filters(db_path, commander_id, max_budget_usd=200.0)

    leaks = [
        c for c in candidates
        if c.get("oracle_id") == oracle_id or c.get("name") == name
    ]
    assert leaks == [], f"commander printings leaked into pool: {leaks}"
