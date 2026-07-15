"""Deck-legality invariant (Option A DoD criterion 2).

The invariant is enforced by DeckBuilder._enforce_legality. These unit tests
exercise every repair path directly (no API key, DB, or cost), which verifies
the invariant for arbitrary inputs. A live end-to-end build across several
commanders is in test_deck_legality_integration (skipped without a key).
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from sabermetrics.pipeline.deck_builder import (
    DeckBuilder,
    _BASIC_LAND_NAMES,
    _make_basic_lands,
)
from sabermetrics.pipeline.slot_assigner import SlotAssignment
from sabermetrics.pipeline.trace import GenerationTracer


def _mk(name, score=0.5, role="utility", ci=None, type_line="Creature", cid=None):
    return SlotAssignment(
        card={
            "name": name,
            "id": cid or f"id-{name}",
            "color_identity": ci if ci is not None else [],
            "type_line": type_line,
            "price_usd": 1.0,
        },
        slot_role=role,
        score=score,
    )


def _builder():
    b = DeckBuilder(Path("data/sabermetrics.db"))
    b._tracer = GenerationTracer(generation_id="test", watchlist=set())
    return b


def _commander(name="Test Commander", colors=("W", "U")):
    return SimpleNamespace(name=name, color_identity=list(colors))


def _assert_legal(deck, commander):
    assert len(deck) == 99, f"expected 99, got {len(deck)}"
    ci = set(commander.color_identity)
    seen: set[str] = set()
    for a in deck:
        name = a.card.get("name", "")
        assert name != commander.name, "commander leaked into the 99"
        if name in _BASIC_LAND_NAMES:
            continue  # basics: dup-exempt, empty identity
        assert name not in seen, f"duplicate nonbasic: {name}"
        seen.add(name)
        card_ci = a.card.get("color_identity") or []
        assert set(card_ci) <= ci, f"{name} out of color identity {card_ci}"


def test_short_deck_filled_to_99() -> None:
    cmd = _commander(colors=("W", "U"))
    deck = [_mk(f"Card{i}", ci=["W"]) for i in range(40)]
    out = _builder()._enforce_legality(deck, cmd)
    _assert_legal(out, cmd)
    basics = [a for a in out if a.card["name"] in _BASIC_LAND_NAMES]
    assert len(basics) == 59
    # fill basics are only Plains/Island for a WU commander
    assert {a.card["name"] for a in basics} <= {"Plains", "Island"}


def test_overfull_deck_trimmed_to_99_basics_first() -> None:
    cmd = _commander(colors=("G",))
    deck = [_mk(f"Spell{i}", score=0.9, ci=["G"]) for i in range(99)]
    deck += _make_basic_lands(20, ["G"])  # 119 total, 20 basics
    out = _builder()._enforce_legality(deck, cmd)
    _assert_legal(out, cmd)
    # basics are the most-removable, so all 20 basics get trimmed first
    assert all(a.card["name"] not in _BASIC_LAND_NAMES for a in out)


def test_protected_cards_survive_trim() -> None:
    cmd = _commander(colors=("R",))
    weak_protected = [_mk(f"Staple{i}", score=0.01, ci=["R"]) for i in range(5)]
    filler = [_mk(f"Filler{i}", score=0.5, ci=["R"]) for i in range(110)]
    protected = {a.card["name"] for a in weak_protected}
    out = _builder()._enforce_legality(
        weak_protected + filler, cmd, protected_names=protected
    )
    _assert_legal(out, cmd)
    names = {a.card["name"] for a in out}
    assert protected <= names, "protected staples were trimmed"


def test_duplicate_nonbasics_collapsed_keeping_best() -> None:
    cmd = _commander(colors=("B",))
    deck = [_mk("Dup", score=0.2, ci=["B"], cid="lo"),
            _mk("Dup", score=0.9, ci=["B"], cid="hi")]
    deck += [_mk(f"X{i}", ci=["B"]) for i in range(100)]
    out = _builder()._enforce_legality(deck, cmd)
    _assert_legal(out, cmd)
    dups = [a for a in out if a.card["name"] == "Dup"]
    assert len(dups) == 1 and dups[0].card["id"] == "hi"


def test_out_of_identity_and_commander_dropped() -> None:
    cmd = _commander(name="Cmdr", colors=("W",))
    deck = [_mk("Cmdr", ci=["W"])]                       # commander itself
    deck += [_mk("OffColor", ci=["B"])]                  # out of identity
    deck += [_mk(f"OK{i}", ci=["W"]) for i in range(50)]
    out = _builder()._enforce_legality(deck, cmd)
    _assert_legal(out, cmd)
    names = {a.card["name"] for a in out}
    assert "Cmdr" not in names and "OffColor" not in names


def test_colorless_commander_fills_wastes() -> None:
    cmd = _commander(name="Kozilek", colors=())
    deck = [_mk(f"Artifact{i}", ci=[], type_line="Artifact") for i in range(30)]
    out = _builder()._enforce_legality(deck, cmd)
    _assert_legal(out, cmd)
    assert any(a.card["name"] == "Wastes" for a in out)


def test_make_basic_lands_distribution() -> None:
    lands = _make_basic_lands(10, ["W", "U"])
    assert len(lands) == 10
    names = {a.card["name"] for a in lands}
    assert names == {"Plains", "Island"}
    assert all(a.slot_role == "land" for a in lands)


# --- Live end-to-end legality across commanders (skipped without a key) ---

HAS_API_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))
HAS_DB = Path("data/sabermetrics.db").exists()


@pytest.mark.skipif(
    not (HAS_API_KEY and HAS_DB),
    reason="needs ANTHROPIC_API_KEY + populated DB (and cost headroom)",
)
def test_deck_legality_integration() -> None:
    """Build real decks for several commanders/budgets; assert each is legal."""
    import sqlite3

    from sabermetrics.pipeline.deck_builder import DeckBuildRequest

    db = Path("data/sabermetrics.db")
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT id FROM cards WHERE is_legal_commander = 1 "
        "AND id IN (SELECT commander_id FROM commander_profiles) LIMIT 3"
    ).fetchall()
    conn.close()
    commander_ids = [r[0] for r in rows]
    assert commander_ids, "no cached-profile commanders to test"

    builder = DeckBuilder(db)
    for cid in commander_ids:
        for budget in (50.0, 400.0):
            result = builder.build(
                DeckBuildRequest(commander_id=cid, budget_usd=budget)
            )
            cards = result.deck.cards
            assert len(cards) == 99, f"{cid}@{budget}: {len(cards)} cards"
            ci = set(result.deck.commander.color_identity)
            seen: set[str] = set()
            for dc in cards:
                nm = dc.card.name
                if nm in _BASIC_LAND_NAMES:
                    continue
                assert nm not in seen, f"dup {nm}"
                seen.add(nm)
                assert set(dc.card.color_identity) <= ci
