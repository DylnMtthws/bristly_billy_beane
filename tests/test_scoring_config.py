"""Lock the centralized scoring weights to their prior hard-coded values.

These weights were moved out of ``analytics.synergy_matrix`` and
``pipeline.greedy_optimizer`` into ``config.ScoringSettings`` (loaded from
``config/settings.yaml``). This is a behavior-preserving relocation: the tests
below assert the defaults still equal the exact literals the code used before,
and that the consuming modules read those values. If a weight is changed
deliberately, update both the value and the expectation here.
"""

from __future__ import annotations

from sabermetrics.config import ScoringSettings, settings


def test_scoring_defaults_match_prior_literals() -> None:
    """ScoringSettings defaults equal the values previously hard-coded."""
    s = ScoringSettings()
    # synergy_matrix.py (co-occurrence removed in Option A criterion 3)
    assert s.synergy_rule_weight == 0.615
    assert s.synergy_embedding_weight == 0.385
    # greedy_optimizer.py — marginal value formula
    assert s.marginal_synergy_weight == 0.45
    assert s.marginal_role_cvar_weight == 0.35
    assert s.marginal_cvar_weight == 0.20
    # greedy_optimizer.py — deck_objective
    assert s.objective_synergy_density_weight == 0.30
    assert s.objective_role_coverage_weight == 0.25
    assert s.objective_alignment_weight == 0.20
    assert s.objective_avg_cvar_weight == 0.15
    assert s.objective_curve_coherence_weight == 0.10


def test_loaded_settings_match_defaults() -> None:
    """The values in config/settings.yaml match the model defaults."""
    d = ScoringSettings()
    assert settings.scoring.model_dump() == d.model_dump()


def test_synergy_matrix_reads_config() -> None:
    """synergy_matrix module constants are sourced from config."""
    from sabermetrics.analytics import synergy_matrix

    assert synergy_matrix.RULE_WEIGHT == settings.scoring.synergy_rule_weight
    assert synergy_matrix.EMBEDDING_WEIGHT == settings.scoring.synergy_embedding_weight


def test_synergy_weights_sum_to_one() -> None:
    """The two synergy signal weights form a convex blend."""
    s = settings.scoring
    total = s.synergy_rule_weight + s.synergy_embedding_weight
    assert abs(total - 1.0) < 1e-9
