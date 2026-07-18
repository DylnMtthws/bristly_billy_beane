"""Ramp package generator (6.5.4).

Deterministic ramp selection: Sol Ring always, Arcane Signet for multicolor,
diversify rocks vs land-ramp vs dorks, sorted by role-specific quality score.
"""

import json
import logging
import re
import sqlite3
from pathlib import Path

import yaml

from sabermetrics.analytics.empirical_valuation import empirical_bonus
from sabermetrics.config import settings
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


def _load_auto_includes() -> tuple[dict, set[str]]:
    """Load auto-include cards from config.

    Returns:
        Tuple of (auto_includes_dict, protected_names_set).
    """
    config_path = Path(__file__).resolve().parent.parent.parent.parent.parent / "config" / "auto_include_cards.yaml"
    if not config_path.exists():
        return {}, set()
    with open(config_path) as f:
        data = yaml.safe_load(f) or {}
    # Collect names with protect_from_swap: true
    protected: set[str] = set()
    for section_entries in data.values():
        if not isinstance(section_entries, list):
            continue
        for entry in section_entries:
            if entry.get("protect_from_swap", False):
                protected.add(entry["name"])
    return data, protected


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

    # --- Empirical grounding: additive, never penalizes absence (ADR-005) ---
    final_score += empirical_bonus(
        card,
        settings.scoring.generator_empirical_weight,
        settings.scoring.generator_empirical_noisy_weight,
    )

    return final_score


class RampPackageGenerator:
    """Generate the ramp package for a deck."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.protected_names: set[str] = set()

    def _load_ramp_candidates(
        self,
        color_identity: list[str],
    ) -> list[dict]:
        """Load pre-scored ramp candidates from the ramp_candidates table.

        Joins with cards table to get full card data. Filters by color identity
        and Commander legality.

        Args:
            color_identity: Commander's color identity.

        Returns:
            List of card dicts augmented with ramp_score from ramp_candidates.
        """
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row

            # Build color identity filter
            color_set = set(color_identity)
            cursor = conn.execute(
                "SELECT c.id, c.name, c.oracle_text, c.type_line, c.cmc, "
                "c.color_identity, c.mana_cost, c.role_tags, c.keywords, "
                "r.ramp_type, r.ramp_score, r.produces_colored, r.is_conditional, "
                "r.net_mana_rate, r.resilience_tier "
                "FROM ramp_candidates r "
                "JOIN cards c ON r.card_id = c.id "
                "WHERE c.is_legal_in_99 = 1 "
                "AND r.is_restricted = 0 "
                "ORDER BY r.ramp_score DESC"
            )

            results: list[dict] = []
            for row in cursor:
                card = dict(row)
                # Filter by color identity
                card_colors_raw = card.get("color_identity") or "[]"
                if isinstance(card_colors_raw, str):
                    try:
                        card_colors = json.loads(card_colors_raw)
                    except (json.JSONDecodeError, TypeError):
                        card_colors = []
                else:
                    card_colors = card_colors_raw
                if not all(c in color_set for c in card_colors):
                    continue

                # Get latest price
                price_row = conn.execute(
                    "SELECT price_usd FROM card_prices "
                    "WHERE card_id = ? ORDER BY snapshot_date DESC LIMIT 1",
                    (card["id"],),
                ).fetchone()
                card["price_usd"] = price_row["price_usd"] if price_row else 0.0

                results.append(card)

            conn.close()
            return results
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            logger.warning("Failed to load ramp_candidates: %s", e)
            return []

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

        Prefers the ramp_candidates table (pre-scored, reminder-text-stripped)
        over the role_tag_pool. Falls back to role_tag_pool if the table is
        empty or unavailable.

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

        auto_includes, self.protected_names = _load_auto_includes()
        used_names = {c.get("name", "") for c in already_placed}
        assignments: list[SlotAssignment] = []
        running_price = 0.0

        # Collect auto-includes in priority order (highest priority first).
        # Protected cards get priority 0 (always kept), others get 1-3.
        auto_ramp_entries: list[tuple[str, int]] = []  # (name, priority)
        _sections_in_order = [
            ("always", True),
            ("multicolor", len(color_identity) >= 2),
            ("three_plus_colors", len(color_identity) >= 3),
            ("has_green", "G" in color_identity),
        ]
        for section, condition in _sections_in_order:
            if not condition:
                continue
            for entry in auto_includes.get(section, []):
                if entry.get("role") == "ramp":
                    is_protected = entry.get("protect_from_swap", False)
                    priority = 0 if is_protected else len(auto_ramp_entries)
                    auto_ramp_entries.append((entry["name"], priority))

        # Sort by priority (protected first, then order of appearance)
        auto_ramp_entries.sort(key=lambda x: x[1])

        # Cap at target_count — protected cards always fit, drop lowest priority
        auto_ramp_names: list[str] = [name for name, _ in auto_ramp_entries]
        if len(auto_ramp_names) > target_count:
            logger.info(
                "Capping ramp auto-includes from %d to %d (target_count)",
                len(auto_ramp_names), target_count,
            )
            auto_ramp_names = auto_ramp_names[:target_count]
        auto_ramp_set = set(auto_ramp_names)

        # Try loading ramp_candidates table
        ramp_candidates = self._load_ramp_candidates(color_identity)
        use_candidates_table = len(ramp_candidates) > 0

        # Combine: use ramp_candidates as primary source, role_tag_pool as fallback
        if use_candidates_table:
            pool = ramp_candidates
            logger.info("Using ramp_candidates table (%d cards)", len(pool))
        else:
            pool = role_tag_pool
            logger.info("Falling back to role_tag_pool (%d cards)", len(pool))

        # Place auto-includes from pool (or role_tag_pool as backup)
        search_pools = [pool] if use_candidates_table else [role_tag_pool]
        if use_candidates_table:
            search_pools.append(role_tag_pool)  # Fallback for auto-includes not in table

        for search_pool in search_pools:
            for card in search_pool:
                name = card.get("name", "")
                if name in auto_ramp_set and name not in used_names:
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
                        auto_ramp_set.discard(name)

        # Score remaining candidates with role-specific function
        candidates: list[tuple[dict, float]] = []
        for card in pool:
            name = card.get("name", "")
            if name in used_names:
                continue

            # Use pre-computed ramp_score if available, otherwise compute
            if "ramp_score" in card and card["ramp_score"] is not None:
                score = float(card["ramp_score"])
            else:
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
            ramp_type = card.get("ramp_type") or _classify_ramp_type(card)

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
            "Ramp generator: %d ramp cards (target %d), types: %s, protected: %s",
            len(assignments), target_count, type_counts, self.protected_names,
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
