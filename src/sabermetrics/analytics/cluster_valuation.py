"""Per-cluster card valuation (Phase 4).

Given a commander's decks split into macro-archetype clusters (Phase 3), compute
how often each card appears WITHIN each cluster — never pooled across the whole
commander pool. Pooling across incompatible sub-strategies (e.g. Korvold
aristocrats vs landfall) produces a structurally hollow "average" deck; a card
that is a 90%-staple of one variant and absent from another averages to a
meaningless ~45%.

Statistical honesty is enforced, per the plan:
  - Every inclusion rate carries a Wilson 95% confidence interval.
  - At cluster sizes n≈20-40, mid-range rates (roughly 30-70%) have margins of
    error of ~±15-20% and are flagged LOW-CONFIDENCE. Only near-0% / near-100%
    inclusion is reliable at this N, and only those drive "staple" claims.
  - "Distinctive" cards (much higher inclusion in one cluster than the rest)
    are the empirical sub-archetype fingerprint — reported, but subject to the
    same small-N caveat.

This module does NOT attempt frequentist sub-variant detection below the cluster
level — N is too low. Qualitative variant characterization is an optional LLM
reasoning pass layered on top (treated as a hypothesis, not a statistic).
"""

from __future__ import annotations

import logging
from math import sqrt
from pathlib import Path

from pydantic import BaseModel, Field

from sabermetrics.analytics.archetype_signatures import load_library
from sabermetrics.analytics.deck_clustering import (
    build_feature_matrix,
    cluster_decks,
    load_commander_decks,
    name_clusters,
    select_k,
)

logger = logging.getLogger(__name__)

# Basic lands carry no valuation signal (present in ~every deck).
_BASICS = {
    "plains", "island", "swamp", "mountain", "forest", "wastes",
    "snow-covered plains", "snow-covered island", "snow-covered swamp",
    "snow-covered mountain", "snow-covered forest", "snow-covered wastes",
}


class CardInclusion(BaseModel):
    """One card's inclusion statistics within a cluster."""

    card_name: str
    count: int
    cluster_size: int
    inclusion_rate: float
    ci_low: float
    ci_high: float
    margin_of_error: float
    reliable: bool          # CI tight enough to trust the point estimate
    lift_vs_rest: float     # inclusion here minus inclusion in all other clusters


class ClusterValuation(BaseModel):
    """Valuation of one cluster: its confident staples and distinctive cards."""

    cluster_id: int
    dominant_archetype: str
    size: int
    meets_floor: bool
    staples: list[CardInclusion] = Field(default_factory=list)       # high + reliable
    distinctive: list[CardInclusion] = Field(default_factory=list)   # high lift vs rest
    low_confidence_count: int = 0   # cards whose rate is present but untrustworthy


class CommanderValuation(BaseModel):
    """Full per-cluster valuation for a commander."""

    commander: str
    n_decks: int
    k: int
    floor: int
    clusters: list[ClusterValuation]
    caveats: list[str] = Field(default_factory=list)


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score confidence interval for a binomial proportion.

    Preferred over the normal approximation at small n and near 0/1, where it
    stays inside [0, 1] and does not collapse to a zero-width interval.

    Args:
        k: Successes (decks containing the card).
        n: Trials (cluster size).
        z: Z-score (1.96 = 95%).

    Returns:
        ``(low, high)`` bounds in [0, 1].
    """
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return max(0.0, center - half), min(1.0, center + half)


def _presence_counts(card_lists: list[list[str]]) -> dict[str, int]:
    """Count decks containing each card (presence, not quantity)."""
    counts: dict[str, int] = {}
    for cards in card_lists:
        for name in set(cards):
            if name.split("//")[0].strip().casefold() in _BASICS:
                continue
            counts[name] = counts.get(name, 0) + 1
    return counts


def compute_cluster_valuation(
    db_path: Path,
    commander: str,
    k: int | None = None,
    floor: int = 20,
    moe_threshold: float = 0.15,
    staple_min_inclusion: float = 0.75,
    distinctive_min_lift: float = 0.25,
    distinctive_min_inclusion: float = 0.40,
    normalize: bool = True,
    seed: int = 0,
    source: str = "archidekt",
) -> CommanderValuation:
    """Compute per-cluster inclusion valuation for a commander.

    Args:
        db_path: SQLite database path.
        commander: Commander id or (partial) name.
        k: Cluster count; if None, floor-aware auto-selection.
        floor: Minimum decks per cluster for validity.
        moe_threshold: Max Wilson margin-of-error for a rate to be "reliable".
        staple_min_inclusion: Min inclusion for a reliable card to be a staple.
        distinctive_min_lift: Min (this-cluster minus rest) inclusion gap.
        distinctive_min_inclusion: Min inclusion here to qualify as distinctive.
        normalize: L2-normalize deck vectors for clustering.
        seed: RNG/model seed.
        source: Deck source.

    Returns:
        A :class:`CommanderValuation`.
    """
    library = load_library()
    decks = load_commander_decks(db_path, commander, source=source)
    caveats: list[str] = []

    if len(decks) < 2:
        return CommanderValuation(
            commander=commander, n_decks=len(decks), k=0, floor=floor,
            clusters=[], caveats=[f"Only {len(decks)} decks — cannot value."],
        )

    features, archetype_names = build_feature_matrix(decks, library, normalize)
    if k is None:
        k, _rationale = select_k(features, floor=floor, seed=seed)
    labels, model = cluster_decks(features, k, seed=seed)
    centroid_ranks = name_clusters(model, archetype_names)

    # Group deck card-lists by cluster.
    by_cluster: dict[int, list[list[str]]] = {c: [] for c in range(model.n_clusters)}
    for deck, lbl in zip(decks, labels):
        by_cluster[int(lbl)].append(deck.card_names)

    clusters: list[ClusterValuation] = []
    for cid in range(model.n_clusters):
        members = by_cluster[cid]
        size = len(members)
        counts = _presence_counts(members)

        # Inclusion in every OTHER cluster pooled (for lift).
        rest_lists = [
            cards for oc, lists in by_cluster.items() if oc != cid for cards in lists
        ]
        rest_counts = _presence_counts(rest_lists)
        rest_size = len(rest_lists)

        cards: list[CardInclusion] = []
        low_conf = 0
        for name, count in counts.items():
            rate = count / size if size else 0.0
            lo, hi = wilson_interval(count, size)
            moe = (hi - lo) / 2
            reliable = moe <= moe_threshold
            rest_rate = (rest_counts.get(name, 0) / rest_size) if rest_size else 0.0
            cards.append(CardInclusion(
                card_name=name, count=count, cluster_size=size,
                inclusion_rate=round(rate, 3),
                ci_low=round(lo, 3), ci_high=round(hi, 3),
                margin_of_error=round(moe, 3), reliable=reliable,
                lift_vs_rest=round(rate - rest_rate, 3),
            ))
            if not reliable:
                low_conf += 1

        staples = sorted(
            (c for c in cards if c.reliable and c.inclusion_rate >= staple_min_inclusion),
            key=lambda c: c.inclusion_rate, reverse=True,
        )
        distinctive = sorted(
            (
                c for c in cards
                if c.inclusion_rate >= distinctive_min_inclusion
                and c.lift_vs_rest >= distinctive_min_lift
            ),
            key=lambda c: c.lift_vs_rest, reverse=True,
        )

        top = centroid_ranks[cid]
        clusters.append(ClusterValuation(
            cluster_id=cid,
            dominant_archetype=top[0][0],
            size=size,
            meets_floor=size >= floor,
            staples=staples,
            distinctive=distinctive[:15],
            low_confidence_count=low_conf,
        ))

    clusters.sort(key=lambda c: c.size, reverse=True)

    caveats.append(
        "Inclusion is computed PER CLUSTER, not pooled. At these cluster sizes, "
        "mid-range rates (~30-70%) carry ~±15-20% margins — only reliable-flagged "
        "cards (tight CI, typically near-0%/near-100%) should be treated as settled."
    )
    small = [c for c in clusters if not c.meets_floor]
    if small:
        caveats.append(
            "Below-floor clusters "
            f"({', '.join(f'{c.dominant_archetype}(n={c.size})' for c in small)}) "
            "yield especially wide intervals — treat their valuation as provisional."
        )

    return CommanderValuation(
        commander=commander, n_decks=len(decks), k=k, floor=floor,
        clusters=clusters, caveats=caveats,
    )


def format_valuation(valuation: CommanderValuation, top_n: int = 12) -> str:
    """Render a :class:`CommanderValuation` as readable text."""
    lines = [
        f"=== Per-cluster card valuation: {valuation.commander} ===",
        f"decks: {valuation.n_decks}   clusters (k): {valuation.k}   "
        f"floor: {valuation.floor}",
    ]
    for c in valuation.clusters:
        floor_flag = "" if c.meets_floor else "  [BELOW FLOOR — provisional]"
        lines.append("")
        lines.append(
            f"── cluster {c.cluster_id}: {c.dominant_archetype} "
            f"(n={c.size}){floor_flag}"
        )
        lines.append(
            f"   {len(c.staples)} reliable staples, "
            f"{c.low_confidence_count} cards at low-confidence inclusion"
        )
        if c.staples:
            lines.append("   confident staples (inclusion [95% CI]):")
            for s in c.staples[:top_n]:
                lines.append(
                    f"     {int(s.inclusion_rate * 100):>3}% "
                    f"[{int(s.ci_low * 100)}-{int(s.ci_high * 100)}]  {s.card_name}"
                )
        if c.distinctive:
            lines.append("   distinctive vs other clusters (inclusion / +lift):")
            for d in c.distinctive[:top_n]:
                lines.append(
                    f"     {int(d.inclusion_rate * 100):>3}% "
                    f"(+{int(d.lift_vs_rest * 100)})  {d.card_name}"
                    f"{'' if d.reliable else '  ~low-conf'}"
                )
    if valuation.caveats:
        lines.append("")
        for cav in valuation.caveats:
            lines.append(f"⚠ {cav}")
    return "\n".join(lines)
