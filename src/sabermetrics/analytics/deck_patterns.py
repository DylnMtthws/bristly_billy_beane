"""Backward-compatible facade for the split deck-pattern modules.

This module was split into:

- :mod:`sabermetrics.analytics.deck_stats` — the statistics models and
  :class:`~sabermetrics.analytics.deck_stats.GameKnightsAnalyzer`.
- :mod:`sabermetrics.analytics.kb_builder` — the
  :class:`~sabermetrics.analytics.kb_builder.KnowledgeBaseBuilder` markdown
  generator.

New code should import from those modules directly. The names below are
re-exported so existing imports keep working.
"""

from sabermetrics.analytics.deck_stats import (
    ArchetypeProfile,
    CardTypeDistribution,
    ComponentStats,
    DeckbuildingPatterns,
    FeatureCorrelation,
    GameKnightsAnalyzer,
    ManaBaseAnalysis,
    ThemeDensity,
    _compute_stats,
)
from sabermetrics.analytics.kb_builder import KnowledgeBaseBuilder

__all__ = [
    "ArchetypeProfile",
    "CardTypeDistribution",
    "ComponentStats",
    "DeckbuildingPatterns",
    "FeatureCorrelation",
    "GameKnightsAnalyzer",
    "KnowledgeBaseBuilder",
    "ManaBaseAnalysis",
    "ThemeDensity",
    "_compute_stats",
]
