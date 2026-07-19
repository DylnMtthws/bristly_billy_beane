"""Land package generator (6.5.4).

Wraps the existing Karsten mana base builder with auto-include logic.
"""

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


class LandPackageGenerator:
    """Generate the land package for a deck."""

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
        """Generate land package using Karsten mana base builder.

        Args:
            color_identity: Commander's color identity.
            target_count: Target number of lands.
            budget_remaining: Remaining deck budget.
            template: Deck template for context.
            already_placed: Cards already in the deck.
            role_tag_pool: Pre-filtered land candidates.

        Returns:
            List of SlotAssignment for lands.
        """
        from sabermetrics.pipeline.greedy_optimizer import is_playable_as_land
        from sabermetrics.pipeline.mana_base import build_mana_base

        # Invariant: total lands in the deck equal the template target no
        # matter which stage placed them. If an earlier stage placed a
        # land-typed card (a "ramp" Krosan Verge once caused a +2 overshoot),
        # this package fills that many fewer.
        lands_already = sum(
            1 for c in already_placed
            if is_playable_as_land(c.get("type_line") or "")
        )
        if lands_already:
            logger.info(
                "Land generator: %d land-typed cards already placed, "
                "reducing target %d -> %d",
                lands_already, target_count, target_count - lands_already,
            )
            target_count = max(0, target_count - lands_already)

        # Prepare auto-includes
        auto_includes = _load_auto_includes()
        auto_land_names: set[str] = set()

        # Multicolor auto-includes
        if len(color_identity) >= 2:
            for entry in auto_includes.get("multicolor", []):
                if entry.get("role") == "land":
                    auto_land_names.add(entry["name"])

        # 3+ color auto-includes
        if len(color_identity) >= 3:
            for entry in auto_includes.get("three_plus_colors", []):
                if entry.get("role") == "land":
                    auto_land_names.add(entry["name"])

        # Color-conditional auto-includes
        if "B" in color_identity:
            for entry in auto_includes.get("has_black", []):
                if entry.get("role") == "land":
                    auto_land_names.add(entry["name"])

        used_names = {c.get("name", "") for c in already_placed}

        # Separate auto-include candidates from the rest
        auto_assignments: list[SlotAssignment] = []
        remaining_pool: list[tuple[dict, dict]] = []

        for card in role_tag_pool:
            name = card.get("name", "")
            if name in used_names:
                continue
            if name in auto_land_names:
                auto_assignments.append(SlotAssignment(
                    card=card,
                    slot_role="land",
                    score=0.9,
                    alternatives=[],
                ))
                used_names.add(name)
                auto_land_names.discard(name)
            else:
                scoring = {
                    "cvar_score": card.get("_cvar_score", 0.3),
                    "slot_role": "land",
                }
                remaining_pool.append((card, scoring))

        # Delegate remaining slots to Karsten mana base builder
        spells = already_placed
        remaining_land_target = target_count - len(auto_assignments)

        if remaining_land_target > 0 and (remaining_pool or True):
            running_price = sum(
                float(c.get("price_usd", 0) or 0) for c in already_placed
            ) + sum(
                float(a.card.get("price_usd", 0) or 0) for a in auto_assignments
            )

            # build_mana_base's max_budget is a WHOLE-DECK budget it compares
            # against running_price + land spend; budget_remaining here is the
            # land-only allotment (capped at the corpus land-share upstream).
            # Passing it raw made spendable = allotment - infra_spend, which
            # went negative once infrastructure outspent the land cap -- a
            # Jund Korvold build got 33 basics and 3 nonbasics that way while
            # cheap-infra Eriette masked the bug.
            karsten_lands = build_mana_base(
                land_candidates=remaining_pool,
                spells=spells,
                commander_colors=color_identity,
                total_lands=remaining_land_target,
                max_budget=running_price + budget_remaining,
                running_price=running_price,
            )
            auto_assignments.extend(karsten_lands)

        logger.info(
            "Land generator: %d lands (%d auto-includes)",
            len(auto_assignments),
            sum(1 for a in auto_assignments if a.score >= 0.9),
        )

        return auto_assignments
