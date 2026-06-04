"""Golden snapshot tests for the ramp/removal/protection detectors.

These are characterization tests: they freeze the exact output of
``detect_ramp_card`` / ``detect_removal_card`` / ``detect_protection_card``
over a fixed corpus of representative cards. The expected values live in
``tests/fixtures/detector_snapshots.json`` and were generated from the
detector code at the time the snapshot was taken.

Purpose: provide a behavior-preserving safety net before the detector
consolidation refactor (refactor plan #2). The parameterized engine must
reproduce these outputs byte-for-byte.

To regenerate the golden file after an *intentional* detector change::

    python -m tests.test_detector_snapshots --regenerate

Do not regenerate to make a failing test pass unless the behavior change is
deliberate and reviewed.
"""

from __future__ import annotations

import json
from pathlib import Path

from sabermetrics.analytics.protection_detector import detect_protection_card
from sabermetrics.analytics.ramp_detector import detect_ramp_card
from sabermetrics.analytics.removal_detector import detect_removal_card

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "detector_snapshots.json"

_DETECTORS = {
    "ramp": detect_ramp_card,
    "removal": detect_removal_card,
    "protection": detect_protection_card,
}

# --- Curated corpus -------------------------------------------------------
#
# Each entry has a stable ``key``, the ``detector`` to run it through, and the
# card fields the detectors read (oracle_text, type_line, cmc). The corpus
# deliberately spans every classification branch plus negative-pattern and
# non-matching cases for each detector.

CORPUS: list[dict] = [
    # ---- Ramp ----
    {
        "key": "ramp/sol_ring",
        "detector": "ramp",
        "oracle_text": "{T}: Add {C}{C}.",
        "type_line": "Artifact",
        "cmc": 1.0,
    },
    {
        "key": "ramp/llanowar_elves",
        "detector": "ramp",
        "oracle_text": "{T}: Add {G}.",
        "type_line": "Creature — Elf Druid",
        "cmc": 1.0,
    },
    {
        "key": "ramp/arcane_signet",
        "detector": "ramp",
        "oracle_text": "{T}: Add one mana of any color in your commander's color identity.",
        "type_line": "Artifact",
        "cmc": 2.0,
    },
    {
        "key": "ramp/rampant_growth",
        "detector": "ramp",
        "oracle_text": (
            "Search your library for a basic land card, put it onto the "
            "battlefield tapped, then shuffle."
        ),
        "type_line": "Sorcery",
        "cmc": 2.0,
    },
    {
        "key": "ramp/cultivate",
        "detector": "ramp",
        "oracle_text": (
            "Search your library for up to two basic land cards, reveal those "
            "cards, put one onto the battlefield tapped and the other into "
            "your hand, then shuffle."
        ),
        "type_line": "Sorcery",
        "cmc": 3.0,
    },
    {
        "key": "ramp/dark_ritual",
        "detector": "ramp",
        "oracle_text": "Add {B}{B}{B}.",
        "type_line": "Instant",
        "cmc": 1.0,
    },
    {
        "key": "ramp/smothering_tithe",
        "detector": "ramp",
        "oracle_text": (
            "Whenever an opponent draws a card, that player may pay {2}. If the "
            "player doesn't, you create a Treasure token."
        ),
        "type_line": "Enchantment",
        "cmc": 4.0,
    },
    {
        "key": "ramp/conditional_dork",
        "detector": "ramp",
        "oracle_text": "If you control an artifact, {T}: Add {U}.",
        "type_line": "Creature — Vedalken",
        "cmc": 2.0,
    },
    {
        "key": "ramp/generic_rock",
        "detector": "ramp",
        "oracle_text": "{T}: Add {2}.",
        "type_line": "Artifact",
        "cmc": 3.0,
    },
    {
        "key": "ramp/enchantment_mana",
        "detector": "ramp",
        "oracle_text": "{T}: Add {G}{G}.",
        "type_line": "Enchantment",
        "cmc": 3.0,
    },
    {
        "key": "ramp/other_any_color",
        "detector": "ramp",
        "oracle_text": "Add one mana of any color.",
        "type_line": "Tribal Instant — Elf",
        "cmc": 1.0,
    },
    {
        "key": "ramp/neg_opponent_treasure",
        "detector": "ramp",
        "oracle_text": (
            "When this creature enters, each opponent creates a Treasure token."
        ),
        "type_line": "Creature — Pirate",
        "cmc": 3.0,
    },
    {
        "key": "ramp/neg_spend_only",
        "detector": "ramp",
        "oracle_text": "{T}: Add {R}. Spend this mana only to cast creature spells.",
        "type_line": "Artifact",
        "cmc": 2.0,
    },
    {
        "key": "ramp/nonmatch_bolt",
        "detector": "ramp",
        "oracle_text": "Lightning Bolt deals 3 damage to any target.",
        "type_line": "Instant",
        "cmc": 1.0,
    },
    # ---- Removal ----
    {
        "key": "removal/swords_to_plowshares",
        "detector": "removal",
        "oracle_text": (
            "Exile target creature. Its controller gains life equal to its power."
        ),
        "type_line": "Instant",
        "cmc": 1.0,
    },
    {
        "key": "removal/murder",
        "detector": "removal",
        "oracle_text": "Destroy target creature.",
        "type_line": "Instant",
        "cmc": 3.0,
    },
    {
        "key": "removal/beast_within",
        "detector": "removal",
        "oracle_text": (
            "Destroy target permanent. Its controller creates a 3/3 green "
            "Beast creature token."
        ),
        "type_line": "Instant",
        "cmc": 3.0,
    },
    {
        "key": "removal/wrath_of_god",
        "detector": "removal",
        "oracle_text": "Destroy all creatures. They can't be regenerated.",
        "type_line": "Sorcery",
        "cmc": 4.0,
    },
    {
        "key": "removal/damnation",
        "detector": "removal",
        "oracle_text": "Destroy all creatures. They can't be regenerated.",
        "type_line": "Sorcery",
        "cmc": 4.0,
    },
    {
        "key": "removal/counterspell",
        "detector": "removal",
        "oracle_text": "Counter target spell.",
        "type_line": "Instant",
        "cmc": 2.0,
    },
    {
        "key": "removal/lightning_bolt",
        "detector": "removal",
        "oracle_text": "Lightning Bolt deals 3 damage to any target.",
        "type_line": "Instant",
        "cmc": 1.0,
    },
    {
        "key": "removal/pongify",
        "detector": "removal",
        "oracle_text": (
            "Destroy target creature. It can't be regenerated. Its controller "
            "creates a 3/3 green Ape creature token."
        ),
        "type_line": "Instant",
        "cmc": 1.0,
    },
    {
        "key": "removal/cyclonic_rift",
        "detector": "removal",
        "oracle_text": (
            "Return target nonland permanent you don't control to its owner's "
            "hand."
        ),
        "type_line": "Instant",
        "cmc": 2.0,
    },
    {
        "key": "removal/path_to_exile",
        "detector": "removal",
        "oracle_text": (
            "Exile target creature. Its controller may search their library "
            "for a basic land card, put it onto the battlefield tapped, then "
            "shuffle."
        ),
        "type_line": "Instant",
        "cmc": 1.0,
    },
    {
        "key": "removal/minus_counters",
        "detector": "removal",
        "oracle_text": "Target creature gets -4/-4 until end of turn.",
        "type_line": "Instant",
        "cmc": 3.0,
    },
    {
        "key": "removal/free_cast",
        "detector": "removal",
        "oracle_text": (
            "If you control a commander, you may cast this spell without paying "
            "its mana cost. Destroy target creature or planeswalker."
        ),
        "type_line": "Instant",
        "cmc": 4.0,
    },
    {
        "key": "removal/boomerang_bounce",
        "detector": "removal",
        "oracle_text": "Return target permanent to its owner's hand.",
        "type_line": "Instant",
        "cmc": 2.0,
    },
    {
        "key": "removal/neg_destroy_own",
        "detector": "removal",
        "oracle_text": "Destroy target creature you control. Draw two cards.",
        "type_line": "Instant",
        "cmc": 2.0,
    },
    {
        "key": "removal/nonmatch_elves",
        "detector": "removal",
        "oracle_text": "{T}: Add {G}.",
        "type_line": "Creature — Elf Druid",
        "cmc": 1.0,
    },
    # ---- Protection ----
    {
        "key": "protection/heroic_intervention",
        "detector": "protection",
        "oracle_text": (
            "Permanents you control gain hexproof and indestructible until "
            "end of turn."
        ),
        "type_line": "Instant",
        "cmc": 2.0,
    },
    {
        "key": "protection/teferis_protection",
        "detector": "protection",
        "oracle_text": (
            "Until your next turn, your life total can't change, you gain "
            "protection from everything, and all permanents you control phase "
            "out."
        ),
        "type_line": "Instant",
        "cmc": 3.0,
    },
    {
        "key": "protection/swiftfoot_boots",
        "detector": "protection",
        "oracle_text": (
            "Equipped creature has hexproof and haste. Equip {1}."
        ),
        "type_line": "Artifact — Equipment",
        "cmc": 2.0,
    },
    {
        "key": "protection/lightning_greaves",
        "detector": "protection",
        "oracle_text": (
            "Equipped creature has shroud and haste. Equip {0}."
        ),
        "type_line": "Artifact — Equipment",
        "cmc": 2.0,
    },
    {
        "key": "protection/flawless_maneuver",
        "detector": "protection",
        "oracle_text": (
            "If you control a commander, you may cast this spell without paying "
            "its mana cost. Creatures you control gain indestructible until "
            "end of turn."
        ),
        "type_line": "Instant",
        "cmc": 3.0,
    },
    {
        "key": "protection/ward_creature",
        "detector": "protection",
        "oracle_text": "Ward {2}",
        "type_line": "Creature — Sphinx",
        "cmc": 5.0,
    },
    {
        "key": "protection/protection_from",
        "detector": "protection",
        "oracle_text": "Target creature gains protection from the color of your choice until end of turn.",
        "type_line": "Instant",
        "cmc": 1.0,
    },
    {
        "key": "protection/redirect",
        "detector": "protection",
        "oracle_text": "Change the target of target spell with a single target.",
        "type_line": "Instant",
        "cmc": 1.0,
    },
    {
        "key": "protection/totem_armor",
        "detector": "protection",
        "oracle_text": (
            "Enchant creature. Totem armor. Enchanted creature gets +1/+1."
        ),
        "type_line": "Enchantment — Aura",
        "cmc": 2.0,
    },
    {
        "key": "protection/neg_opponent_hexproof",
        "detector": "protection",
        "oracle_text": "Target opponent gains hexproof until end of turn.",
        "type_line": "Instant",
        "cmc": 1.0,
    },
    {
        "key": "protection/nonmatch_sol_ring",
        "detector": "protection",
        "oracle_text": "{T}: Add {C}{C}.",
        "type_line": "Artifact",
        "cmc": 1.0,
    },
]


def _compute_snapshot() -> dict[str, dict | None]:
    """Run every corpus card through its detector and collect the outputs."""
    out: dict[str, dict | None] = {}
    for entry in CORPUS:
        detect = _DETECTORS[entry["detector"]]
        card = {
            "oracle_text": entry["oracle_text"],
            "type_line": entry["type_line"],
            "cmc": entry["cmc"],
        }
        out[entry["key"]] = detect(card)
    return out


def _load_golden() -> dict:
    with GOLDEN_PATH.open() as f:
        return json.load(f)


def test_corpus_keys_are_unique() -> None:
    """Guards against accidental duplicate keys masking a case."""
    keys = [entry["key"] for entry in CORPUS]
    assert len(keys) == len(set(keys))


def test_golden_file_covers_corpus() -> None:
    """The golden file must have an entry for every corpus card and no extras."""
    golden = _load_golden()
    assert set(golden.keys()) == {entry["key"] for entry in CORPUS}


def test_detector_outputs_match_snapshot() -> None:
    """Each detector's output must exactly match the frozen golden value."""
    golden = _load_golden()
    actual = _compute_snapshot()
    # Compare per-key for clearer failure messages.
    for key in sorted(golden):
        assert actual[key] == golden[key], f"snapshot mismatch for {key}"


def _regenerate() -> None:
    """Write the current detector outputs to the golden file."""
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    snapshot = _compute_snapshot()
    with GOLDEN_PATH.open("w") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"Wrote {len(snapshot)} snapshots to {GOLDEN_PATH}")


if __name__ == "__main__":
    import sys

    if "--regenerate" in sys.argv:
        _regenerate()
    else:
        print("Pass --regenerate to rewrite the golden file.")
