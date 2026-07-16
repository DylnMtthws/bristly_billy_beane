"""Tests for Phase 5 held-out validation + consensus decklists."""

from sabermetrics.analytics.cluster_validation import (
    AggregateCard,
    AggregateDecklist,
    _train_staples,
    format_aggregate,
    holdout_validation,
)

ARISTO = ["Blood Artist", "Zulaport Cutthroat", "Cruel Celebrant",
          "Bastion of Remembrance", "Viscera Seer", "Sol Ring"]
LANDFALL = ["Lotus Cobra", "Scute Swarm", "Rampaging Baloths",
            "Avenger of Zendikar", "Felidar Retreat", "Sol Ring"]


def test_train_staples_threshold() -> None:
    lists = [["Sol Ring", "Blood Artist"], ["Sol Ring", "Viscera Seer"],
             ["Sol Ring", "Blood Artist"]]
    # Sol Ring 3/3=100%, Blood Artist 2/3=67%, Viscera 1/3=33%
    staples = _train_staples(lists, min_inclusion=0.6)
    assert "Sol Ring" in staples and "Blood Artist" in staples
    assert "Viscera Seer" not in staples


def test_holdout_insufficient_data(tmp_path, monkeypatch) -> None:
    from sabermetrics.analytics import cluster_validation as cv
    monkeypatch.setattr(cv, "load_commander_decks", lambda *a, **k: [])
    report = holdout_validation(tmp_path / "x.db", "Nobody")
    assert report.verdict == "insufficient data"
    assert report.n_splits == 0


def test_holdout_generalizes_on_separable_pool(tmp_path, monkeypatch) -> None:
    # 30 aristocrats + 30 landfall decks (clearly separable) -> should generalize.
    from sabermetrics.analytics import cluster_validation as cv
    from sabermetrics.analytics.deck_clustering import DeckRecord

    fake = (
        [DeckRecord(deck_id=f"a{i}", card_names=ARISTO, popularity_rank=i)
         for i in range(30)]
        + [DeckRecord(deck_id=f"l{i}", card_names=LANDFALL, popularity_rank=i)
           for i in range(30)]
    )
    monkeypatch.setattr(cv, "load_commander_decks", lambda *a, **k: fake)
    report = holdout_validation(tmp_path / "x.db", "Sep", n_splits=15, k=2)
    assert report.verdict == "generalizes"
    assert report.assignment_agreement_mean >= 0.9
    assert report.staple_recall_mean >= 0.8


def test_format_helpers_run() -> None:
    agg = [AggregateDecklist(cluster_id=0, archetype="aristocrats", size=40,
                             cards=[AggregateCard(card_name="Sol Ring",
                                                  inclusion_rate=0.92)])]
    assert "Sol Ring" in format_aggregate(agg)
