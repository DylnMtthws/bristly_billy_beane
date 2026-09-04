"""Regenerate the Kinnan strategy pack and the cEDH fixture corpus.

Run from the repository root:

    python scripts/build_cedh_fixtures.py

Two outputs, both checked in:

* ``config/cedh_packs/kinnan_basalt.yaml`` — the authored strategy pack. Real
  content: the 99 come from ``commander_simulator``'s
  ``data/kinnan.deck.toml`` (Moxfield snapshot 2026-09-01, cards_sha256
  ``f3919eaf...``), which is the list the simulator actually models. Roles,
  priorities and win packages are authored here.
* ``fixtures/cedh/cards.json`` — a **synthetic** card corpus covering exactly
  those cards, so the ordinary test suite can build the slice with no network
  and no Postgres.

The fixture's card facts are representative, not authoritative. Oracle ids are
uuid5 hashes of names and the oracle text is a one-line paraphrase. There are
no prices, because CardFacts has no price field: cEDH is proxy-normal and the
engine optimises for performance. Production reads the real values from
``mtg_v1.card_any_medium``; the fixture exists to exercise code paths, and it
says so in its own header so nothing downstream can mistake it for the corpus.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PACK_PATH = ROOT / "config" / "cedh_packs" / "kinnan_basalt.yaml"
CARDS_PATH = ROOT / "fixtures" / "cedh" / "cards.json"

#: Namespace for deterministic fixture oracle ids. Not a Scryfall id.
FIXTURE_NS = uuid.UUID("6c2f0a1e-4f3e-5a7b-9c1d-0e5f7a8b9c00")

COMMANDER = "Kinnan, Bonder Prodigy"

# (name, color_identity, type_line, mana_value)
#
# No price column. cEDH is proxy-normal, the engine optimises for
# performance, and CardFacts has no price field to populate.
LANDS = [
    ("Ancient Tomb", "", "Land", 0.0),
    ("Boseiju, Who Endures", "G", "Legendary Land", 0.0),
    ("Botanical Sanctum", "GU", "Land", 0.0),
    ("Breeding Pool", "GU", "Land — Forest Island", 0.0),
    ("City of Brass", "", "Land", 0.0),
    ("Command Tower", "", "Land", 0.0),
    ("Exotic Orchard", "", "Land", 0.0),
    ("Flooded Strand", "", "Land", 0.0),
    ("Forest", "G", "Basic Land — Forest", 0.0),
    ("Gemstone Caverns", "", "Legendary Land", 0.0),
    ("Island", "U", "Basic Land — Island", 0.0),
    ("Mana Confluence", "", "Land", 0.0),
    ("Misty Rainforest", "", "Land", 0.0),
    ("Otawara, Soaring City", "U", "Legendary Land", 0.0),
    ("Polluted Delta", "", "Land", 0.0),
    ("Rejuvenating Springs", "GU", "Land", 0.0),
    ("Scalding Tarn", "", "Land", 0.0),
    ("Tarnished Citadel", "", "Land", 0.0),
    ("Tropical Island", "GU", "Land — Forest Island", 0.0),
    ("Verdant Catacombs", "", "Land", 0.0),
    ("Waterlogged Grove", "GU", "Land", 0.0),
    ("Windswept Heath", "", "Land", 0.0),
    ("Wooded Foothills", "", "Land", 0.0),
    ("Yavimaya Coast", "GU", "Land", 0.0),
]

ACCELERATION = [
    ("Sol Ring", "", "Artifact", 1.0),
    ("Mana Vault", "", "Artifact", 1.0),
    ("Basalt Monolith", "", "Artifact", 3.0),
    ("Grim Monolith", "", "Legendary Artifact", 2.0),
    ("Chrome Mox", "", "Artifact", 0.0),
    ("Mox Diamond", "", "Artifact", 0.0),
    ("Mox Opal", "", "Legendary Artifact", 0.0),
    ("Mox Amber", "", "Legendary Artifact", 0.0),
    ("Lotus Petal", "", "Artifact", 0.0),
    ("Delighted Halfling", "G", "Creature — Halfling Citizen", 1.0),
    ("Birds of Paradise", "G", "Creature — Bird", 1.0),
    ("Elvish Mystic", "G", "Creature — Elf Druid", 1.0),
    ("Llanowar Elves", "G", "Creature — Elf Druid", 1.0),
    ("Fyndhorn Elves", "G", "Creature — Elf Druid", 1.0),
    ("Elvish Spirit Guide", "G", "Creature — Elf Spirit", 3.0),
    ("Arcane Signet", "", "Artifact", 2.0),
    ("Fellwar Stone", "", "Artifact", 2.0),
    ("Talisman of Curiosity", "GU", "Artifact", 2.0),
    ("Springleaf Drum", "", "Artifact", 1.0),
    ("Enduring Vitality", "G", "Enchantment Creature — Elemental", 3.0),
]

TUTORS = [
    ("Transmute Artifact", "U", "Sorcery", 2.0),
    ("Chord of Calling", "G", "Instant", 3.0),
    ("Finale of Devastation", "G", "Sorcery", 2.0),
    ("Drift of Phantasms", "U", "Creature — Wall", 3.0),
    ("Dizzy Spell", "U", "Sorcery", 1.0),
    ("Trophy Mage", "U", "Creature — Human Wizard", 3.0),
    ("Nature's Rhythm", "G", "Sorcery", 4.0),
    ("Invasion of Ikoria", "G", "Battle — Siege", 4.0),
]

INTERACTION = [
    ("Force of Will", "U", "Instant", 5.0),
    ("Force of Negation", "U", "Instant", 3.0),
    ("Flusterstorm", "U", "Instant", 1.0),
    ("Mental Misstep", "U", "Instant", 1.0),
    ("Swan Song", "U", "Instant", 1.0),
    ("An Offer You Can't Refuse", "U", "Instant", 1.0),
    ("Dispel", "U", "Instant", 1.0),
    ("Mindbreak Trap", "U", "Instant — Trap", 4.0),
    ("Misdirection", "U", "Instant", 4.0),
    ("Disrupting Shoal", "U", "Instant — Arcane", 2.0),
    ("Commandeer", "U", "Instant", 7.0),
    ("Chain of Vapor", "U", "Instant", 1.0),
    ("Snapback", "U", "Instant", 3.0),
    ("Sudden Substitution", "U", "Instant", 6.0),
    ("Volatile Stormdrake", "U", "Creature — Drake", 4.0),
    ("Sink into Stupor", "U", "Sorcery // Land", 3.0),
]

PROTECTION = [
    ("Fierce Guardianship", "U", "Instant", 3.0),
    ("Pact of Negation", "U", "Instant", 0.0),
    ("Borne Upon a Wind", "U", "Instant", 2.0),
    ("Endurance", "G", "Creature — Elemental Incarnation", 3.0),
    ("High Fae Trickster", "U", "Creature — Faerie Rogue", 4.0),
]

CARD_ADVANTAGE = [
    ("Rhystic Study", "U", "Enchantment", 3.0),
    ("Mystic Remora", "U", "Enchantment", 1.0),
    ("Sylvan Library", "G", "Enchantment", 2.0),
    ("The One Ring", "", "Legendary Artifact", 4.0),
    ("Consecrated Sphinx", "U", "Creature — Sphinx", 6.0),
    ("Faerie Mastermind", "U", "Creature — Faerie Rogue", 2.0),
    ("Wan Shi Tong, Librarian", "U", "Legendary Creature — Spirit", 4.0),
]

WIN_PACKAGE = [
    ("Thrasios, Triton Hero", "GU", "Legendary Creature — Merfolk Wizard", 2.0),
    ("Copy Artifact", "U", "Enchantment", 2.0),
    ("Clever Impersonator", "U", "Creature — Shapeshifter", 4.0),
    ("Mirrormade", "U", "Enchantment", 4.0),
    ("Copy Enchantment", "U", "Enchantment", 3.0),
    ("Flesh Duplicate", "U", "Creature — Shapeshifter", 5.0),
    ("Imposter Mech", "U", "Artifact — Vehicle", 3.0),
    ("Mirage Mirror", "", "Artifact", 3.0),
    ("Mockingbird", "U", "Creature — Bird", 2.0),
    ("Twincast", "U", "Instant", 4.0),
    ("Dramatic Reversal", "U", "Instant", 2.0),
    ("Valley Floodcaller", "U", "Creature — Otter Wizard", 3.0),
    ("Hullbreaker Horror", "U", "Creature — Kraken", 7.0),
    ("Emrakul, the Promised End", "", "Legendary Creature — Eldrazi", 13.0),
    ("Gene Pollinator", "U", "Creature — Mutant", 3.0),
    ("The Cabbage Merchant", "U", "Legendary Creature — Human", 2.0),
]

FLEX = [
    ("Colossal Skyturtle", "U", "Creature — Turtle // Land", 5.0),
    ("Hidden Strings", "U", "Sorcery", 2.0),
    ("Flash Photography", "U", "Instant", 2.0),
]

ROLE_GROUPS: list[tuple[str, list[tuple[str, str, str, float]]]] = [
    ("land", LANDS),
    ("acceleration", ACCELERATION),
    ("tutor", TUTORS),
    ("interaction", INTERACTION),
    ("protection", PROTECTION),
    ("card_advantage", CARD_ADVANTAGE),
    ("win_package", WIN_PACKAGE),
    ("flex", FLEX),
]

#: Cards eligible for a second role. Keeps the pool honest about the fact that
#: Basalt Monolith is both the ramp and the engine.
SECOND_ROLE = {
    "Basalt Monolith": "win_package",
    "Thrasios, Triton Hero": "card_advantage",
    "Copy Artifact": "acceleration",
    "Sink into Stupor": "land",
    "Colossal Skyturtle": "land",
    "Drift of Phantasms": "protection",
}

AUTO_INCLUDE = [
    "Basalt Monolith",
    "Thrasios, Triton Hero",
    "Sol Ring",
    "Mana Vault",
    "Grim Monolith",
    "Tropical Island",
    "Command Tower",
    "Force of Will",
    "Rhystic Study",
    "Mystic Remora",
]


def fixture_oracle_id(name: str) -> str:
    """Deterministic, stable fixture id. Not a Scryfall oracle_id."""
    return str(uuid.uuid5(FIXTURE_NS, name))


def build_pack() -> dict:
    cards = []
    for role, group in ROLE_GROUPS:
        for index, (name, *_rest) in enumerate(group):
            roles = [role]
            second = SECOND_ROLE.get(name)
            if second and second != role:
                roles.append(second)
            cards.append(
                {
                    "name": name,
                    "roles": roles,
                    # Authored order within the role, descending. Deterministic
                    # and inspectable: nothing samples this.
                    "priority": round(10.0 - index * 0.1, 2),
                }
            )
    targets = {role: len(group) for role, group in ROLE_GROUPS}
    return {
        "pack_id": "kinnan_basalt",
        "name": "Kinnan — Basalt engine into Thrasios",
        "summary": (
            "Kinnan doubles a nonland mana source's output. Basalt Monolith "
            "under that doubling produces unbounded colourless, and Thrasios "
            "turns unbounded colourless into the deck. The list plays a dense "
            "free-interaction suite to protect the two-card engine and clones "
            "to rebuild it after removal."
        ),
        "commander_names": [COMMANDER],
        "primary_win_package": {
            "name": "Unbounded colourless into Thrasios",
            "kind": "loop",
            "pieces": ["Basalt Monolith", "Thrasios, Triton Hero"],
            "converts_via": (
                "Kinnan's activated ability doubles Basalt Monolith's output, "
                "which unlocks the Monolith's own untap cost for unbounded "
                "colourless mana; Thrasios converts that into the deck. NOTE: "
                "no card in this list reads 'you win the game'. This is an "
                "assembled state that a competent pilot converts, which is why "
                "it is named for the state and not for an outcome."
            ),
            "notes": (
                "Named to match the simulator's assembly objective "
                "'infinite_C_into_thrasios'."
            ),
        },
        "secondary_win_packages": [
            {
                "name": "Cloned mana engine",
                "kind": "combo",
                "pieces": ["Copy Artifact", "Basalt Monolith"],
                "converts_via": (
                    "A second Monolith rebuilds the engine after the first is "
                    "answered. Does not itself close the game."
                ),
            },
            {
                "name": "Emrakul, the Promised End",
                "kind": "attrition",
                "pieces": [
                    "Emrakul, the Promised End",
                    "Finale of Devastation",
                ],
                "converts_via": (
                    "Opponent-facing: takes a turn rather than winning. "
                    "Explicitly not a win condition on its own."
                ),
            },
        ],
        "role_targets": targets,
        "auto_include": AUTO_INCLUDE,
        "cards": cards,
        "source": (
            "Moxfield list 'Grind Them Into Dust - Kinnan Midrange Control' "
            "(WoundedSatellite), via commander_simulator data/kinnan.deck.toml "
            "snapshot 2026-09-01, cards_sha256 f3919eaf185bfb73"
        ),
        "source_url": "",
        "curated_at": "2026-09-03",
        "version": "1",
        # This list IS the one commander_simulator authored a pack for, so it
        # asks for that pack by name. The mapping is declared here rather than
        # derived: the two repositories' pack namespaces are unrelated, and a
        # pack without a declared mapping falls back to explicit derived
        # execution instead of silently borrowing Kinnan logic.
        "simulator_pack_id": "kinnan-midrange-goldfish",
        "simulator_pack_version": "1.0.0",
    }


def build_cards() -> dict:
    rows = []
    everything = [
        (COMMANDER, "GU", "Legendary Creature — Human Wizard", 3.0),
        *[entry for _role, group in ROLE_GROUPS for entry in group],
    ]
    for name, identity, type_line, mana_value in everything:
        colors = tuple(identity)
        is_land = "Land" in type_line
        row = {
            "oracle_id": fixture_oracle_id(name),
            "name": name,
            "layout": "modal_dfc" if " // " in type_line else "normal",
            "mana_cost": "" if is_land else f"{{{int(mana_value)}}}",
            "mana_value": mana_value,
            "type_line": type_line,
            "oracle_text": f"[fixture paraphrase] {name}.",
            "colors": list(colors),
            "color_identity": list(colors),
            "keywords": [],
            "all_types": [t for t in ("Land",) if is_land]
            or [type_line.split(" —")[0].split()[-1]],
            # Empty for a card that cannot be cast: lands. The real contract
            # has 1,455 such rows and consumers taking min() must skip them.
            "castable_cmcs": [] if is_land else [mana_value],
            "has_land_face": is_land or " // Land" in type_line,
            "face_count": 2 if " // " in type_line else 0,
            "faces": [],
            "content_updated_at": "2026-09-01T21:20:19+00:00",
        }
        if " // " in type_line:
            front, back = type_line.split(" // ")
            row["faces"] = [
                {
                    "face_index": 0,
                    "name": name,
                    "mana_cost": f"{{{int(mana_value)}}}",
                    "face_mana_value": mana_value,
                    "type_line": front,
                    "oracle_text": f"[fixture paraphrase] {name}, front face.",
                },
                {
                    "face_index": 1,
                    "name": f"{name} back face",
                    "mana_cost": "",
                    "face_mana_value": 0.0,
                    "type_line": back,
                    "oracle_text": "[fixture paraphrase] land back face.",
                },
            ]
            # The contract leaves card-level mana_cost empty for MDFCs. The
            # fixture reproduces that so a consumer reading it hits the same
            # wall the real corpus produces.
            row["mana_cost"] = ""
            row["oracle_text"] = ""
        rows.append(row)

    legality = {
        "commander": {row["oracle_id"]: "legal" for row in rows},
    }
    return {
        "_README": (
            "SYNTHETIC FIXTURE — NOT THE CORPUS. Oracle ids are uuid5 hashes "
            "of card names and oracle text is a placeholder. Card names and "
            "the 99-card list are real (see the pack's `source`). There are no "
            "prices: cEDH is proxy-normal, so CardFacts carries no price field "
            "and nothing in the engine could read one. Production reads "
            "mtg_v1.card_any_medium; this file exists so the test suite needs "
            "no Postgres."
        ),
        "snapshot": {
            "source_view": "fixture:mtg_v1.card_any_medium",
            "row_count": len(rows),
            "max_content_updated_at": "2026-09-01T21:20:19+00:00",
            "captured_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        },
        "cards": rows,
        "legality": legality,
    }


def main() -> None:
    pack = build_pack()
    names = [c["name"] for c in pack["cards"]]
    if len(names) != len(set(names)):
        duplicates = sorted({n for n in names if names.count(n) > 1})
        raise SystemExit(f"pack has duplicate cards: {duplicates}")
    if len(names) != 99:
        raise SystemExit(f"pack has {len(names)} cards, expected 99")
    if sum(pack["role_targets"].values()) != 99:
        raise SystemExit("role targets do not sum to 99")

    PACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PACK_PATH.open("w", encoding="utf-8") as handle:
        handle.write(
            "# GENERATED by scripts/build_cedh_fixtures.py — edit that file.\n"
            "#\n"
            "# The 99 are the real list commander_simulator models (see\n"
            "# `source`). Roles, priorities and win packages are authored.\n"
            "# Cards are named here and resolved to oracle_ids at load time\n"
            "# against mtg_v1; a name that does not resolve fails the pack\n"
            "# loudly rather than quietly shortening the deck.\n\n"
        )
        yaml.safe_dump(pack, handle, sort_keys=False, allow_unicode=True)

    CARDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CARDS_PATH.write_text(json.dumps(build_cards(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {PACK_PATH.relative_to(ROOT)}")
    print(f"wrote {CARDS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
