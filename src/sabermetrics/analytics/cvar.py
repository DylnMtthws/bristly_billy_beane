"""CVAR composite scoring (D4.4).

Weighted sum: synergy + mana_eff + replacement_val - price_penalty.
Pure function: same inputs -> same outputs. <100ms per card.
"""

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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
    weights_synergy: float = 0.35
    weights_mana_efficiency: float = 0.25
    weights_replacement_value: float = 0.25
    weights_price_efficiency: float = 0.15
    average_card_price: float = 2.0
    max_budget: Optional[float] = None


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

    return min(1.0, score)


def compute_mana_efficiency_score(card: dict) -> float:
    """Score mana efficiency: lower CMC relative to impact is better.

    Heuristic: cards with CMC 0-2 get high scores, 3-5 medium, 6+ low.
    Modified by card type (instants/sorceries at low CMC are especially good).
    Uses effective CMC (considers morph, evoke, dash, etc.) when lower
    than printed CMC.
    Range: 0.0 to 1.0.
    """
    from sabermetrics.analytics.effective_cost import compute_effective_cmc

    cmc = compute_effective_cmc(card)
    type_line = (card.get("type_line") or "").lower()
    oracle_text = (card.get("oracle_text") or "").lower()

    # Base score inversely proportional to CMC
    if cmc <= 1:
        base = 1.0
    elif cmc <= 2:
        base = 0.9
    elif cmc <= 3:
        base = 0.75
    elif cmc <= 4:
        base = 0.6
    elif cmc <= 5:
        base = 0.45
    elif cmc <= 6:
        base = 0.3
    else:
        base = max(0.1, 0.3 - (cmc - 6) * 0.05)

    # Bonus for instants (mana-flexible)
    if "instant" in type_line:
        base = min(1.0, base + 0.1)

    # Bonus for cards with mana generation
    if "add" in oracle_text and ("mana" in oracle_text or "{" in oracle_text):
        base = min(1.0, base + 0.15)

    # Penalty for high-cost cards without impact keywords
    impact_words = ["destroy", "exile", "draw", "create", "return",
                    "each opponent", "all creatures", "win the game"]
    if cmc >= 5 and not any(w in oracle_text for w in impact_words):
        base *= 0.7

    return base


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

    Range: 0.0 to 1.0. Cards without prices get 0.5 (neutral).
    """
    price = card.get("price_usd") or card.get("current_price_usd")
    if price is None:
        return 0.5

    price = float(price)
    if price <= 0:
        return 1.0

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

    # Look up CWE if available
    cwe = None
    if db_path is not None:
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute(
                "SELECT cwe_score FROM card_win_equity "
                "WHERE card_id = ? AND commander_id = ?",
                (card.get("id", ""), context.commander_id),
            )
            row = cursor.fetchone()
            if row:
                cwe = row[0]
            conn.close()
        except Exception:
            pass

    # Composite score
    composite = (
        context.weights_synergy * synergy
        + context.weights_mana_efficiency * mana_eff
        + context.weights_replacement_value * replacement
        + context.weights_price_efficiency * price_eff
    )

    # Boost with CWE if available (additive bonus scaled to 0-0.1)
    if cwe is not None:
        composite += 0.1 * max(0, cwe)

    return CVARResult(
        composite_score=round(composite, 4),
        synergy_score=round(synergy, 4),
        mana_efficiency_score=round(mana_eff, 4),
        replacement_value_score=round(replacement, 4),
        price_efficiency_score=round(price_eff, 4),
        card_win_equity=round(cwe, 4) if cwe is not None else None,
    )
