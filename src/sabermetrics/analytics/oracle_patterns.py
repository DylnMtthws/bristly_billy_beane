"""Single source of canonical oracle-text patterns for functional categories.

Both :mod:`sabermetrics.analytics.components` (deck-composition counting) and
:mod:`sabermetrics.analytics.role_tagger` (per-card role assignment) match cards
against these patterns, so the two agree on what counts as ramp, draw, removal,
etc. Previously each module carried its own divergent copy.

These answer the coarse question "does this card look like ramp/draw/...". The
scored candidate detectors in :mod:`sabermetrics.analytics.detectors` keep their
own negative-gated, scored pattern sets on purpose — they answer the different,
finer question "how good a ramp/removal/protection card is this".

Patterns are matched case-insensitively. Where the two source modules diverged,
the canonical set takes the more complete/precise option (e.g. worded card
counts in DRAW, ``X`` in board-wipe stat reductions) and drops loose patterns
that produced false positives (e.g. a bare ``whenever ... draw`` trigger).
"""

import re


def _compile(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


RAMP = _compile([
    r"add\s+\{[WUBRGC]",
    r"add\s+one mana of any",
    r"add\s+\w+\s+mana",
    r"search your library for.*(?:basic )?land.*put.*(?:onto|on) the battlefield",
    r"put.*land.*(?:from|onto|on).*the battlefield",
    r"create.*treasure",
])

FIXING = _compile([
    r"add one mana of any color",
    r"add\s+\{[WUBRG]\}\s*(?:or|,)\s*\{[WUBRG]\}",
])

# Worded ("draw two cards") and numeric ("draw 2 cards") counts; the bare
# "whenever ... draw" trigger from the old components set is dropped — it
# tagged opponent-draw payoffs as card draw.
DRAW = _compile([
    r"draws?\s+(?:a|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+cards?",
    r"look at the top.*(?:put|draw)",
    r"reveal.*(?:put.*hand|draw)",
])

REMOVAL = _compile([
    r"destroy target",
    r"exile target",
    r"deals?\s+\d+\s+damage\s+to\s+(?:target|any|each)",
    r"target.*gets?\s+\-\d+/\-\d+",
    r"counter target spell",
    r"return target.*to.*(?:owner|hand)",
])

# Stat reductions allow X as well as a digit so "all creatures get -X/-X"
# (e.g. Toxic Deluge) registers as a wipe.
BOARD_WIPE = _compile([
    r"destroy all",
    r"exile all",
    r"(?:each|all) (?:creature|permanent|nonland).*gets?\s+\-(?:\d+|x)/\-(?:\d+|x)",
    r"return all.*to.*(?:owner|hand)",
])

# Specific search targets only — basic-land fetch is ramp, not tutoring.
TUTOR = _compile([
    r"search your library for a card",
    r"search your library for an? (?:creature|instant|sorcery|artifact|enchantment|planeswalker)",
])

RECURSION = _compile([
    r"return.*from.*graveyard.*(?:hand|battlefield|to the battlefield)",
    r"return.*(?:card|creature|permanent).*to the battlefield.*(?:under|from)",
    r"put.*from.*graveyard.*(?:onto|into).*(?:battlefield|hand)",
    r"cast.*from.*graveyard",
])

PROTECTION = _compile([
    r"hexproof",
    r"indestructible",
    r"phase out",
    r"can't be (?:the target|countered|destroyed)",
    r"protection from",
])

WINCON = _compile([
    r"you win the game",
    r"extra turn",
    r"each opponent loses",
    r"infinite",
    r"damage to each opponent",
])

THREAT = _compile([
    r"deals? combat damage to.*player",
    r"commander damage",
])

# Payoffs locked behind attacking with multiple creatures. Consumed by the
# structural scorer as a discount gate (not a role): decks whose real lists
# run few attackers rarely meet these conditions, so the printed payoff
# overstates the card ("prepared" MDFCs, battalion, raid).
COMBAT_GATED = _compile([
    r"attacks? with (?:two|three|four|\d+) or more creatures",
    r"\bbattalion\b",
    r"\braid\b\s*[—-]",
])


def is_combat_gated(oracle_text: str | None) -> bool:
    """Whether the card's payoff requires attacking with multiple creatures.

    Args:
        oracle_text: The card's oracle text (None-safe).

    Returns:
        True when any combat-gate pattern matches.
    """
    text = oracle_text or ""
    return any(p.search(text) for p in COMBAT_GATED)


# Convenience mapping consumed by role_tagger (ordered to match ROLE_TAGS).
ROLE_PATTERNS: dict[str, list[re.Pattern]] = {
    "ramp": RAMP,
    "fixing": FIXING,
    "draw": DRAW,
    "removal": REMOVAL,
    "board_wipe": BOARD_WIPE,
    "tutor": TUTOR,
    "recursion": RECURSION,
    "protection": PROTECTION,
    "wincon": WINCON,
    "threat": THREAT,
}
