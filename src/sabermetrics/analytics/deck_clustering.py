"""Macro-archetype clustering within one commander's deck pool (Phase 3).

Each of a commander's ~100 popular decks is scored against the Phase 1
signature library (:mod:`analytics.archetype_signatures`), giving a position in
"archetype space". Decks are then clustered (k-means) to recover the
commander's real sub-strategies, and each cluster is named by the archetype its
centroid leans into.

Crucially, clusters are NOT trusted on sight. A bootstrap stability check
(resample the pool with replacement, recluster, compare via adjusted Rand
index) reports whether the split is real at this sample size. Low agreement =
the clusters are an artifact of ~100 decks, and downstream valuation should say
so rather than treat them as settled — exactly the discipline the plan requires.

Cluster sizes are checked against a floor (default 20): at a ~100-deck pool,
20-40 decks/cluster is the *minimum* for validity, not a comfortable number.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score

from sabermetrics.analytics.archetype_signatures import (
    ArchetypeLibrary,
    load_library,
    score_deck,
)

logger = logging.getLogger(__name__)


class DeckRecord(BaseModel):
    """One deck's cards plus the metadata clustering reports against."""

    deck_id: str
    card_names: list[str]
    popularity_rank: int | None = None
    creator_tags: list[str] = Field(default_factory=list)


class ClusterSummary(BaseModel):
    """One cluster's identity and membership."""

    cluster_id: int
    dominant_archetype: str
    size: int
    meets_floor: bool
    centroid_top: list[tuple[str, float]]  # top archetypes by centroid weight
    sample_deck_ids: list[str]


class StabilityResult(BaseModel):
    """Bootstrap adjusted-Rand-index stability of the clustering."""

    n_bootstrap: int
    mean_ari: float
    median_ari: float
    p05_ari: float
    p95_ari: float
    verdict: str  # "stable" | "moderate" | "not real at this N"


class ClusterReport(BaseModel):
    """Full Phase 3 clustering result for one commander."""

    commander: str
    n_decks: int
    k: int
    k_rationale: str = ""
    floor: int
    normalize: bool
    clusters: list[ClusterSummary]
    stability: StabilityResult | None
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_commander_decks(
    db_path: Path,
    commander: str,
    source: str = "archidekt",
    min_cards: int = 50,
    max_cards: int = 110,
) -> list[DeckRecord]:
    """Load a commander's decks (card names + metadata) from the DB.

    Only decks with a plausible resolved non-commander card count are kept. A
    real Commander deck has 99 non-commander cards, so:
      - below ``min_cards``: an incomplete/stub deck (e.g. a 1-card placeholder)
        that would deflate inclusion rates;
      - above ``max_cards``: a collection / theorycraft "deck" that piled cards
        into included categories, inflating inclusion (the maybeboard exclusion
        can't catch these because their bloat is in non-maybeboard categories).

    Args:
        db_path: SQLite database path.
        commander: Commander id or (partial) name.
        source: Deck source to load (default the Phase 2 ``archidekt`` corpus).
        min_cards: Minimum resolved non-commander cards for a deck to be kept.
        max_cards: Maximum resolved non-commander cards for a deck to be kept.

    Returns:
        One :class:`DeckRecord` per usable deck, non-commander card names included.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        deck_rows = conn.execute(
            "SELECT d.id AS id, d.popularity_rank AS rank, d.archetype_tags AS tags "
            "FROM decks d JOIN cards c ON d.commander_id = c.id "
            "WHERE d.source = ? AND (c.id = ? OR c.name LIKE ?) "
            "ORDER BY d.popularity_rank",
            (source, commander, f"%{commander}%"),
        ).fetchall()

        records: list[DeckRecord] = []
        for dr in deck_rows:
            card_rows = conn.execute(
                "SELECT c.name AS name FROM deck_cards dc "
                "JOIN cards c ON dc.card_id = c.id "
                "WHERE dc.deck_id = ? AND dc.is_commander = 0",
                (dr["id"],),
            ).fetchall()
            try:
                tags = json.loads(dr["tags"]) if dr["tags"] else []
            except (json.JSONDecodeError, TypeError):
                tags = []
            card_names = [r["name"] for r in card_rows]
            if not (min_cards <= len(card_names) <= max_cards):
                continue  # implausible size: stub or collection — skip
            records.append(DeckRecord(
                deck_id=dr["id"],
                card_names=card_names,
                popularity_rank=dr["rank"],
                creator_tags=tags,
            ))
        return records
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Feature construction
# ---------------------------------------------------------------------------


def build_feature_matrix(
    decks: list[DeckRecord],
    library: ArchetypeLibrary,
    normalize: bool = True,
) -> tuple[np.ndarray, list[str]]:
    """Build the deck × archetype-score feature matrix.

    Args:
        decks: Commander's decks.
        library: The archetype signature library.
        normalize: If True, L2-normalize each deck's vector so clustering keys
            on archetype *profile* (which archetypes) rather than deck size.
            All-zero rows (no signature cards) are left as zeros.

    Returns:
        ``(features, archetype_names)`` — an ``(n_decks, n_archetypes)`` array
        and the column order.
    """
    archetype_names = sorted(library.archetypes.keys())
    n = len(decks)
    a = len(archetype_names)
    features = np.zeros((n, a), dtype=np.float64)

    for i, deck in enumerate(decks):
        scores = score_deck(deck.card_names, library)
        for j, name in enumerate(archetype_names):
            features[i, j] = scores.get(name, 0.0)

    if normalize:
        norms = np.linalg.norm(features, axis=1, keepdims=True)
        norms = np.where(norms == 0.0, 1.0, norms)
        features = features / norms

    return features, archetype_names


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


def suggest_k(
    decks: list[DeckRecord], library: ArchetypeLibrary, min_support: int = 5
) -> int:
    """Suggest a cluster count from the pool's dominant-archetype spread.

    Counts archetypes that are the dominant label for at least ``min_support``
    decks, clamped to [2, 6]. A data-driven default when k is not supplied.

    Args:
        decks: Commander's decks.
        library: The archetype signature library.
        min_support: Minimum decks for an archetype to warrant its own cluster.

    Returns:
        Suggested k in [2, 6].
    """
    from collections import Counter

    from sabermetrics.analytics.archetype_signatures import classify_deck

    counts: Counter[str] = Counter()
    for deck in decks:
        dom = classify_deck(deck.card_names, library).dominant
        if dom:
            counts[dom] += 1
    k = sum(1 for _a, n in counts.items() if n >= min_support)
    return max(2, min(6, k))


def select_k(
    features: np.ndarray,
    floor: int,
    k_min: int = 2,
    k_max: int = 6,
    n_bootstrap: int = 50,
    seed: int = 0,
) -> tuple[int, str]:
    """Choose k by the floor-aware, stability-first rule.

    Preference order:
      1. Largest k that is bootstrap-STABLE *and* has every cluster at/above
         the validity floor (real, granular, and trustworthy).
      2. Else, the k whose clusters all clear the floor with the highest ARI.
      3. Else, the k with the highest ARI (report will still flag it).

    Args:
        features: Feature matrix.
        floor: Minimum decks per cluster for validity.
        k_min / k_max: Candidate range (clamped to < n_decks).
        n_bootstrap: Resamples per candidate (kept modest for selection speed).
        seed: RNG/model seed.

    Returns:
        ``(chosen_k, rationale)``.
    """
    n = features.shape[0]
    hi = min(k_max, n - 1)
    evaluated: list[tuple[int, bool, bool, float]] = []
    for k in range(k_min, hi + 1):
        labels, _ = cluster_decks(features, k, seed=seed)
        sizes = np.bincount(labels, minlength=k)
        all_floor = bool((sizes >= floor).all())
        stab = bootstrap_stability(features, k, n_bootstrap=n_bootstrap, seed=seed)
        is_stable = stab.verdict == "stable"
        evaluated.append((k, all_floor, is_stable, stab.mean_ari))

    if not evaluated:
        return max(2, min(k_min, n)), "fallback: pool too small to sweep k"

    strict = [e for e in evaluated if e[1] and e[2]]
    if strict:
        k = max(strict, key=lambda e: e[0])[0]
        return k, f"largest stable k with all clusters >= floor ({floor})"

    floored = [e for e in evaluated if e[1]]
    if floored:
        k = max(floored, key=lambda e: e[3])[0]
        return k, f"no stable k met the floor; best-ARI k with all clusters >= {floor}"

    k = max(evaluated, key=lambda e: e[3])[0]
    return k, "no k cleared the floor; highest-ARI k (clusters flagged below floor)"


def cluster_decks(
    features: np.ndarray, k: int, seed: int = 0
) -> tuple[np.ndarray, KMeans]:
    """Cluster decks with k-means in archetype space.

    Args:
        features: ``(n_decks, n_archetypes)`` feature matrix.
        k: Number of clusters.
        seed: Fixed random_state so fits are deterministic given the data
            (isolates sampling effects for the stability check).

    Returns:
        ``(labels, fitted_model)``.
    """
    k = max(1, min(k, features.shape[0]))
    model = KMeans(n_clusters=k, random_state=seed, n_init=10)
    labels = model.fit_predict(features)
    return labels, model


def name_clusters(
    model: KMeans, archetype_names: list[str], epsilon: float = 1e-6
) -> dict[int, list[tuple[str, float]]]:
    """Rank each cluster's archetypes by centroid weight.

    Args:
        model: A fitted KMeans model.
        archetype_names: Column order of the feature matrix.
        epsilon: Below this centroid max, the cluster is called "mixed".

    Returns:
        Mapping cluster_id -> list of ``(archetype, weight)`` sorted desc.
        A near-zero centroid yields ``[("mixed", 0.0)]``.
    """
    result: dict[int, list[tuple[str, float]]] = {}
    for cid, centroid in enumerate(model.cluster_centers_):
        order = np.argsort(centroid)[::-1]
        top = [
            (archetype_names[j], round(float(centroid[j]), 3))
            for j in order[:3]
            if centroid[j] > epsilon
        ]
        result[cid] = top or [("mixed", 0.0)]
    return result


# ---------------------------------------------------------------------------
# Bootstrap stability
# ---------------------------------------------------------------------------


def bootstrap_stability(
    features: np.ndarray, k: int, n_bootstrap: int = 100, seed: int = 0
) -> StabilityResult:
    """Measure clustering stability via bootstrap adjusted Rand index.

    Reference labels come from clustering the full pool. Each bootstrap draws n
    decks with replacement, refits k-means, predicts labels for the *full*
    pool (nearest centroid), and compares to the reference via ARI. Stable
    clusters produce consistently high ARI; low ARI means the split is an
    artifact of the sample size.

    Args:
        features: Feature matrix.
        k: Number of clusters.
        n_bootstrap: Number of resamples.
        seed: RNG seed for reproducibility.

    Returns:
        A :class:`StabilityResult` with ARI distribution and a verdict.
    """
    n = features.shape[0]
    ref_labels, _ = cluster_decks(features, k, seed=seed)

    rng = np.random.default_rng(seed)
    aris: list[float] = []
    for _ in range(n_bootstrap):
        idx = rng.integers(0, n, size=n)
        sample = features[idx]
        if len(np.unique(sample, axis=0)) < k:
            continue  # too few distinct points to form k clusters
        model = KMeans(n_clusters=k, random_state=seed, n_init=10)
        model.fit(sample)
        pred_full = model.predict(features)
        aris.append(float(adjusted_rand_score(ref_labels, pred_full)))

    if not aris:
        return StabilityResult(
            n_bootstrap=0, mean_ari=0.0, median_ari=0.0,
            p05_ari=0.0, p95_ari=0.0, verdict="not real at this N",
        )

    arr = np.array(aris)
    mean_ari = float(arr.mean())
    verdict = (
        "stable" if mean_ari >= 0.75
        else "moderate" if mean_ari >= 0.5
        else "not real at this N"
    )
    return StabilityResult(
        n_bootstrap=len(aris),
        mean_ari=round(mean_ari, 3),
        median_ari=round(float(np.median(arr)), 3),
        p05_ari=round(float(np.percentile(arr, 5)), 3),
        p95_ari=round(float(np.percentile(arr, 95)), 3),
        verdict=verdict,
    )


# ---------------------------------------------------------------------------
# Orchestration + reporting
# ---------------------------------------------------------------------------


def run_clustering(
    db_path: Path,
    commander: str,
    k: int | None = None,
    n_bootstrap: int = 100,
    floor: int = 20,
    normalize: bool = True,
    seed: int = 0,
    source: str = "archidekt",
) -> ClusterReport:
    """Load, cluster, name, and stability-check one commander's deck pool.

    Args:
        db_path: SQLite database path.
        commander: Commander id or (partial) name.
        k: Cluster count; if None, chosen by :func:`suggest_k`.
        n_bootstrap: Bootstrap resamples for the stability check.
        floor: Minimum decks per cluster for validity (plan: 20-40).
        normalize: L2-normalize deck vectors (cluster on profile, not size).
        seed: RNG/model seed.
        source: Deck source to cluster.

    Returns:
        A :class:`ClusterReport`.
    """
    library = load_library()
    decks = load_commander_decks(db_path, commander, source=source)
    warnings: list[str] = []

    if len(decks) < 2:
        return ClusterReport(
            commander=commander, n_decks=len(decks), k=0, floor=floor,
            normalize=normalize, clusters=[], stability=None,
            warnings=[f"Only {len(decks)} decks — clustering not meaningful."],
        )

    features, archetype_names = build_feature_matrix(decks, library, normalize)

    if k is None:
        k, k_rationale = select_k(
            features, floor=floor, n_bootstrap=min(n_bootstrap, 50), seed=seed
        )
    else:
        k_rationale = "k supplied explicitly"

    if len(decks) < 5 * k:
        warnings.append(
            f"{len(decks)} decks for k={k} — pool is thin; treat clusters as "
            "provisional (plan expects ~100 decks, ~20-40 per cluster)."
        )

    labels, model = cluster_decks(features, k, seed=seed)
    centroid_ranks = name_clusters(model, archetype_names)
    stability = bootstrap_stability(features, k, n_bootstrap=n_bootstrap, seed=seed)

    clusters: list[ClusterSummary] = []
    for cid in range(model.n_clusters):
        members = [i for i, lbl in enumerate(labels) if lbl == cid]
        size = len(members)
        top = centroid_ranks[cid]
        clusters.append(ClusterSummary(
            cluster_id=cid,
            dominant_archetype=top[0][0],
            size=size,
            meets_floor=size >= floor,
            centroid_top=top,
            sample_deck_ids=[decks[i].deck_id for i in members[:5]],
        ))
    clusters.sort(key=lambda c: c.size, reverse=True)

    if not any(c.meets_floor for c in clusters):
        warnings.append(
            f"No cluster reaches the {floor}-deck floor — splits are likely "
            "too fine for the pool size."
        )
    if stability.verdict == "not real at this N":
        warnings.append(
            f"Bootstrap ARI {stability.mean_ari} — clustering is NOT stable; "
            "do not treat these clusters as real sub-archetypes."
        )

    return ClusterReport(
        commander=commander, n_decks=len(decks), k=k, k_rationale=k_rationale,
        floor=floor, normalize=normalize, clusters=clusters, stability=stability,
        warnings=warnings,
    )


def format_report(report: ClusterReport) -> str:
    """Render a :class:`ClusterReport` as readable text."""
    lines = [
        f"=== Macro-archetype clustering: {report.commander} ===",
        f"decks: {report.n_decks}   k: {report.k} ({report.k_rationale})   "
        f"floor: {report.floor}   normalized: {report.normalize}",
    ]
    if report.stability:
        s = report.stability
        lines.append(
            f"stability: bootstrap ARI mean={s.mean_ari} "
            f"(median={s.median_ari}, 5-95%=[{s.p05_ari},{s.p95_ari}], "
            f"n={s.n_bootstrap}) -> {s.verdict.upper()}"
        )
    lines.append("")
    lines.append(f"{'cluster':<9}{'archetype':<14}{'size':>5}{'floor?':>8}  top archetypes")
    lines.append("-" * 66)
    for c in report.clusters:
        top = ", ".join(f"{a}:{w}" for a, w in c.centroid_top)
        lines.append(
            f"{c.cluster_id:<9}{c.dominant_archetype:<14}{c.size:>5}"
            f"{('yes' if c.meets_floor else 'NO'):>8}  {top}"
        )
    if report.warnings:
        lines.append("")
        for w in report.warnings:
            lines.append(f"⚠ {w}")
    return "\n".join(lines)
