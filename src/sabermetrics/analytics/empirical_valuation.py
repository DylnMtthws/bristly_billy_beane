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


class EmpiricalComposition(BaseModel):
    """Median deck composition of one target variant's real decks.

    Medians, not means: the plausibility window keeps 50-110 card decks, and
    the stragglers skew means (Eriette: mean lands 32.3 vs median 36).
    """

    lands: int
    enchantments: int          # non-land enchantments
    creatures: int
    artifacts: int
    auras: int
    avg_cmc: float             # median of per-deck avg non-land CMC
    # Median fraction of deck value spent on lands in the variant's real
    # decks. Caps the land generator's budget so B2's price-neutral scoring
    # can't blow half the budget on premium mana (the $45 Gemstone Caverns).
    land_budget_share: float = 0.0


class EmpiricalInclusion(BaseModel):
    """Per-card inclusion for one target variant of a commander."""

    commander: str
    variant: str
    variant_size: int
    n_decks: int
    inclusion: dict[str, float] = Field(default_factory=dict)   # name_lower -> rate
    reliable: set[str] = Field(default_factory=set)             # tight-CI names
    composition: EmpiricalComposition | None = None

    def rate(self, card_name: str) -> float:
        """Inclusion rate for a card (0.0 if unseen — neutral, never negative)."""
        return self.inclusion.get(card_name.lower(), 0.0)


def compute_composition(
    per_deck_rows: dict[str, list[tuple[str, float, int]]],
) -> EmpiricalComposition | None:
    """Median composition across decks from (type_line, cmc, quantity) rows.

    Args:
        per_deck_rows: deck_id -> list of (type_line, cmc, quantity) tuples.

    Returns:
        Median composition, or None when there are no decks.
    """
    import statistics

    lands, enchs, creatures, artifacts, auras, cmcs = [], [], [], [], [], []
    land_shares = []
    for rows in per_deck_rows.values():
        n_land = n_ench = n_crea = n_art = n_aura = 0
        cmc_sum = 0.0
        cmc_n = 0
        land_spend = total_spend = 0.0
        for row in rows:
            type_line, cmc, qty = row[0], row[1], row[2]
            price = float(row[3]) if len(row) > 3 else 0.0
            tl = (type_line or "").lower()
            total_spend += price * qty
            if "land" in tl:
                n_land += qty
                land_spend += price * qty
                continue
            if "enchantment" in tl:
                n_ench += qty
            if "creature" in tl:
                n_crea += qty
            if "artifact" in tl:
                n_art += qty
            if "aura" in tl:
                n_aura += qty
            cmc_sum += float(cmc or 0) * qty
            cmc_n += qty
        lands.append(n_land)
        enchs.append(n_ench)
        creatures.append(n_crea)
        artifacts.append(n_art)
        auras.append(n_aura)
        cmcs.append(cmc_sum / cmc_n if cmc_n else 0.0)
        if total_spend > 0:
            land_shares.append(land_spend / total_spend)

    if not lands:
        return None
    return EmpiricalComposition(
        lands=round(statistics.median(lands)),
        enchantments=round(statistics.median(enchs)),
        creatures=round(statistics.median(creatures)),
        artifacts=round(statistics.median(artifacts)),
        auras=round(statistics.median(auras)),
        avg_cmc=round(statistics.median(cmcs), 2),
        land_budget_share=round(
            statistics.median(land_shares), 3
        ) if land_shares else 0.0,
    )


def _load_composition(db_path: Path, deck_ids: list[str]) -> EmpiricalComposition | None:
    """Load quantity-aware composition rows for the given decks and reduce.

    Queried from deck_cards directly rather than DeckRecord.card_names because
    the record drops quantities -- a deck's 10 Swamps would count as 1 land.
    """
    import sqlite3

    if not deck_ids:
        return None
    conn = sqlite3.connect(str(db_path))
    try:
        placeholders = ",".join("?" * len(deck_ids))
        rows = conn.execute(
            "SELECT dc.deck_id, ca.type_line, ca.cmc, dc.quantity, "
            "COALESCE(cp.price_usd, 0) "
            f"FROM deck_cards dc JOIN cards ca ON dc.card_id = ca.id "
            "LEFT JOIN card_prices cp ON cp.card_id = ca.id "
            "AND cp.snapshot_date = (SELECT MAX(snapshot_date) FROM card_prices) "
            f"WHERE dc.deck_id IN ({placeholders})",
            deck_ids,
        ).fetchall()
    except sqlite3.OperationalError as e:
        # Composition is supplementary; degrade to None rather than sinking
        # the whole inclusion signal (e.g. tests with a stub DB).
        logger.warning("Composition load failed, degrading: %s", e)
        return None
    finally:
        conn.close()

    per_deck: dict[str, list[tuple[str, float, int, float]]] = {}
    for deck_id, type_line, cmc, qty, price in rows:
        per_deck.setdefault(deck_id, []).append(
            (type_line or "", float(cmc or 0), int(qty or 1), float(price or 0))
        )
    return compute_composition(per_deck)


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


def annotate_empirical(cards: list[dict], source: list[dict]) -> None:
    """Carry empirical annotations from ``source`` onto ``cards`` by card name.

    The Stage 4 generators load their pre-scored candidate-table cards fresh
    from SQL, so those dicts lack the ``_empirical_inclusion`` /
    ``_empirical_reliable`` fields that ``deck_builder._structural_score`` set
    on the deck's candidate pool. This copies them over by name so
    :func:`empirical_bonus` works on the candidate-table path too, not only the
    role-tag fallback. Cards with no match are left neutral (rate 0.0, no
    bonus), preserving absence-neutrality (ADR-005).

    Mutates ``cards`` in place.

    Args:
        cards: Card dicts to annotate (e.g. candidate-table rows).
        source: Card dicts already carrying the empirical fields.
    """
    lookup = {
        c.get("name", ""): (
            float(c.get("_empirical_inclusion", 0.0) or 0.0),
            bool(c.get("_empirical_reliable", False)),
        )
        for c in source
    }
    for card in cards:
        rate, reliable = lookup.get(card.get("name", ""), (0.0, False))
        card["_empirical_inclusion"] = rate
        card["_empirical_reliable"] = reliable


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
    member_ids: dict[int, list[str]] = {}
    for deck, lbl in zip(decks, labels):
        members.setdefault(int(lbl), []).append(deck.card_names)
        member_ids.setdefault(int(lbl), []).append(deck.deck_id)
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

    composition = _load_composition(db_path, member_ids.get(target, []))

    variant = cluster_archetype.get(target, "mixed")
    logger.info(
        "[empirical] '%s': variant='%s' (%d/%d decks), %d cards, "
        "reliable=%s (worst-case margin %.3f vs %.2f bar), composition=%s",
        commander, variant, size, len(decks), len(inclusion),
        variant_reliable, (hi - lo) / 2, moe_threshold,
        composition.model_dump() if composition else None,
    )
    return EmpiricalInclusion(
        commander=commander, variant=variant, variant_size=size,
        n_decks=len(decks), inclusion=inclusion, reliable=reliable,
        composition=composition,
    )
