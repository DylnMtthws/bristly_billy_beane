"""Per-variant empirical inclusion for the deck generator (Phase 6 wire-in).

Turns the clustered, verified decklist corpus (Phases 2-5) into a card-scoring
signal for generation: for a commander and a target variant, how often does each
card actually appear in real popular decks of that variant?

Design guardrails (do not violate — this is the whole point):
  - This is BEHAVIORAL CORROBORATION, not authority (ADR-005). It boosts cards
    the community validates; it NEVER penalizes a card for being absent. The
    tool exists to find undervalued cards that popular decks under-run, so
    absence must stay neutral.
  - Inclusion is taken from ONE target variant (cluster), never pooled across
    incompatible variants (which produces a hollow "average" deck).
  - Reliability (tight Wilson CI) is tracked so the scorer can trust near-100%
    staples more than noisy mid-range rates.
  - Degrades to None when the commander has no usable corpus, so generation
    falls back cleanly to the existing signals.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from sabermetrics.analytics.archetype_signatures import load_library
from sabermetrics.analytics.cluster_valuation import _presence_counts, wilson_interval
from sabermetrics.analytics.deck_clustering import (
    build_feature_matrix,
    cluster_decks,
    load_commander_decks,
    name_clusters,
    select_k,
)

logger = logging.getLogger(__name__)


class EmpiricalInclusion(BaseModel):
    """Per-card inclusion for one target variant of a commander."""

    commander: str
    variant: str
    variant_size: int
    n_decks: int
    inclusion: dict[str, float] = Field(default_factory=dict)   # name_lower -> rate
    reliable: set[str] = Field(default_factory=set)             # tight-CI names

    def rate(self, card_name: str) -> float:
        """Inclusion rate for a card (0.0 if unseen — neutral, never negative)."""
        return self.inclusion.get(card_name.lower(), 0.0)


def empirical_bonus(
    card: dict, reliable_weight: float, noisy_weight: float
) -> float:
    """Additive selection bonus for a card's empirical inclusion.

    Reads ``_empirical_inclusion`` / ``_empirical_reliable`` from the card dict,
    set by ``deck_builder._structural_score`` when a decklist corpus exists.
    The single scoring rule shared by every selection stage (greedy fill and the
    Stage 4 role generators) so grounding is applied identically everywhere.

    The bonus is always non-negative and is 0.0 for a card with no corpus data,
    so absence never penalizes and every stage degrades cleanly without a corpus
    (ADR-005). Callers pass weights on their own score scale, since the
    generators blend on a 0–1 scale and greedy on the marginal-value scale.

    Args:
        card: Candidate card dict.
        reliable_weight: Multiplier when the rate has a tight Wilson CI.
        noisy_weight: Multiplier when the rate is mid-confidence.

    Returns:
        ``weight * rate``, or 0.0 when the card has no corpus data.
    """
    rate = float(card.get("_empirical_inclusion", 0.0) or 0.0)
    if rate <= 0.0:
        return 0.0
    weight = reliable_weight if card.get("_empirical_reliable") else noisy_weight
    return weight * rate


def _select_cluster(
    strategy: str | None,
    cluster_archetype: dict[int, str],
    sizes: dict[int, int],
) -> int:
    """Pick the target cluster: strategy-matched if possible, else the largest.

    Args:
        strategy: Free-text strategy hint (e.g. "aristocrats", "landfall").
        cluster_archetype: cluster id -> dominant archetype name.
        sizes: cluster id -> member count.

    Returns:
        The chosen cluster id.
    """
    if strategy:
        s = strategy.lower()
        matches = [cid for cid, arch in cluster_archetype.items() if arch in s or s in arch]
        if matches:
            return max(matches, key=lambda c: sizes.get(c, 0))
    return max(sizes, key=lambda c: sizes.get(c, 0))


def get_target_cluster_inclusion(
    db_path: Path,
    commander: str,
    strategy: str | None = None,
    min_decks: int = 20,
    moe_threshold: float = 0.15,
    seed: int = 0,
) -> EmpiricalInclusion | None:
    """Empirical inclusion for a commander's target variant, or None.

    Args:
        db_path: SQLite database path.
        commander: Commander id or (partial) name.
        strategy: Optional strategy hint used to pick the variant.
        min_decks: Minimum corpus size to produce a signal at all.
        moe_threshold: Wilson margin-of-error bar for reliability. Applied to
            the variant's worst-case (p=0.5) margin, not per card -- see below.
        seed: RNG/model seed (kept consistent with clustering).

    Returns:
        An :class:`EmpiricalInclusion`, or None if no usable corpus exists.
    """
    library = load_library()
    decks = load_commander_decks(db_path, commander)
    if len(decks) < min_decks:
        logger.info(
            "[empirical] '%s': %d decks (< %d) — no empirical signal",
            commander, len(decks), min_decks,
        )
        return None

    features, names = build_feature_matrix(decks, library, normalize=True)
    k, _rationale = select_k(features, floor=20, seed=seed)
    labels, model = cluster_decks(features, k, seed=seed)
    cluster_archetype = {
        cid: top[0][0] for cid, top in name_clusters(model, names).items()
    }

    members: dict[int, list[list[str]]] = {}
    for deck, lbl in zip(decks, labels):
        members.setdefault(int(lbl), []).append(deck.card_names)
    sizes = {cid: len(m) for cid, m in members.items()}

    target = _select_cluster(strategy, cluster_archetype, sizes)
    target_members = members[target]
    size = len(target_members)

    counts = _presence_counts(target_members)
    inclusion: dict[str, float] = {}
    for name, count in counts.items():
        inclusion[name.lower()] = round(count / size if size else 0.0, 3)

    # Reliability is a property of the VARIANT's sample size, not the per-card
    # CI width. At the variant sizes we see (~20-74 decks) the Wilson width is
    # dominated by n, not the rate: every mid-to-high rate has a ~0.13 half-width
    # at n=49, so a per-card width test cannot separate a 0.55 staple from 0.50
    # noise -- it only ever flagged the extremes, and was inert once n>=36.
    # Instead trust the whole variant when even its worst case (a 50/50 rate)
    # stays within the margin bar; below that the sample is too small to trust
    # any mid-range rate, so nothing is reliable. Uses the same moe_threshold,
    # so no separate magic constant.
    lo, hi = wilson_interval(round(0.5 * size), size)
    variant_reliable = size > 0 and (hi - lo) / 2 <= moe_threshold
    reliable: set[str] = set(inclusion) if variant_reliable else set()

    variant = cluster_archetype.get(target, "mixed")
    logger.info(
        "[empirical] '%s': variant='%s' (%d/%d decks), %d cards, "
        "reliable=%s (worst-case margin %.3f vs %.2f bar)",
        commander, variant, size, len(decks), len(inclusion),
        variant_reliable, (hi - lo) / 2, moe_threshold,
    )
    return EmpiricalInclusion(
        commander=commander, variant=variant, variant_size=size,
        n_decks=len(decks), inclusion=inclusion, reliable=reliable,
    )
