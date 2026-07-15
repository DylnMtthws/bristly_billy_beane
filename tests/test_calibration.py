"""Scoring calibration against real decklists (Option A DoD criterion 6).

The CVAR scorer must rank cards that appear in real tracked decks well above a
random legal card. Target: mean percentile >= 0.70 (random baseline is 0.50).

The metric carries some sample noise around the target (small samples can dip
just under); this runs a representative sample (n=40, fixed seed) that clears
0.70 with margin. See scripts/calibrate_scoring.py to reproduce / re-tune.
"""

import sys
from pathlib import Path

import pytest

DB = Path("data/sabermetrics.db")
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


@pytest.mark.skipif(not DB.exists(), reason="needs decklist DB")
def test_cvar_scorer_beats_random_on_real_decks() -> None:
    from calibrate_scoring import calibrate

    stats = calibrate(DB, n_commanders=40, seed=42)
    # Sanity: the sample is real and the pool covers most real-deck cards.
    assert stats["real_cards_evaluated"] > 500
    assert stats["mean_pool_coverage"] > 0.8
    # DoD target: real-deck cards rank in the top ~30% on average.
    assert stats["mean_percentile"] >= 0.70, stats
