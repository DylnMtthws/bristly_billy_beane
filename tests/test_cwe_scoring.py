"""Card Win Equity scoring boost (revived with TopDeck tournament data).

The boost is additive, one-sided (only positive win-equity helps), and gated by
sample size so low-evidence tournament data cannot move the ranking.
"""

from sabermetrics.analytics.cvar import ScoringContext, compute_cvar
from sabermetrics.config import settings


def _card():
    return {
        "id": "x", "name": "Test", "type_line": "Creature", "oracle_text": "",
        "color_identity": ["G"], "keywords": "[]", "cmc": 2.0, "rarity": "rare",
        "price_usd": 1.0,
    }


def _ctx(**kw):
    return ScoringContext(
        commander_id="c", commander_name="C", commander_colors=["G"], **kw
    )


def test_positive_cwe_with_enough_samples_boosts_score() -> None:
    base = compute_cvar(_card(), _ctx()).composite_score
    boosted = compute_cvar(
        _card(),
        _ctx(cwe_by_card={"x": 0.5}, cwe_sample_by_card={"x": 20}),
    )
    assert boosted.composite_score > base
    # boost == cwe_weight * min(1, cwe)
    assert abs(boosted.composite_score - (base + settings.scoring.cwe_weight * 0.5)) < 1e-9
    assert boosted.card_win_equity == 0.5


def test_low_sample_cwe_is_gated_out() -> None:
    base = compute_cvar(_card(), _ctx()).composite_score
    below = settings.scoring.cwe_min_sample - 1
    gated = compute_cvar(
        _card(), _ctx(cwe_by_card={"x": 0.5}, cwe_sample_by_card={"x": below})
    )
    assert gated.composite_score == base  # no boost applied


def test_negative_cwe_never_penalizes() -> None:
    base = compute_cvar(_card(), _ctx()).composite_score
    neg = compute_cvar(
        _card(), _ctx(cwe_by_card={"x": -0.4}, cwe_sample_by_card={"x": 50})
    )
    assert neg.composite_score == base  # one-sided: no penalty


def test_no_cwe_data_leaves_score_unchanged() -> None:
    r = compute_cvar(_card(), _ctx())
    assert r.card_win_equity is None
