"""Ramp package generator (6.5.4).

Deterministic ramp selection: Sol Ring always, Arcane Signet for multicolor,
diversify rocks vs land-ramp vs dorks, sorted by CVAR within role_tags pool.
"""

import json
import logging
from pathlib import Path

import yaml

from sabermetrics.models.template import DeckTemplate
from sabermetrics.pipeline.slot_assigner import SlotAssignment

logger = logging.getLogger(__name__)


def _load_auto_includes() -> dict:
    """Load auto-include cards from config."""
    config_path = Path(__file__).resolve().parent.parent.parent.parent.parent / "config" / "auto_include_cards.yaml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


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
    ) -> list[SlotAssignment]:
        """Generate ramp package with auto-includes and budget awareness.

        Args:
            color_identity: Commander's color identity.
            target_count: Target number of ramp cards.
            budget_remaining: Remaining deck budget.
            template: Deck template for context.
            already_placed: Cards already in the deck.
            role_tag_pool: Pre-filtered cards with role_tags containing "ramp".

        Returns:
            List of SlotAssignment for ramp cards.
        """
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

        # Commander CMC constraint: prefer ramp at CMC <= commander_cmc - 2
        cmdr_cmc = template.avg_cmc_target + 1  # rough estimate

        # Sort remaining candidates by CVAR score
        candidates: list[tuple[dict, float]] = []
        for card in role_tag_pool:
            name = card.get("name", "")
            if name in used_names:
                continue
            cvar = card.get("_cvar_score", 0.3)
            cmc = float(card.get("cmc", 3) or 3)

            # Prefer cheaper ramp
            if cmc <= cmdr_cmc - 2:
                cvar += 0.1

            # Budget awareness: prefer $0.25-$2 range
            price = float(card.get("price_usd", 0) or 0)
            if 0.25 <= price <= 2.0:
                cvar += 0.05
            elif price > 5.0:
                cvar -= 0.05

            candidates.append((card, cvar))

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
