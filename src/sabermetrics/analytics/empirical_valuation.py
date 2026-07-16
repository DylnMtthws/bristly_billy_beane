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
        moe_threshold: Wilson margin-of-error below which a rate is "reliable".
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
    reliable: set[str] = set()
    for name, count in counts.items():
        rate = count / size if size else 0.0
        inclusion[name.lower()] = round(rate, 3)
        lo, hi = wilson_interval(count, size)
        if (hi - lo) / 2 <= moe_threshold:
            reliable.add(name.lower())

    variant = cluster_archetype.get(target, "mixed")
    logger.info(
        "[empirical] '%s': variant='%s' (%d/%d decks), %d cards, %d reliable",
        commander, variant, size, len(decks), len(inclusion), len(reliable),
    )
    return EmpiricalInclusion(
        commander=commander, variant=variant, variant_size=size,
        n_decks=len(decks), inclusion=inclusion, reliable=reliable,
    )
