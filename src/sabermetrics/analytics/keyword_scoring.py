"""Graded keyword-match scoring. Deliberately NOT in ``mechanics/``.

Split out of ``oracle_keywords`` when that module's extraction half was promoted
to :mod:`sabermetrics.mechanics`. The extraction half answers "does this text
reference this keyword" — an auditable predicate. The functions here answer
"how much is that worth", with hand-tuned weights (1.0 / 0.5 / 0.35) and a
``cmc >= 5`` threshold that exist to serve :mod:`sabermetrics.analytics.cvar`
and the casual/budget objective it was built for.

That objective is not the cEDH objective, so these weights are not promoted,
not imported by the new path, and not consulted. Some of the *method* may be
worth rebuilding against a stated cEDH objective later; that is a decision to
make on purpose, with new acceptance criteria, not by importing a module.
"""

from __future__ import annotations

import json
import re


def card_matches_referenced_keywords(
    card: dict,
    referenced_keywords: list[str],
    referenced_mechanics: list[str],
) -> bool:
    """Check if a candidate card matches any referenced keyword or mechanic.

    Inspects the card's keywords array, oracle text, and type line.

    Args:
        card: Card dict with keywords, oracle_text, type_line fields.
        referenced_keywords: Keywords extracted from the commander's oracle text.
        referenced_mechanics: Mechanic tags extracted from the commander's oracle text.

    Returns:
        True if the card matches at least one referenced keyword or mechanic.
    """
    return (
        referenced_match_strength(card, referenced_keywords, referenced_mechanics) > 0.0
    )


def _grants_durably(oracle_text: str, keyword: str) -> bool:
    """Whether the text grants ``keyword`` to your permanents without expiring.

    Durable ("Creatures you control have flying") counts as keyword support;
    temporary ("they gain haste until end of turn") does not.

    Args:
        oracle_text: Lowercased card oracle text.
        keyword: The keyword being sought.

    Returns:
        True for a durable grant clause naming the keyword.
    """
    for sentence in re.split(r"[.\n]", oracle_text):
        if keyword not in sentence:
            continue
        if "until" in sentence:  # expires -- not support
            continue
        if re.search(r"\b(?:have|has|gains?)\b", sentence):
            return True
    return False


def referenced_match_strength(
    card: dict,
    referenced_keywords: list[str],
    referenced_mechanics: list[str],
) -> float:
    """How strongly a card answers what the commander references (0.0-1.0).

    Graded rather than boolean because the flat bonus made every match
    equivalent: in a Yarus (face-down) deck, Tibalt earned the same credit
    for the word "haste" appearing in an unrelated ultimate as a real morph
    creature earned for matching ``face_down_synergy``, and out-scored it on
    the other terms. Tiers, strongest first:

    * 1.0 -- matches a referenced MECHANIC (archetype-defining: these come
      from specific multi-word patterns, not a single word).
    * 0.5 -- card POSSESSES a sought keyword (its Scryfall keywords array,
      or its own rules text grants it to itself).
    * 0.35 -- card GRANTS a sought keyword to your creatures (an anthem-style
      enabler is real support, just weaker than being the thing itself).
    * 0.0 -- the keyword merely appears somewhere in the card's text.

    Args:
        card: Card dict with keywords, oracle_text, type_line fields.
        referenced_keywords: Keywords the commander seeks.
        referenced_mechanics: Mechanic tags the commander references.

    Returns:
        Match strength in [0.0, 1.0].
    """
    if not referenced_keywords and not referenced_mechanics:
        return 0.0

    # Check card keywords array
    card_kw = card.get("keywords", "[]")
    if isinstance(card_kw, str):
        try:
            card_kw = json.loads(card_kw)
        except (json.JSONDecodeError, TypeError):
            card_kw = []
    card_keywords = {k.lower() for k in (card_kw or [])}
    card_oracle = (card.get("oracle_text") or "").lower()

    best = 0.0
    for ref_kw in referenced_keywords:
        if ref_kw in card_keywords:
            best = max(best, 0.5)
            continue
        # Self-possession in rules text ("this creature has flying").
        if re.search(
            rf"(?:this (?:creature|permanent)|it)\s+(?:has|gains?)\s+[^.]*\b{re.escape(ref_kw)}\b",
            card_oracle,
        ):
            best = max(best, 0.5)
        # Granting it to your team DURABLY is support, not the thing itself.
        # A temporary grant ("they gain haste until end of turn" on a -6
        # ultimate) is not keyword support at all -- that clause is what
        # made Tibalt read as a haste payoff in a Yarus deck.
        elif _grants_durably(card_oracle, ref_kw):
            best = max(best, 0.35)

    if best >= 1.0:
        return best

    # Check mechanic tags
    type_line = (card.get("type_line") or "").lower()
    for mech in referenced_mechanics:
        if mech == "toughness_matters":
            # Walls and high-toughness creatures
            if "wall" in type_line or "defender" in card_keywords:
                return 1.0
            # Cards that mechanically USE toughness as a resource —
            # not cards that merely set or mention toughness values
            # (e.g. "base power and toughness 0/1" is NOT relevant).
            if re.search(
                r"(?:"
                r"equal to (?:its |that creature's |their )?toughness"
                r"|total toughness"
                r"|(?:creatures?|permanents?)\s+(?:you control\s+)?with defender"
                r"|\+0/\+\d"
                r"|assigns? combat damage equal to"
                r")",
                card_oracle,
            ):
                return 1.0
        elif mech == "artifact_creature":
            if "artifact" in type_line and "creature" in type_line:
                return 1.0
        elif mech == "enchantment_creature":
            if "enchantment" in type_line and "creature" in type_line:
                return 1.0
        elif mech == "tap_synergy":
            if re.search(
                r"(?:tap\s+target|tap\s+an?\s+untapped|doesn'?t\s+untap|becomes?\s+tapped)",
                card_oracle,
            ):
                return 1.0
        elif mech == "face_down_synergy":
            face_kw = {"morph", "megamorph", "disguise"}
            if card_keywords & face_kw:
                return 1.0
            if re.search(r"(?:face\s+down|face\s+up|manifest|cloak)", card_oracle):
                return 1.0
        elif mech == "sacrifice_synergy":
            if re.search(
                r"(?:sacrifice\s+(?:a|an|another)|when\s+this\s+creature\s+dies)",
                card_oracle,
            ):
                return 1.0
        elif mech == "ability_cost_reduction":
            # Cards with a MANA-cost activated ability ("{7}{R}:" invoker
            # class). The cost segment before the colon must contain a mana
            # symbol: "{T}: Add {G}" has nothing to reduce, and morph/
            # disguise turn-up is a SPECIAL ACTION printed without a colon
            # ("Morph {6}{R}{R}") -- ability-cost reducers touch neither.
            if re.search(
                r"(?mi)^[^:\n]*\{(?:\d+|[WUBRGCXS](?:/[WUBRGCP])?)\}[^:\n]*:",
                card_oracle,
            ):
                return 1.0
        elif mech == "cost_reduction":
            # High-CMC cards benefit most from cost reduction
            cmc = float(card.get("cmc", 0))
            if cmc >= 5:
                return 1.0
        elif mech == "counters_matter":
            if "+1/+1 counter" in card_oracle:
                return 1.0
        elif mech == "death_trigger":
            if re.search(r"(?:when\b.*\bdies\b|whenever\b.*\bdies\b)", card_oracle):
                return 1.0
        elif mech == "graveyard_synergy":
            grave_kw = {
                "flashback",
                "unearth",
                "embalm",
                "eternalize",
                "escape",
                "disturb",
            }
            if card_keywords & grave_kw:
                return 1.0
            if "from your graveyard" in card_oracle:
                return 1.0
        elif mech == "token_synergy":
            if "create" in card_oracle and "token" in card_oracle:
                return 1.0
        elif mech == "spellslinger":
            if "instant" in type_line or "sorcery" in type_line:
                return 1.0
            if re.search(
                r"(?:instant\s+or\s+sorcery|noncreature\s+spell)", card_oracle
            ):
                return 1.0
        elif mech == "aura_synergy":
            # Card IS an Aura (type_line: "Enchantment — Aura")
            if "aura" in type_line:
                return 1.0
            # Card mechanically references Auras
            if re.search(r"\bauras?\b", card_oracle):
                return 1.0
            # Enchantress/constellation effects (enablers for Aura decks)
            if re.search(
                r"(?:whenever\b.*\b(?:cast\b.*\benchantment|enchantment\b.*\b(?:enters|put))"
                r"|for each\s+enchantment)",
                card_oracle,
            ):
                return 1.0
        elif mech == "enchantment_synergy":
            if "enchantment" in type_line:
                return 1.0
            if re.search(
                r"(?:enchantments?\s+you\s+control"
                r"|whenever\b.*\benchantment"
                r"|enchantment\s+card)",
                card_oracle,
            ):
                return 1.0
        elif mech == "equipment_synergy":
            if "equipment" in type_line:
                return 1.0
            if re.search(r"(?:equipped?\b|equip\b|equipment)", card_oracle):
                return 1.0
        elif mech == "vehicle_synergy":
            if "vehicle" in type_line:
                return 1.0
            if "crew" in card_keywords:
                return 1.0
            if re.search(r"\bvehicles?\b", card_oracle):
                return 1.0

    return best
