"""CVAR composite scoring (D4.4).

Weighted sum: synergy + mana_eff + replacement_val - price_penalty.
Pure function: same inputs -> same outputs. <100ms per card.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Minimum price for any card — even basic lands cost ~$0.05.
# Prevents $0 display and perfect-efficiency scores for unpriced cards.
PRICE_FLOOR_USD = 0.05


class CVARResult(BaseModel):
    """Result of CVAR composite scoring."""

    composite_score: float
    synergy_score: float
    mana_efficiency_score: float
    replacement_value_score: float
    price_efficiency_score: float
    card_win_equity: Optional[float] = None


class ScoringContext(BaseModel):
    """Shared context passed to all scoring functions."""

    commander_id: str
    commander_name: str
    commander_colors: list[str]
    commander_keywords: list[str] = Field(default_factory=list)
    commander_oracle_text: Optional[str] = None
    referenced_keywords: list[str] = Field(default_factory=list)
    referenced_mechanics: list[str] = Field(default_factory=list)
    engine_keywords: list[str] = Field(default_factory=list)
    output_keywords: list[str] = Field(default_factory=list)
    edhrec_top_cards: dict[str, float] = Field(default_factory=dict)  # card_name_lower -> inclusion_pct
    desired_card_traits: list[str] = Field(default_factory=list)
    weights_synergy: float = 0.35
    weights_mana_efficiency: float = 0.25
    weights_replacement_value: float = 0.25
    weights_price_efficiency: float = 0.15
    average_card_price: float = 2.0
    max_budget: Optional[float] = None


def _count_desired_trait_matches(card: dict, traits: list[str]) -> int:
    """Count how many desired trait descriptions a card satisfies.

    For each trait string, checks:
    - MTG keywords extracted from trait text against card keywords
    - Type mentions against card type_line
    - CMC mentions ("low mana cost" → CMC ≤ 3)

    Args:
        card: Card dict with keywords, type_line, cmc fields.
        traits: Desired characteristic strings from value inversions.

    Returns:
        Number of traits matched.
    """
    from sabermetrics.analytics.oracle_keywords import MTG_KEYWORD_ABILITIES

    card_kw = card.get("keywords", "[]")
    if isinstance(card_kw, str):
        card_kw = json.loads(card_kw)
    card_keywords = {k.lower() for k in card_kw}
    oracle_text = (card.get("oracle_text") or "").lower()
    type_line = (card.get("type_line") or "").lower()
    cmc = float(card.get("cmc", 0) or 0)

    matches = 0
    for trait in traits:
        trait_lower = trait.lower()

        # Check MTG keywords in the trait description
        for kw in MTG_KEYWORD_ABILITIES:
            if kw in trait_lower and (kw in card_keywords or kw in oracle_text):
                matches += 1
                break
        else:
            # Check type mentions
            for type_kw in ("wall", "artifact", "enchantment", "creature", "instant", "sorcery"):
                if type_kw in trait_lower and type_kw in type_line:
                    matches += 1
                    break
            else:
                # Check CMC-related traits
                if ("low mana cost" in trait_lower or "low cmc" in trait_lower) and cmc <= 3:
                    matches += 1
                elif "high toughness" in trait_lower:
                    toughness = card.get("toughness")
                    if toughness is not None:
                        try:
                            if int(toughness) >= 4:
                                matches += 1
                        except (ValueError, TypeError):
                            pass

    return matches


def compute_synergy_score(card: dict, context: ScoringContext) -> float:
    """Score how well a card synergizes with the commander.

    Heuristic based on keyword overlap, oracle text pattern matching,
    and type-line relevance. Range: 0.0 to 1.0.
    """
    score = 0.0
    oracle_text = (card.get("oracle_text") or "").lower()
    cmdr_text = (context.commander_oracle_text or "").lower()

    # Keyword overlap
    card_kw = card.get("keywords", "[]")
    if isinstance(card_kw, str):
        card_kw = json.loads(card_kw)
    card_keywords = {k.lower() for k in card_kw}
    cmdr_keywords = {k.lower() for k in context.commander_keywords}

    if card_keywords & cmdr_keywords:
        score += 0.3

    # Engine-aware mechanic matching
    if context.engine_keywords:
        # Cards matching engine keywords get full bonus
        engine_matches = sum(
            1 for kw in context.engine_keywords if kw in oracle_text
        )
        if engine_matches > 0:
            score += min(0.4, engine_matches * 0.15)
        # Cards matching ONLY output keywords (not engine) get zero from
        # this signal — they are false synergy traps
        elif context.output_keywords:
            output_matches = sum(
                1 for kw in context.output_keywords if kw in oracle_text
            )
            if output_matches > 0 and engine_matches == 0:
                pass  # Deliberately no bonus — false synergy trap
    else:
        # Fallback: original mechanic_patterns for commanders without engine data
        mechanic_patterns = [
            "sacrifice", "token", "counter", "draw", "graveyard",
            "exile", "enters the battlefield", "dies", "combat damage",
            "life", "mana", "enchantment", "artifact", "creature",
            "aura", "equipment",
        ]
        shared = 0
        for pattern in mechanic_patterns:
            if pattern in oracle_text and pattern in cmdr_text:
                shared += 1
        if shared > 0:
            score += min(0.4, shared * 0.1)

    # Referenced keyword/mechanic match (commander references keywords it
    # doesn't possess, e.g. Arcades → defender)
    if context.referenced_keywords or context.referenced_mechanics:
        from sabermetrics.analytics.oracle_keywords import (
            card_matches_referenced_keywords,
        )

        if card_matches_referenced_keywords(
            card, context.referenced_keywords, context.referenced_mechanics
        ):
            score += 0.6

    # Color alignment bonus (more colors shared = better)
    card_ci = card.get("color_identity", "[]")
    if isinstance(card_ci, str):
        card_ci = json.loads(card_ci)
    if set(card_ci) <= set(context.commander_colors):
        # Cards that use more of the commander's colors are slightly better
        if len(card_ci) > 0:
            score += 0.1

    # Type line relevance
    type_line = (card.get("type_line") or "").lower()
    if "legendary" in type_line and "creature" in type_line:
        score += 0.05
    if "tribal" in cmdr_text or "creature type" in cmdr_text:
        if "creature" in type_line:
            score += 0.1

    # EDHREC behavioral corroboration (ADR-005: triangulation, not authority)
    card_name = (card.get("name") or "").lower()
    inclusion_pct = context.edhrec_top_cards.get(card_name, 0.0)
    if inclusion_pct > 0:
        score += min(0.2, inclusion_pct / 100.0 * 0.4)

    # Value inversion desired trait matching
    if context.desired_card_traits:
        trait_matches = _count_desired_trait_matches(card, context.desired_card_traits)
        score += min(0.25, trait_matches * 0.08)

    return min(1.0, score)


# Role-based impact multipliers for mana value scoring.
# Uses the highest multiplier among all roles a card has.
_ROLE_IMPACT: dict[str, float] = {
    "board_wipe": 1.5,
    "wincon": 1.5,
    "tutor": 1.4,
    "removal": 1.3,
    "draw": 1.3,
    "recursion": 1.25,
    "protection": 1.2,
    "ramp": 1.2,
    "threat": 1.1,
    "fixing": 1.0,
    "utility": 0.85,
}

# Oracle text fallback patterns for impact detection (when role tags unavailable)
_HIGH_IMPACT_PHRASES = [
    "destroy all", "exile all", "each opponent loses",
    "extra turn", "you win the game", "each player sacrifices",
    "all creatures get", "return all",
]
_MEDIUM_HIGH_IMPACT_PHRASES = [
    "destroy target", "exile target", "counter target spell",
    "draw a card", "draw cards", "draws a card",
    "search your library", "return target",
    "deals damage to any target", "deals damage to each",
    "whenever",
]
_RAMP_PHRASES = [
    "add {", "add one mana", "add mana",
]


def _compute_impact_multiplier(card: dict) -> float:
    """Estimate card impact from role tags or oracle text.

    Returns a multiplier from 0.7 (low impact) to 1.5 (high impact).
    """
    # Try role tags first (pre-computed, more accurate)
    role_tags = card.get("role_tags")
    if isinstance(role_tags, str):
        role_tags = json.loads(role_tags)
    if role_tags:
        return max(_ROLE_IMPACT.get(r, 1.0) for r in role_tags)

    # Fallback: oracle text pattern matching
    oracle_text = (card.get("oracle_text") or "").lower()

    if any(p in oracle_text for p in _HIGH_IMPACT_PHRASES):
        return 1.5
    if any(p in oracle_text for p in _MEDIUM_HIGH_IMPACT_PHRASES):
        return 1.3
    if any(p in oracle_text for p in _RAMP_PHRASES):
        return 1.2

    # Low-impact: creatures with short/empty oracle text (vanilla or near-vanilla)
    type_line = (card.get("type_line") or "").lower()
    if "creature" in type_line and len(oracle_text) < 40:
        return 0.7

    return 1.0


def compute_mana_efficiency_score(card: dict) -> float:
    """Score mana value: impact delivered relative to mana spent.

    Combines a Commander-appropriate CMC curve with an impact multiplier
    derived from role tags or oracle text. Range: 0.0 to 1.0.
    """
    from sabermetrics.analytics.effective_cost import compute_effective_cmc

    cmc = compute_effective_cmc(card)
    type_line = (card.get("type_line") or "").lower()

    # Base score: flattened curve for Commander's slower pace
    if cmc <= 0:
        base = 0.65
    elif cmc <= 1:
        base = 0.70
    elif cmc <= 2:
        base = 0.75
    elif cmc <= 3:
        base = 0.70
    elif cmc <= 4:
        base = 0.60
    elif cmc <= 5:
        base = 0.50
    elif cmc <= 6:
        base = 0.40
    else:
        base = max(0.25, 0.40 - (cmc - 6) * 0.05)

    # Impact multiplier: high-impact cards score better per mana
    impact = _compute_impact_multiplier(card)

    # Instant bonus: cheap high-impact instants (Swords to Plowshares,
    # Counterspell, Path to Exile) get a larger bonus — the combination
    # of low cost + instant speed + strong effect is premium in Commander.
    if "instant" in type_line:
        if cmc <= 2 and impact >= 1.2:
            base = min(1.0, base + 0.3)
        else:
            base = min(1.0, base + 0.1)

    return min(1.0, max(0.0, base * impact))


def compute_replacement_value(
    card: dict, db_path: Path | None = None, commander_id: str | None = None
) -> float:
    """Score how hard it is to replace this card's effect.

    Higher score = more unique/irreplaceable effect.
    Uses card rarity and keyword uniqueness as proxies.
    Range: 0.0 to 1.0.
    """
    rarity = (card.get("rarity") or "common").lower()
    oracle_text = (card.get("oracle_text") or "").lower()

    # Rarity as proxy for uniqueness
    rarity_scores = {
        "mythic": 0.5,
        "rare": 0.35,
        "uncommon": 0.2,
        "common": 0.1,
    }
    base = rarity_scores.get(rarity, 0.1)

    # Unique effect patterns add to replacement value
    unique_patterns = [
        ("can't be countered", 0.15),
        ("extra turn", 0.2),
        ("search your library", 0.15),
        ("win the game", 0.2),
        ("can't attack", 0.1),
        ("can't cast", 0.1),
        ("hexproof", 0.1),
        ("indestructible", 0.1),
        ("double strike", 0.1),
    ]
    for pattern, bonus in unique_patterns:
        if pattern in oracle_text:
            base += bonus

    return min(1.0, base)


def compute_price_efficiency(
    card: dict, avg_price: float = 2.0
) -> float:
    """Score price efficiency: cheaper cards relative to average score higher.

    Range: 0.0 to 1.0. Cards without prices are treated as floor-priced.
    """
    price = card.get("price_usd") or card.get("current_price_usd")
    if price is None:
        price = PRICE_FLOOR_USD
    price = max(float(price), PRICE_FLOOR_USD)

    # Ratio-based scoring: cards at average price get 0.5
    # Cards at half price get ~0.75, double price get ~0.25
    ratio = avg_price / price
    return min(1.0, max(0.0, 0.5 * (1 + (ratio - 1) * 0.5)))


def compute_cvar(
    card: dict,
    context: ScoringContext,
    db_path: Path | None = None,
) -> CVARResult:
    """Compute CVAR composite score for a card given commander context.

    Formula: w1*synergy + w2*mana_eff + w3*replacement_val - w4*price_penalty

    Args:
        card: Card dict with standard fields.
        context: Scoring context with commander info and weights.
        db_path: Optional database path for CWE lookup.

    Returns:
        CVARResult with composite score and sub-scores.
    """
    synergy = compute_synergy_score(card, context)
    mana_eff = compute_mana_efficiency_score(card)
    replacement = compute_replacement_value(card, db_path, context.commander_id)
    price_eff = compute_price_efficiency(card, context.average_card_price)

    # Composite score. The card_win_equity boost was removed in Option A
    # criterion 3: card_win_equity is derived from tournament_results, for which
    # no data source is wired (TopDeck.gg ingestion is unconfigured), so the
    # table is empty and the boost never fired. The field stays None on the
    # result for backward compatibility; re-enable the read here if a real
    # tournament-outcome source is ever populated.
    composite = (
        context.weights_synergy * synergy
        + context.weights_mana_efficiency * mana_eff
        + context.weights_replacement_value * replacement
        + context.weights_price_efficiency * price_eff
    )

    return CVARResult(
        composite_score=round(composite, 4),
        synergy_score=round(synergy, 4),
        mana_efficiency_score=round(mana_eff, 4),
        replacement_value_score=round(replacement, 4),
        price_efficiency_score=round(price_eff, 4),
        card_win_equity=None,
    )
