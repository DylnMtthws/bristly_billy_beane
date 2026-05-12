"""Oracle text keyword extraction for CVAR synergy scoring.

Extracts keyword abilities that a commander's oracle text *references*
(e.g. "creatures with defender") rather than possesses. This closes the
signal gap where commanders like Arcades, the Strategist reference keywords
they don't have in their own keywords array.

Pure-function module following theme_patterns.py conventions:
module-level compiled regexes, no DB access.
"""

import json
import re

# ---------------------------------------------------------------------------
# Canonical MTG keyword abilities (CR 702.x)
# ---------------------------------------------------------------------------

MTG_KEYWORD_ABILITIES: set[str] = {
    # Evergreen
    "deathtouch", "defender", "double strike", "enchant", "equip",
    "first strike", "flash", "flying", "haste", "hexproof",
    "indestructible", "intimidate", "landwalk", "lifelink", "menace",
    "protection", "reach", "shroud", "trample", "vigilance", "ward",
    # Deciduous / returning
    "absorb", "affinity", "afflict", "afterlife", "aftermath",
    "amass", "amplify", "annihilator", "ascend", "aura swap",
    "awaken", "backup", "banding", "bargain", "battalion",
    "battle cry", "bestow", "bloodthirst", "boast", "bolster",
    "bushido", "buyback", "cascade", "casualty", "champion",
    "changeling", "channel", "cipher", "cleave", "companion",
    "compleated", "conjure", "connive", "conspire", "convoke",
    "craft", "crew", "cumulative upkeep", "cycling", "dash",
    "daybound", "decayed", "defender", "delve", "demonstrate",
    "descend", "detain", "devoid", "devour", "discover",
    "disturb", "domain", "dredge", "echo", "embalm", "emerge",
    "eminence", "enchant", "encore", "enlist", "enrage",
    "entwine", "epic", "escalate", "escape", "eternalize",
    "evoke", "evolve", "exalted", "exploit", "extort",
    "fabricate", "fading", "fear", "flanking", "flashback",
    "foretell", "fortify", "frenzy", "fuse", "goad",
    "graft", "gravestorm", "haunt", "heroic", "hideaway",
    "horsemanship", "improvise", "incubate", "infect", "ingest",
    "inspired", "intensity", "investigate", "jump-start",
    "kicker", "landfall", "level up", "living weapon",
    "madness", "megamorph", "meld", "mentor", "miracle",
    "modular", "monstrosity", "morph", "mountainwalk", "mutate",
    "myriad", "nightbound", "ninjutsu", "offering", "outlast",
    "overload", "partner", "persist", "phasing", "plainswalk",
    "poisonous", "populate", "proliferate", "provoke", "prowess",
    "prowl", "rampage", "ravenous", "rebound", "reconfigure",
    "recover", "reinforce", "renown", "replicate", "retrace",
    "riot", "ripple", "saddle", "scavenge", "shadow",
    "skulk", "soulbond", "soulshift", "spectacle", "splice",
    "split second", "squad", "storm", "sunburst", "support",
    "surge", "suspend", "swampwalk", "totem armor", "training",
    "transfigure", "transmute", "treasure", "tribute", "undaunted",
    "undying", "unearth", "unleash", "vanishing", "wither",
    # Additional commonly referenced
    "forestwalk", "islandwalk", "fear", "intimidate",
    "toxic", "for mirrodin!", "living metal",
}

# ---------------------------------------------------------------------------
# Reference patterns — phrases where oracle text references a keyword
# ---------------------------------------------------------------------------

_REFERENCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"creatures?\s+(?:you control\s+)?with\s+(\w[\w\s]*?)(?:\s+(?:get|gain|have|deal|assign|can|don't|enters?|you))",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:has|gains?|have)\s+(\w[\w\s]*?)(?:\.|,|\s+and\s|\s+until)",
        re.IGNORECASE,
    ),
    re.compile(
        r"each\s+creature\s+with\s+(\w[\w\s]*?)(?:\s+(?:gets?|gains?|deals?|assigns?|can|you))",
        re.IGNORECASE,
    ),
    re.compile(
        r"creatures?\s+with\s+(\w[\w\s]*?)\s+(?:can't|don't|may|also|are|that)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:whenever|when)\s+a\s+creature\s+with\s+(\w[\w\s]*?)\s+",
        re.IGNORECASE,
    ),
]

# ---------------------------------------------------------------------------
# Mechanic reference patterns — special mechanics not in keyword arrays
# ---------------------------------------------------------------------------

_MECHANIC_REFERENCE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"assigns?\s+combat\s+damage\s+equal\s+to\s+its\s+toughness",
            re.IGNORECASE,
        ),
        "toughness_matters",
    ),
    (
        re.compile(r"artifact\s+creatures?\s+(?:you control|enters?|gets?)", re.IGNORECASE),
        "artifact_creature",
    ),
    (
        re.compile(r"enchantment\s+creatures?\s+(?:you control|enters?|gets?)", re.IGNORECASE),
        "enchantment_creature",
    ),
    # --- Tap/untap synergy (Hylda, Derevi, Fallaji Wayfarer) ---
    (
        re.compile(
            r"(?:tap\s+an?\s+untapped\s+creature|becomes?\s+tapped|doesn'?t\s+untap|tap\s+target\s+creature)",
            re.IGNORECASE,
        ),
        "tap_synergy",
    ),
    # --- Face-down synergy (Yarus, Kadena, Animar) ---
    (
        re.compile(
            r"(?:face[- ]down\s+creature|turned\s+face\s+up|morph|manifest|disguise|cloak)",
            re.IGNORECASE,
        ),
        "face_down_synergy",
    ),
    # --- Sacrifice synergy (Korvold, Prossh, Meren) ---
    (
        re.compile(
            r"(?:whenever\b.*\bsacrifice|sacrifice\s+(?:a|an|another)\s+(?:creature|permanent|artifact|enchantment))",
            re.IGNORECASE,
        ),
        "sacrifice_synergy",
    ),
    # --- Cost reduction (Rakdos Lord of Riots, Animar) ---
    (
        re.compile(
            r"(?:costs?\s+\{?\w+\}?\s+less|reduce.*cost)",
            re.IGNORECASE,
        ),
        "cost_reduction",
    ),
    # --- Counters matter (Atraxa, Marchesa) ---
    (
        re.compile(
            r"(?:\+1/\+1\s+counter.*(?:on\s+each|on\s+all|on\s+target)|whenever.*counter.*(?:placed|put))",
            re.IGNORECASE,
        ),
        "counters_matter",
    ),
    # --- Death trigger (Teysa Karlov, Syr Konrad) ---
    (
        re.compile(
            r"(?:whenever\b.*\bdies\b|whenever\b.*put\s+into\s+(?:a\s+)?graveyard\s+from\s+(?:the\s+)?battlefield)",
            re.IGNORECASE,
        ),
        "death_trigger",
    ),
    # --- Graveyard synergy (Muldrotha, Meren) ---
    (
        re.compile(
            r"(?:(?:return|cast)\b.*\b(?:from|in)\b.*\bgraveyard|(?:card|creature)\s+(?:in|from)\s+(?:your|a)\s+graveyard)",
            re.IGNORECASE,
        ),
        "graveyard_synergy",
    ),
    # --- Token synergy (Rhys, Adrix and Nev) ---
    (
        re.compile(
            r"(?:whenever\b.*\bcreate\b.*\btoken|(?:each|all)\s+tokens?\s+you\s+control)",
            re.IGNORECASE,
        ),
        "token_synergy",
    ),
    # --- Spellslinger (Feather, Kalamax, Veyran) ---
    (
        re.compile(
            r"(?:whenever\s+you\s+cast\b.*?\b(?:instant|sorcery|noncreature))",
            re.IGNORECASE,
        ),
        "spellslinger",
    ),
    # --- Aura synergy (Eriette, Uril, Bruna) ---
    (
        re.compile(
            r"(?:(?:number of|each|whenever\b.*)\bauras?\b"
            r"|enchanted\s+by\s+an?\s+aura"
            r"|auras?\s+(?:you control|enters?|attached))",
            re.IGNORECASE,
        ),
        "aura_synergy",
    ),
    # --- Enchantment synergy (Sythis, Zur, Tuvasa) ---
    (
        re.compile(
            r"(?:whenever\b.*\bcast\b.*\benchantment"
            r"|search\b.*\bfor\b.*\benchantment"
            r"|enchantments?\s+you\s+control(?!\s+can't))",
            re.IGNORECASE,
        ),
        "enchantment_synergy",
    ),
    # --- Equipment synergy (Nahiri, Bruenor, Wyleth) ---
    (
        re.compile(
            r"(?:equipped\s+creature"
            r"|equip\s+costs?"
            r"|(?:number of|each|whenever\b.*)\bequipments?\b"
            r"|equipment\s+(?:you control|enters?|attached))",
            re.IGNORECASE,
        ),
        "equipment_synergy",
    ),
    # --- Vehicle synergy (Shorikai, Greasefang) ---
    (
        re.compile(
            r"(?:(?:number of|each|whenever\b.*)\bvehicles?\b"
            r"|vehicles?\s+(?:you control|enters?))",
            re.IGNORECASE,
        ),
        "vehicle_synergy",
    ),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_referenced_keywords(oracle_text: str | None) -> list[str]:
    """Extract keyword abilities the card's oracle text references.

    Finds phrases like "creatures with defender" and validates the
    extracted candidate against MTG_KEYWORD_ABILITIES.

    Args:
        oracle_text: The card's oracle text (may be None).

    Returns:
        Deduplicated list of referenced keyword ability names (lowercase).
    """
    if not oracle_text:
        return []

    found: set[str] = set()
    text = oracle_text.lower()

    for pattern in _REFERENCE_PATTERNS:
        for match in pattern.finditer(text):
            candidate = match.group(1).strip().lower()
            # The candidate may have trailing words; try shrinking
            if candidate in MTG_KEYWORD_ABILITIES:
                found.add(candidate)
            else:
                # Try single first word (e.g. "flying deals" → "flying")
                first_word = candidate.split()[0] if candidate else ""
                if first_word in MTG_KEYWORD_ABILITIES:
                    found.add(first_word)
                # Try two-word combo (e.g. "double strike")
                words = candidate.split()
                if len(words) >= 2:
                    two_word = f"{words[0]} {words[1]}"
                    if two_word in MTG_KEYWORD_ABILITIES:
                        found.add(two_word)

    return sorted(found)


def extract_referenced_mechanics(oracle_text: str | None) -> list[str]:
    """Extract special mechanic tags from oracle text patterns.

    Detects mechanics like "toughness_matters" from patterns such as
    "assigns combat damage equal to its toughness".

    Args:
        oracle_text: The card's oracle text (may be None).

    Returns:
        Deduplicated list of mechanic tag strings.
    """
    if not oracle_text:
        return []

    found: set[str] = set()
    for pattern, tag in _MECHANIC_REFERENCE_PATTERNS:
        if pattern.search(oracle_text):
            found.add(tag)

    return sorted(found)


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
    if not referenced_keywords and not referenced_mechanics:
        return False

    # Check card keywords array
    card_kw = card.get("keywords", "[]")
    if isinstance(card_kw, str):
        card_kw = json.loads(card_kw)
    card_keywords = {k.lower() for k in card_kw}

    for ref_kw in referenced_keywords:
        if ref_kw in card_keywords:
            return True

    # Check card oracle text for keyword references
    card_oracle = (card.get("oracle_text") or "").lower()
    for ref_kw in referenced_keywords:
        # Card has/gains this keyword (e.g. "this creature has defender")
        if ref_kw in card_oracle:
            return True

    # Check mechanic tags
    type_line = (card.get("type_line") or "").lower()
    for mech in referenced_mechanics:
        if mech == "toughness_matters":
            # Walls and high-toughness creatures
            if "wall" in type_line or "defender" in card_keywords:
                return True
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
                return True
        elif mech == "artifact_creature":
            if "artifact" in type_line and "creature" in type_line:
                return True
        elif mech == "enchantment_creature":
            if "enchantment" in type_line and "creature" in type_line:
                return True
        elif mech == "tap_synergy":
            if re.search(
                r"(?:tap\s+target|tap\s+an?\s+untapped|doesn'?t\s+untap|becomes?\s+tapped)",
                card_oracle,
            ):
                return True
        elif mech == "face_down_synergy":
            face_kw = {"morph", "megamorph", "disguise"}
            if card_keywords & face_kw:
                return True
            if re.search(r"(?:face\s+down|face\s+up|manifest|cloak)", card_oracle):
                return True
        elif mech == "sacrifice_synergy":
            if re.search(
                r"(?:sacrifice\s+(?:a|an|another)|when\s+this\s+creature\s+dies)",
                card_oracle,
            ):
                return True
        elif mech == "cost_reduction":
            # High-CMC cards benefit most from cost reduction
            cmc = float(card.get("cmc", 0))
            if cmc >= 5:
                return True
        elif mech == "counters_matter":
            if "+1/+1 counter" in card_oracle:
                return True
        elif mech == "death_trigger":
            if re.search(r"(?:when\b.*\bdies\b|whenever\b.*\bdies\b)", card_oracle):
                return True
        elif mech == "graveyard_synergy":
            grave_kw = {"flashback", "unearth", "embalm", "eternalize", "escape", "disturb"}
            if card_keywords & grave_kw:
                return True
            if "from your graveyard" in card_oracle:
                return True
        elif mech == "token_synergy":
            if "create" in card_oracle and "token" in card_oracle:
                return True
        elif mech == "spellslinger":
            if "instant" in type_line or "sorcery" in type_line:
                return True
            if re.search(r"(?:instant\s+or\s+sorcery|noncreature\s+spell)", card_oracle):
                return True
        elif mech == "aura_synergy":
            # Card IS an Aura (type_line: "Enchantment — Aura")
            if "aura" in type_line:
                return True
            # Card mechanically references Auras
            if re.search(r"\bauras?\b", card_oracle):
                return True
            # Enchantress/constellation effects (enablers for Aura decks)
            if re.search(
                r"(?:whenever\b.*\b(?:cast\b.*\benchantment|enchantment\b.*\b(?:enters|put))"
                r"|for each\s+enchantment)",
                card_oracle,
            ):
                return True
        elif mech == "enchantment_synergy":
            if "enchantment" in type_line:
                return True
            if re.search(
                r"(?:enchantments?\s+you\s+control"
                r"|whenever\b.*\benchantment"
                r"|enchantment\s+card)",
                card_oracle,
            ):
                return True
        elif mech == "equipment_synergy":
            if "equipment" in type_line:
                return True
            if re.search(r"(?:equipped?\b|equip\b|equipment)", card_oracle):
                return True
        elif mech == "vehicle_synergy":
            if "vehicle" in type_line:
                return True
            if "crew" in card_keywords:
                return True
            if re.search(r"\bvehicles?\b", card_oracle):
                return True

    return False
