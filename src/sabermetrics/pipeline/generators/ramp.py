"""Ramp package generator (6.5.4).

Deterministic ramp selection: Sol Ring always, Arcane Signet for multicolor,
diversify rocks vs land-ramp vs dorks, sorted by role-specific quality score.
"""

import json
import logging
import re
from pathlib import Path

import yaml

from sabermetrics.models.template import DeckTemplate
from sabermetrics.pipeline.slot_assigner import SlotAssignment

logger = logging.getLogger(__name__)

# --- Ramp quality regexes ---

_CONDITIONAL_MANA = re.compile(
    r"(if you control|when you (?:cast|discard)|sacrifice a |discard a )",
    re.IGNORECASE,
)
_RESTRICTED_MANA = re.compile(
    r"spend this mana only (?:to cast|on)",
    re.IGNORECASE,
)
_MANA_OUTPUT = re.compile(
    r"\{([WUBRGC])\}",
    re.IGNORECASE,
)
_ADD_CLAUSE = re.compile(
    r"add\b(.*?)(?:\.|$)",
    re.IGNORECASE,
)
_ADD_ANY_COLOR = re.compile(
    r"add (?:one mana of any|mana of any)",
    re.IGNORECASE,
)
_ADD_GENERIC_MANA = re.compile(
    r"add \{(\d+)\}",
    re.IGNORECASE,
)


def _load_auto_includes() -> dict:
    """Load auto-include cards from config."""
    config_path = Path(__file__).resolve().parent.parent.parent.parent.parent / "config" / "auto_include_cards.yaml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def _estimate_mana_output(oracle: str) -> tuple[float, bool]:
    """Estimate mana output per activation from oracle text.

    Returns:
        Tuple of (mana_per_activation, produces_colored).
    """
    oracle_lower = oracle.lower()
    produces_colored = False
    total_mana = 0.0

    # "Add one mana of any color" or similar
    if _ADD_ANY_COLOR.search(oracle_lower):
        produces_colored = True
        total_mana = max(total_mana, 1.0)

    # Count explicit mana symbols in Add clauses
    for match in _ADD_CLAUSE.finditer(oracle_lower):
        clause = match.group(1)
        symbols = _MANA_OUTPUT.findall(clause)
        if symbols:
            colored = [s for s in symbols if s.upper() != "C"]
            if colored:
                produces_colored = True
            total_mana = max(total_mana, len(symbols))

        # Generic mana like "Add {2}"
        generic = _ADD_GENERIC_MANA.findall(clause)
        for g in generic:
            total_mana = max(total_mana, float(g))

    # Fallback: if we see "add" and mana symbols anywhere
    if total_mana == 0 and "add" in oracle_lower:
        symbols = _MANA_OUTPUT.findall(oracle_lower)
        if symbols:
            colored = [s for s in symbols if s.upper() != "C"]
            if colored:
                produces_colored = True
            total_mana = max(1.0, len(symbols))

    # Land search produces colored mana (basics produce colored)
    if "search your library" in oracle_lower and "land" in oracle_lower:
        produces_colored = True
        total_mana = max(total_mana, 1.0)

    return (total_mana or 1.0, produces_colored)


def _score_ramp(
    card: dict,
    commander_colors: list[str],
    avg_cmc: float,
) -> float:
    """Score a ramp card on role-specific quality.

    Signals:
    - Net mana rate (output / CMC investment): primary signal
    - Conditionality penalty (0.4x): unreliable mana is barely mana
    - Restricted mana penalty (0.2x): "spend only on..." heavily penalized
    - Color production bonus: colored mana > colorless
    - Resilience tier: land ramp > enchantment > artifact > creature
    - CVAR blend at 35%

    Args:
        card: Card dict with oracle_text, cmc, type_line, _cvar_score.
        commander_colors: Commander's color identity.
        avg_cmc: Target average CMC for the deck.

    Returns:
        Combined quality score (higher is better).
    """
    oracle = card.get("oracle_text") or ""
    cmc = float(card.get("cmc", 3) or 3)
    cvar = float(card.get("_cvar_score", 0.3) or 0.3)

    # --- Net mana rate ---
    mana_output, produces_colored = _estimate_mana_output(oracle)
    # Avoid division by zero; CMC 0 cards (e.g. Mox) get max rate
    effective_cmc = max(cmc, 0.5)
    net_rate = mana_output / effective_cmc  # e.g. Sol Ring: 2/1=2.0, Cultivate: 1/3=0.33

    # Scale to 0-3 range (Sol Ring ~2.0 is excellent, 0.33 is mediocre)
    role_score = min(net_rate * 1.5, 3.0)

    # --- Conditionality penalty ---
    if _CONDITIONAL_MANA.search(oracle):
        role_score *= 0.4

    # --- Restricted mana penalty ---
    if _RESTRICTED_MANA.search(oracle):
        role_score *= 0.2

    # --- Color production bonus ---
    if produces_colored:
        # More colors in commander = more value from colored mana
        if len(commander_colors) >= 3:
            role_score += 0.5
        else:
            role_score += 0.3

    # --- Resilience tier ---
    ramp_type = _classify_ramp_type(card)
    resilience_bonus = {
        "land_ramp": 0.3,   # Survives board wipes
        "other": 0.15,      # Enchantment ramp, sorceries
        "rock": 0.0,        # Artifacts — common, removable
        "dork": -0.1,       # Creatures — most fragile
    }
    role_score += resilience_bonus.get(ramp_type, 0.0)

    # --- CMC preference (ramp before commander) ---
    cmdr_cmc = avg_cmc + 1  # rough commander CMC estimate
    if cmc <= cmdr_cmc - 2:
        role_score += 0.2

    # --- Blend with CVAR (35% CVAR, 65% role-specific) ---
    # Normalize role_score to ~0-1 range for blending (cap at 3.0, /3)
    normalized_role = min(role_score / 3.0, 1.0)
    final_score = 0.65 * normalized_role + 0.35 * cvar

    return final_score


class RampPackageGenerator:
    """Generate the ramp package for a deck."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def generate(
        self,
        color_identity: list[str],
        target_count: int,
        budget_remaining: float,
        template: DeckTemplate,
        already_placed: list[dict],
        role_tag_pool: list[dict],
        commander_colors: list[str] | None = None,
        avg_cmc: float | None = None,
    ) -> list[SlotAssignment]:
        """Generate ramp package with auto-includes and role-specific scoring.

        Args:
            color_identity: Commander's color identity.
            target_count: Target number of ramp cards.
            budget_remaining: Remaining deck budget.
            template: Deck template for context.
            already_placed: Cards already in the deck.
            role_tag_pool: Pre-filtered cards with role_tags containing "ramp".
            commander_colors: Commander's color identity (defaults to color_identity).
            avg_cmc: Target average CMC (defaults to template value).

        Returns:
            List of SlotAssignment for ramp cards.
        """
        colors = commander_colors or color_identity
        deck_avg_cmc = avg_cmc or template.avg_cmc_target

        auto_includes = _load_auto_includes()
        used_names = {c.get("name", "") for c in already_placed}
        assignments: list[SlotAssignment] = []
        running_price = 0.0

        # Auto-includes: Sol Ring always
        auto_ramp_names: set[str] = set()
        for entry in auto_includes.get("always", []):
            if entry.get("role") == "ramp":
                auto_ramp_names.add(entry["name"])

        # Arcane Signet for multicolor
        if len(color_identity) >= 2:
            for entry in auto_includes.get("multicolor", []):
                if entry.get("role") == "ramp":
                    auto_ramp_names.add(entry["name"])

        # Place auto-includes from pool
        for card in role_tag_pool:
            name = card.get("name", "")
            if name in auto_ramp_names and name not in used_names:
                price = float(card.get("price_usd", 0) or 0)
                if budget_remaining <= 0 or running_price + price <= budget_remaining:
                    assignments.append(SlotAssignment(
                        card=card,
                        slot_role="ramp",
                        score=0.95,
                        alternatives=[],
                    ))
                    used_names.add(name)
                    running_price += price
                    auto_ramp_names.discard(name)

        # Score remaining candidates with role-specific function
        candidates: list[tuple[dict, float]] = []
        for card in role_tag_pool:
            name = card.get("name", "")
            if name in used_names:
                continue

            score = _score_ramp(card, colors, deck_avg_cmc)

            # Budget awareness: prefer $0.25-$2 range
            price = float(card.get("price_usd", 0) or 0)
            if 0.25 <= price <= 2.0:
                score += 0.02
            elif price > 5.0:
                score -= 0.02

            candidates.append((card, score))

        candidates.sort(key=lambda x: x[1], reverse=True)

        # Diversify: track ramp subtypes
        type_counts = {"rock": 0, "land_ramp": 0, "dork": 0, "other": 0}

        for card, score in candidates:
            if len(assignments) >= target_count:
                break

            name = card.get("name", "")
            if name in used_names:
                continue

            price = float(card.get("price_usd", 0) or 0)
            if budget_remaining > 0 and running_price + price > budget_remaining:
                continue

            # Classify ramp type
            ramp_type = _classify_ramp_type(card)

            # Soft cap on each type for diversity
            cap = max(3, target_count // 2)
            if type_counts.get(ramp_type, 0) >= cap:
                continue

            assignments.append(SlotAssignment(
                card=card,
                slot_role="ramp",
                score=round(score, 4),
                alternatives=[],
            ))
            used_names.add(name)
            running_price += price
            type_counts[ramp_type] = type_counts.get(ramp_type, 0) + 1

        logger.info(
            "Ramp generator: %d ramp cards (target %d), types: %s",
            len(assignments), target_count, type_counts,
        )
        return assignments


def _classify_ramp_type(card: dict) -> str:
    """Classify ramp into rock/land_ramp/dork/other."""
    type_line = (card.get("type_line") or "").lower()
    oracle = (card.get("oracle_text") or "").lower()

    if "creature" in type_line and ("add" in oracle or "mana" in oracle):
        return "dork"
    if "artifact" in type_line:
        return "rock"
    if "search your library" in oracle and "land" in oracle:
        return "land_ramp"
    if "put" in oracle and "land" in oracle and "battlefield" in oracle:
        return "land_ramp"
    return "other"
