"""Synergy-aware greedy deck optimizer.

Two-phase optimization:
1. Greedy fill — select cards by marginal contribution to the deck
2. Swap refinement — improve deck by swapping cards if objective improves

Cards are evaluated by how they interact with cards already in the deck,
not independently. The deck_objective function measures deck-level quality
across synergy density, role coverage, average CVAR, and curve coherence.
"""

import json
import logging
from math import log

import numpy as np
from pydantic import BaseModel, Field

from sabermetrics.analytics.role_targets import RoleTarget, role_need_multiplier
from sabermetrics.analytics.synergy_matrix import SynergyMatrix
from sabermetrics.models.template import DeckTemplate
from sabermetrics.pipeline.slot_assigner import SlotAssignment

logger = logging.getLogger(__name__)


class OptimizerResult(BaseModel):
    """Result of greedy optimization."""

    assignments: list[SlotAssignment]
    objective_score: float
    role_coverage: dict[str, dict]
    passes_used: int
    cards_swapped: int


def greedy_fill(
    shell: list[SlotAssignment],
    candidates: list[dict],
    synergy: SynergyMatrix,
    role_targets: dict[str, RoleTarget],
    budget_remaining: float,
    slots_remaining: int,
) -> list[SlotAssignment]:
    """Fill remaining slots by marginal contribution to deck.

    For each open slot, scores every remaining candidate by:
    - synergy_contrib: mean synergy with cards already in deck
    - role_mult: how urgently the deck needs this card's roles
    - cvar_base: standalone card quality

    Args:
        shell: Infrastructure cards already placed.
        candidates: All candidate cards (including those in shell).
        synergy: Precomputed pairwise synergy matrix.
        role_targets: Per-role reliability targets.
        budget_remaining: Dollars left to spend.
        slots_remaining: How many differentiator slots to fill.

    Returns:
        List of SlotAssignments for the differentiator slots.
    """
    # Track what's already in deck
    deck_names: set[str] = {a.card.get("name", "") for a in shell}
    deck_indices: list[int] = []
    for a in shell:
        idx = synergy.card_id_to_index.get(a.card.get("id", ""))
        if idx is not None:
            deck_indices.append(idx)

    # Count current roles from shell
    role_counts: dict[str, int] = {}
    for a in shell:
        roles = _get_card_roles(a.card)
        for r in roles:
            role_counts[r] = role_counts.get(r, 0) + 1

    # Filter eligible candidates
    eligible = [
        c for c in candidates
        if c.get("name", "") not in deck_names
        and "land" not in (c.get("type_line") or "").lower()
    ]

    assignments: list[SlotAssignment] = []
    budget_left = budget_remaining

    for _ in range(slots_remaining):
        if not eligible:
            break

        best_card = None
        best_score = -1.0
        best_idx = -1

        for ci, card in enumerate(eligible):
            price = float(card.get("price_usd", 0) or 0)
            if price > budget_left:
                continue

            card_id = card.get("id", "")
            card_idx = synergy.card_id_to_index.get(card_id)

            # Synergy contribution: mean synergy with current deck
            synergy_contrib = 0.0
            if card_idx is not None and deck_indices:
                syn_scores = synergy.matrix[card_idx, deck_indices]
                synergy_contrib = float(np.mean(syn_scores))

            # Role need multiplier
            card_roles = _get_card_roles(card)
            role_mult = 1.0
            if card_roles:
                mults = []
                for r in card_roles:
                    target = role_targets.get(r)
                    if target:
                        mults.append(role_need_multiplier(
                            role_counts.get(r, 0), target.target_count
                        ))
                if mults:
                    role_mult = max(mults)

            cvar_base = card.get("_cvar_score", 0.0)

            # Marginal value formula
            marginal = (
                0.45 * synergy_contrib
                + 0.35 * (role_mult * cvar_base)
                + 0.20 * cvar_base
            )

            if marginal > best_score:
                best_score = marginal
                best_card = card
                best_idx = ci

        if best_card is None:
            break

        # Add to deck
        card_id = best_card.get("id", "")
        card_idx = synergy.card_id_to_index.get(card_id)
        if card_idx is not None:
            deck_indices.append(card_idx)

        card_roles = _get_card_roles(best_card)
        primary_role = card_roles[0] if card_roles else "utility"
        # Map to valid SlotRole
        valid_roles = {"ramp", "draw", "removal", "wincon", "utility", "land", "other"}
        if primary_role not in valid_roles:
            primary_role = "utility"

        for r in card_roles:
            role_counts[r] = role_counts.get(r, 0) + 1

        assignments.append(SlotAssignment(
            card=best_card,
            slot_role=primary_role,
            score=round(best_score, 4),
            alternatives=[],
        ))
        deck_names.add(best_card.get("name", ""))
        budget_left -= float(best_card.get("price_usd", 0) or 0)

        # Remove from eligible
        eligible.pop(best_idx)

    logger.info(
        "Greedy fill: %d cards placed, $%.2f budget remaining",
        len(assignments), budget_left,
    )
    return assignments


def swap_refine(
    deck: list[SlotAssignment],
    candidates: list[dict],
    synergy: SynergyMatrix,
    role_targets: dict[str, RoleTarget],
    budget: float,
    max_passes: int = 3,
    protect_lands: bool = True,
) -> tuple[list[SlotAssignment], int]:
    """Improve deck by swapping cards if objective improves.

    Infrastructure cards ARE eligible for swaps — e.g., a generic ramp
    card can be replaced by a synergy-relevant ramp card, as long as
    role coverage doesn't drop below target.

    Args:
        deck: Current full deck assignments.
        candidates: All available candidate cards.
        synergy: Precomputed synergy matrix.
        role_targets: Per-role reliability targets.
        budget: Total budget constraint.
        max_passes: Maximum swap passes.
        protect_lands: If True, land cards are not swapped.

    Returns:
        Tuple of (improved deck, total swaps made).
    """
    total_swaps = 0
    deck_names = {a.card.get("name", "") for a in deck}

    # Build pool of candidates not in deck
    swap_pool = [
        c for c in candidates
        if c.get("name", "") not in deck_names
        and "land" not in (c.get("type_line") or "").lower()
    ]

    for pass_num in range(max_passes):
        improved = False

        current_obj = deck_objective(
            [a.card for a in deck], synergy, role_targets,
        )

        for deck_idx in range(len(deck)):
            assignment = deck[deck_idx]

            # Skip lands if protected
            if protect_lands and assignment.slot_role == "land":
                continue
            if protect_lands and "land" in (
                assignment.card.get("type_line") or ""
            ).lower():
                continue

            current_card = assignment.card
            current_price = float(current_card.get("price_usd", 0) or 0)
            current_roles = _get_card_roles(current_card)

            # Check role minimums — would removing this card violate minimums?
            role_counts = _count_roles(deck)
            can_remove = True
            for r in current_roles:
                target = role_targets.get(r)
                if target and role_counts.get(r, 0) <= target.min_count:
                    can_remove = False
                    break
            if not can_remove:
                continue

            best_swap_card = None
            best_swap_obj = current_obj

            total_price = sum(
                float(a.card.get("price_usd", 0) or 0) for a in deck
            )

            for swap_card in swap_pool:
                swap_name = swap_card.get("name", "")
                if swap_name in deck_names:
                    continue

                swap_price = float(swap_card.get("price_usd", 0) or 0)
                new_total = total_price - current_price + swap_price
                if new_total > budget:
                    continue

                # Simulate swap
                old_card = deck[deck_idx].card
                swap_roles = _get_card_roles(swap_card)
                primary = swap_roles[0] if swap_roles else "utility"
                valid_roles = {"ramp", "draw", "removal", "wincon", "utility", "land", "other"}
                if primary not in valid_roles:
                    primary = "utility"

                deck[deck_idx] = SlotAssignment(
                    card=swap_card,
                    slot_role=primary,
                    score=0.0,
                    alternatives=[],
                )

                new_obj = deck_objective(
                    [a.card for a in deck], synergy, role_targets,
                )

                if new_obj > best_swap_obj + 0.001:  # Require meaningful improvement
                    best_swap_obj = new_obj
                    best_swap_card = swap_card

                # Restore original
                deck[deck_idx] = assignment

            if best_swap_card is not None:
                old_name = current_card.get("name", "")
                new_name = best_swap_card.get("name", "")
                swap_roles = _get_card_roles(best_swap_card)
                primary = swap_roles[0] if swap_roles else "utility"
                valid_roles = {"ramp", "draw", "removal", "wincon", "utility", "land", "other"}
                if primary not in valid_roles:
                    primary = "utility"

                deck[deck_idx] = SlotAssignment(
                    card=best_swap_card,
                    slot_role=primary,
                    score=round(best_swap_obj, 4),
                    alternatives=[],
                )
                deck_names.discard(old_name)
                deck_names.add(new_name)
                total_swaps += 1
                improved = True

                logger.info(
                    "Swap pass %d: %s → %s (obj %.4f → %.4f)",
                    pass_num + 1, old_name, new_name,
                    current_obj, best_swap_obj,
                )

        if not improved:
            logger.info("Swap refinement converged after %d passes", pass_num + 1)
            break

    return deck, total_swaps


def deck_objective(
    deck_cards: list[dict],
    synergy: SynergyMatrix,
    role_targets: dict[str, RoleTarget],
    template: DeckTemplate | None = None,
) -> float:
    """Deck-level objective function.

    Components (all 0-1 normalized):
      synergy_density (0.40): mean pairwise synergy among non-land cards
      role_coverage   (0.35): 1.0 minus penalty for roles below target
      avg_cvar        (0.15): mean CVAR of non-land cards
      curve_coherence (0.10): 1.0 minus divergence from template curve

    Args:
        deck_cards: List of card dicts in the deck.
        synergy: Precomputed synergy matrix.
        role_targets: Per-role reliability targets.
        template: Optional deck template for curve coherence.

    Returns:
        Objective score (higher is better), typically 0-1.
    """
    non_lands = [
        c for c in deck_cards
        if "land" not in (c.get("type_line") or "").lower()
    ]

    if not non_lands:
        return 0.0

    syn_density = _compute_synergy_density(non_lands, synergy)
    role_cov = _compute_role_coverage(deck_cards, role_targets)
    avg_cvar = _compute_avg_cvar(non_lands)
    curve_coh = _compute_curve_coherence(deck_cards, template) if template else 0.5

    return (
        0.40 * syn_density
        + 0.35 * role_cov
        + 0.15 * avg_cvar
        + 0.10 * curve_coh
    )


def _compute_synergy_density(
    non_land_cards: list[dict],
    synergy: SynergyMatrix,
) -> float:
    """Mean pairwise synergy among non-land cards."""
    indices = []
    for card in non_land_cards:
        idx = synergy.card_id_to_index.get(card.get("id", ""))
        if idx is not None:
            indices.append(idx)

    if len(indices) < 2:
        return 0.0

    # Extract submatrix and compute mean of upper triangle
    idx_arr = np.array(indices)
    submatrix = synergy.matrix[np.ix_(idx_arr, idx_arr)]
    n = len(indices)
    total = float(np.sum(np.triu(submatrix, k=1)))
    pairs = n * (n - 1) / 2
    return total / pairs if pairs > 0 else 0.0


def _compute_role_coverage(
    deck_cards: list[dict],
    role_targets: dict[str, RoleTarget],
) -> float:
    """1.0 minus weighted penalty for under-served roles."""
    role_counts: dict[str, int] = {}
    for card in deck_cards:
        roles = _get_card_roles(card)
        for r in roles:
            role_counts[r] = role_counts.get(r, 0) + 1

    if not role_targets:
        return 1.0

    total_penalty = 0.0
    total_weight = 0.0

    for role, target in role_targets.items():
        current = role_counts.get(role, 0)
        weight = 1.5 if target.is_engine_critical else 1.0
        total_weight += weight

        if current >= target.target_count:
            pass  # No penalty
        elif current >= target.min_count:
            # Partial penalty
            gap = (target.target_count - current) / max(target.target_count, 1)
            total_penalty += weight * gap * 0.5
        else:
            # Severe penalty — below minimum
            gap = (target.min_count - current) / max(target.min_count, 1)
            total_penalty += weight * (0.5 + gap * 0.5)

    if total_weight == 0:
        return 1.0

    penalty_normalized = total_penalty / total_weight
    return max(0.0, 1.0 - penalty_normalized)


def _compute_avg_cvar(non_land_cards: list[dict]) -> float:
    """Mean CVAR score of non-land cards, clamped to 0-1."""
    if not non_land_cards:
        return 0.0
    scores = [c.get("_cvar_score", 0.0) for c in non_land_cards]
    return min(1.0, sum(scores) / len(scores))


def _compute_curve_coherence(
    deck_cards: list[dict],
    template: DeckTemplate | None,
) -> float:
    """1.0 minus normalized KL-divergence from ideal CMC distribution."""
    if not template or not template.curve_shape:
        return 0.5

    # Actual CMC distribution
    actual: dict[int, int] = {}
    for card in deck_cards:
        if "land" in (card.get("type_line") or "").lower():
            continue
        cmc = int(float(card.get("cmc", 0) or 0))
        bucket = min(cmc, 7)
        actual[bucket] = actual.get(bucket, 0) + 1

    total_actual = sum(actual.values())
    total_ideal = sum(template.curve_shape.values())

    if total_actual == 0 or total_ideal == 0:
        return 0.5

    # KL divergence: sum(p * log(p/q)) with smoothing
    epsilon = 0.01
    kl = 0.0
    for bucket in range(8):
        p = (actual.get(bucket, 0) + epsilon) / (total_actual + 8 * epsilon)
        q = (template.curve_shape.get(bucket, 0) + epsilon) / (
            total_ideal + 8 * epsilon
        )
        kl += p * log(p / q)

    # Normalize: KL can be unbounded, but typical values 0-2
    coherence = max(0.0, 1.0 - kl / 2.0)
    return coherence


def _get_card_roles(card: dict) -> list[str]:
    """Extract role tags from a card dict."""
    rt_raw = card.get("role_tags", "[]")
    if isinstance(rt_raw, str):
        try:
            rt = json.loads(rt_raw)
        except (json.JSONDecodeError, TypeError):
            rt = []
    else:
        rt = rt_raw or []
    return rt if rt else ["utility"]


def _count_roles(deck: list[SlotAssignment]) -> dict[str, int]:
    """Count role tags across all cards in deck."""
    counts: dict[str, int] = {}
    for a in deck:
        roles = _get_card_roles(a.card)
        for r in roles:
            counts[r] = counts.get(r, 0) + 1
    return counts
