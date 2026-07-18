"""Held-out validation + consensus decklists for clusters (Phase 5).

Before any of the clustering/valuation is trusted downstream, two checks:

1. Held-out generalization. Split the pool 80/20, build the clustering + staple
   list on the 80% train set, then test on the untouched 20%:
     - assignment agreement: does a held-out deck's own dominant archetype match
       the train cluster it is assigned to (nearest centroid)?
     - staple recall: does a held-out deck actually contain the staples the train
       model predicted for its assigned cluster?
   Because a single 20%-of-~74 split is tiny and noisy, the split is repeated
   many times and the spread is reported — a mean with a wide interval is an
   honest "we can't tell at this N", not a failure to hide.

2. Consensus decklist. The per-cluster inclusion-ranked card list ("what the
   average deck of this sub-archetype looks like"), surfaced for a human SME to
   eyeball for plausibility before it drives anything.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from sabermetrics.analytics.archetype_signatures import (
    classify_deck,
    load_library,
)
from sabermetrics.analytics.cluster_valuation import _presence_counts
from sabermetrics.analytics.deck_clustering import (
    build_feature_matrix,
    cluster_decks,
    load_commander_decks,
    name_clusters,
    select_k,
)

logger = logging.getLogger(__name__)


class HoldoutReport(BaseModel):
    """Repeated-split held-out validation summary."""

    commander: str
    n_decks: int
    n_train: int
    n_test: int
    n_splits: int
    k_used: int
    assignment_agreement_mean: float
    assignment_agreement_p05_p95: tuple[float, float]
    staple_recall_mean: float
    staple_recall_p05_p95: tuple[float, float]
    verdict: str
    notes: list[str] = Field(default_factory=list)


class AggregateCard(BaseModel):
    card_name: str
    inclusion_rate: float


class AggregateDecklist(BaseModel):
    cluster_id: int
    archetype: str
    size: int
    cards: list[AggregateCard]


def _train_staples(
    member_card_lists: list[list[str]], min_inclusion: float
) -> set[str]:
    """Cards present in >= min_inclusion of a cluster's train decks."""
    n = len(member_card_lists)
    if n == 0:
        return set()
    counts = _presence_counts(member_card_lists)
    return {name for name, c in counts.items() if c / n >= min_inclusion}


def holdout_validation(
    db_path: Path,
    commander: str,
    test_frac: float = 0.2,
    n_splits: int = 25,
    k: int | None = None,
    staple_min_inclusion: float = 0.6,
    seed: int = 0,
) -> HoldoutReport:
    """Repeated 80/20 held-out validation of the clustering + staples.

    Args:
        db_path: SQLite database path.
        commander: Commander id or (partial) name.
        test_frac: Held-out fraction.
        n_splits: Number of random splits to average over.
        k: Cluster count; if None, floor-aware auto (fit once on full pool).
        staple_min_inclusion: Train inclusion for a card to be a predicted staple.
        seed: RNG seed.

    Returns:
        A :class:`HoldoutReport`.
    """
    library = load_library()
    decks = load_commander_decks(db_path, commander)
    n = len(decks)
    notes: list[str] = []

    if n < 20:
        return HoldoutReport(
            commander=commander, n_decks=n, n_train=0, n_test=0, n_splits=0,
            k_used=0, assignment_agreement_mean=0.0,
            assignment_agreement_p05_p95=(0.0, 0.0), staple_recall_mean=0.0,
            staple_recall_p05_p95=(0.0, 0.0), verdict="insufficient data",
            notes=[f"Only {n} decks — cannot validate."],
        )

    full_features, archetype_names = build_feature_matrix(decks, library, True)
    if k is None:
        k, _rationale = select_k(full_features, floor=20, seed=seed)

    # Precompute each deck's feature row, card set, and own dominant archetype.
    card_sets = [set(d.card_names) for d in decks]
    dominants = [classify_deck(d.card_names, library).dominant for d in decks]

    n_test = max(1, round(n * test_frac))
    rng = np.random.default_rng(seed)

    agreements: list[float] = []
    recalls: list[float] = []

    for _ in range(n_splits):
        perm = rng.permutation(n)
        test_idx = perm[:n_test]
        train_idx = perm[n_test:]
        if len(train_idx) < k:
            continue

        train_feats = full_features[train_idx]
        _labels, model = cluster_decks(train_feats, k, seed=seed)
        cluster_archetype = {
            cid: top[0][0] for cid, top in name_clusters(model, archetype_names).items()
        }

        # Predicted staples per train cluster.
        train_labels = model.predict(train_feats)
        staples_by_cluster: dict[int, set[str]] = {}
        for cid in range(model.n_clusters):
            members = [card_sets[train_idx[j]] for j in range(len(train_idx))
                       if train_labels[j] == cid]
            staples_by_cluster[cid] = _train_staples(
                [list(m) for m in members], staple_min_inclusion
            )

        # Assign held-out decks (nearest centroid) and score.
        test_feats = full_features[test_idx]
        assigned = model.predict(test_feats)

        split_agree: list[float] = []
        split_recall: list[float] = []
        for j, ti in enumerate(test_idx):
            cid = int(assigned[j])
            dom = dominants[ti]
            if dom is not None:
                split_agree.append(1.0 if dom == cluster_archetype.get(cid) else 0.0)
            staples = staples_by_cluster.get(cid, set())
            if staples:
                hit = len(card_sets[ti] & staples) / len(staples)
                split_recall.append(hit)

        if split_agree:
            agreements.append(float(np.mean(split_agree)))
        if split_recall:
            recalls.append(float(np.mean(split_recall)))

    def _summ(xs: list[float]) -> tuple[float, tuple[float, float]]:
        if not xs:
            return 0.0, (0.0, 0.0)
        arr = np.array(xs)
        return (
            round(float(arr.mean()), 3),
            (round(float(np.percentile(arr, 5)), 3),
             round(float(np.percentile(arr, 95)), 3)),
        )

    agree_mean, agree_ci = _summ(agreements)
    recall_mean, recall_ci = _summ(recalls)

    verdict = (
        "generalizes" if agree_mean >= 0.7 and recall_mean >= 0.7
        else "partial" if agree_mean >= 0.55 and recall_mean >= 0.55
        else "does not generalize at this N"
    )
    if n_test < 10:
        notes.append(
            f"Test set is only ~{n_test} decks — intervals are wide; read the "
            "5-95% band, not the point estimate."
        )

    return HoldoutReport(
        commander=commander, n_decks=n, n_train=n - n_test, n_test=n_test,
        n_splits=len(agreements), k_used=k,
        assignment_agreement_mean=agree_mean,
        assignment_agreement_p05_p95=agree_ci,
        staple_recall_mean=recall_mean, staple_recall_p05_p95=recall_ci,
        verdict=verdict, notes=notes,
    )


def aggregate_decklist(
    db_path: Path,
    commander: str,
    top_n: int = 45,
    k: int | None = None,
    seed: int = 0,
) -> list[AggregateDecklist]:
    """Build each cluster's inclusion-ranked consensus decklist (full corpus).

    Args:
        db_path: SQLite database path.
        commander: Commander id or (partial) name.
        top_n: Cards to include per cluster (highest inclusion first).
        k: Cluster count; if None, floor-aware auto.
        seed: RNG/model seed.

    Returns:
        One :class:`AggregateDecklist` per cluster (sorted by size).
    """
    library = load_library()
    decks = load_commander_decks(db_path, commander)
    if len(decks) < 2:
        return []

    features, names = build_feature_matrix(decks, library, True)
    if k is None:
        k, _rationale = select_k(features, floor=20, seed=seed)
    labels, model = cluster_decks(features, k, seed=seed)
    cluster_archetype = {
        cid: top[0][0] for cid, top in name_clusters(model, names).items()
    }

    by_cluster: dict[int, list[list[str]]] = {}
    for deck, lbl in zip(decks, labels):
        by_cluster.setdefault(int(lbl), []).append(deck.card_names)

    out: list[AggregateDecklist] = []
    for cid, members in by_cluster.items():
        size = len(members)
        counts = _presence_counts(members)
        ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        out.append(AggregateDecklist(
            cluster_id=cid,
            archetype=cluster_archetype.get(cid, "mixed"),
            size=size,
            cards=[
                AggregateCard(card_name=name, inclusion_rate=round(c / size, 3))
                for name, c in ranked
            ],
        ))
    out.sort(key=lambda a: a.size, reverse=True)
    return out


def format_holdout(report: HoldoutReport) -> str:
    """Render a :class:`HoldoutReport`."""
    ag = report.assignment_agreement_p05_p95
    rc = report.staple_recall_p05_p95
    lines = [
        f"=== Held-out validation: {report.commander} ===",
        f"decks: {report.n_decks}  (train {report.n_train} / test {report.n_test})"
        f"  k={report.k_used}  splits={report.n_splits}",
        f"assignment agreement: {report.assignment_agreement_mean} "
        f"(5-95% [{ag[0]}, {ag[1]}])  "
        "— held-out deck's own archetype matches its assigned cluster",
        f"staple recall:        {report.staple_recall_mean} "
        f"(5-95% [{rc[0]}, {rc[1]}])  "
        "— held-out decks contain the train-predicted staples",
        f"verdict: {report.verdict.upper()}",
    ]
    for note in report.notes:
        lines.append(f"⚠ {note}")
    return "\n".join(lines)


def format_aggregate(decklists: list[AggregateDecklist], per_row: int = 3) -> str:
    """Render consensus decklists for manual (SME) eyeballing."""
    lines = ["=== Consensus decklists per cluster (for manual review) ==="]
    for agg in decklists:
        lines.append("")
        lines.append(
            f"── cluster {agg.cluster_id}: {agg.archetype} (n={agg.size} decks) "
            f"— top {len(agg.cards)} cards by inclusion"
        )
        row: list[str] = []
        for c in agg.cards:
            row.append(f"{int(c.inclusion_rate * 100):>3}% {c.card_name}")
            if len(row) == per_row:
                lines.append("   " + "   ".join(row))
                row = []
        if row:
            lines.append("   " + "   ".join(row))
    return "\n".join(lines)
