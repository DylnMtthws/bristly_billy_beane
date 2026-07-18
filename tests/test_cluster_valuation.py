"""Tests for Phase 4 per-cluster card valuation."""

from sabermetrics.analytics.cluster_valuation import (
    _presence_counts,
    wilson_interval,
)


def test_wilson_interval_known_values() -> None:
    # Symmetric, contains the point estimate, within [0,1].
    lo, hi = wilson_interval(15, 30)
    assert 0.0 < lo < 0.5 < hi < 1.0


def test_wilson_interval_extremes_stay_in_bounds() -> None:
    lo, hi = wilson_interval(30, 30)   # 100% inclusion
    assert hi <= 1.0 and lo > 0.8      # tight, high
    lo0, hi0 = wilson_interval(0, 30)  # 0% inclusion
    assert lo0 == 0.0 and hi0 < 0.2    # tight, low
    assert wilson_interval(1, 0) == (0.0, 0.0)  # n=0 safe


def test_wilson_midrange_is_wider_than_extreme() -> None:
    mid = wilson_interval(15, 30)          # p=0.5
    ext = wilson_interval(28, 30)          # p≈0.93
    mid_moe = (mid[1] - mid[0]) / 2
    ext_moe = (ext[1] - ext[0]) / 2
    # Mid-range uncertainty exceeds the plan's ~0.15 threshold; extreme doesn't.
    assert mid_moe > 0.15 > ext_moe


def test_presence_counts_dedupes_and_drops_basics() -> None:
    lists = [
        ["Blood Artist", "Blood Artist", "Swamp", "Sol Ring"],  # dup + basic
        ["Blood Artist", "Forest"],
    ]
    counts = _presence_counts(lists)
    assert counts["Blood Artist"] == 2     # counted once per deck
    assert counts["Sol Ring"] == 1
    assert "Swamp" not in counts and "Forest" not in counts  # basics excluded
