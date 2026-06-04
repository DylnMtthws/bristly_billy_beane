"""Deckbuilding pattern statistics over Game Knights decklists.

Aggregates component counts, mana-base quality, card-type distributions, theme
densities, archetype profiles, and feature correlations into a
:class:`DeckbuildingPatterns` model. Rendering that model into a knowledge base
document lives in :mod:`sabermetrics.analytics.kb_builder`.

Split out of the former ``deck_patterns`` module, which now re-exports these
names for backward compatibility.
"""

import logging
import sqlite3
import statistics
from collections import Counter
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from sabermetrics.analytics.components import (
    analyze_mana_base,
    count_board_wipes,
    count_card_draw,
    count_ramp_spells,
    count_removal,
    count_tutors,
)
from sabermetrics.analytics.theme_patterns import (
    classify_dominant_theme,
    compute_deck_theme_vector,
)
from sabermetrics.pipeline.mana_base import compute_color_targets

logger = logging.getLogger(__name__)


class ComponentStats(BaseModel):
    """Descriptive statistics for a single deck metric."""

    mean: float = 0.0
    median: float = 0.0
    min: float = 0.0
    max: float = 0.0
    std_dev: float = 0.0


class ManaBaseAnalysis(BaseModel):
    """Statistical mana base analysis results."""

    quality_scores: ComponentStats = Field(default_factory=ComponentStats)
    color_source_counts: dict[str, ComponentStats] = Field(default_factory=dict)
    etb_tapped_ratio: ComponentStats = Field(default_factory=ComponentStats)
    archetype_targets: dict[str, dict[str, int]] = Field(default_factory=dict)


class CardTypeDistribution(BaseModel):
    """Distribution of card types across analyzed decks."""

    creatures: ComponentStats = Field(default_factory=ComponentStats)
    instants: ComponentStats = Field(default_factory=ComponentStats)
    sorceries: ComponentStats = Field(default_factory=ComponentStats)
    enchantments: ComponentStats = Field(default_factory=ComponentStats)
    artifacts: ComponentStats = Field(default_factory=ComponentStats)
    planeswalkers: ComponentStats = Field(default_factory=ComponentStats)


class ThemeDensity(BaseModel):
    """Prevalence of a mechanic theme across analyzed decks."""

    theme_name: str
    card_count_stats: ComponentStats = Field(default_factory=ComponentStats)
    deck_count_with_theme: int = 0
    percentage_of_decks: float = 0.0


class ArchetypeProfile(BaseModel):
    """Composition profile for decks sharing a dominant theme."""

    archetype_name: str
    deck_count: int = 0
    land_counts: ComponentStats = Field(default_factory=ComponentStats)
    ramp_counts: ComponentStats = Field(default_factory=ComponentStats)
    draw_counts: ComponentStats = Field(default_factory=ComponentStats)
    removal_counts: ComponentStats = Field(default_factory=ComponentStats)
    wipe_counts: ComponentStats = Field(default_factory=ComponentStats)
    creature_counts: ComponentStats = Field(default_factory=ComponentStats)
    avg_cmc: ComponentStats = Field(default_factory=ComponentStats)
    avg_theme_density: float = 0.0
    top_cards: list[dict[str, object]] = Field(default_factory=list)


class FeatureCorrelation(BaseModel):
    """Pearson correlation between two deckbuilding features."""

    feature_a: str
    feature_b: str
    correlation_coefficient: float
    description: str
    sample_size: int = 0


class DeckbuildingPatterns(BaseModel):
    """Aggregated deckbuilding patterns across a set of decks."""

    deck_count: int = 0
    land_counts: ComponentStats = Field(default_factory=ComponentStats)
    ramp_counts: ComponentStats = Field(default_factory=ComponentStats)
    draw_counts: ComponentStats = Field(default_factory=ComponentStats)
    removal_counts: ComponentStats = Field(default_factory=ComponentStats)
    wipe_counts: ComponentStats = Field(default_factory=ComponentStats)
    tutor_counts: ComponentStats = Field(default_factory=ComponentStats)
    avg_cmc: ComponentStats = Field(default_factory=ComponentStats)
    mana_curve: dict[int, float] = Field(default_factory=dict)
    color_distribution: dict[str, int] = Field(default_factory=dict)
    most_played_cards: list[dict[str, object]] = Field(default_factory=list)
    mana_base_analysis: ManaBaseAnalysis | None = None
    card_type_distribution: CardTypeDistribution | None = None
    theme_densities: list[ThemeDensity] = Field(default_factory=list)
    archetype_profiles: list[ArchetypeProfile] = Field(default_factory=list)
    feature_correlations: list[FeatureCorrelation] = Field(default_factory=list)


def _compute_stats(values: list[float]) -> ComponentStats:
    """Compute descriptive statistics from a list of values.

    Args:
        values: Numeric values to summarize.

    Returns:
        ComponentStats with mean, median, min, max, std_dev.
    """
    if not values:
        return ComponentStats()
    return ComponentStats(
        mean=round(statistics.mean(values), 2),
        median=round(statistics.median(values), 2),
        min=round(min(values), 2),
        max=round(max(values), 2),
        std_dev=round(statistics.stdev(values), 2) if len(values) > 1 else 0.0,
    )


class GameKnightsAnalyzer:
    """Analyzes deckbuilding patterns across Game Knights decklists."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def analyze(
        self, source_filter: str = "archidekt_gameknights"
    ) -> DeckbuildingPatterns:
        """Aggregate deckbuilding patterns from decks matching the source filter.

        Args:
            source_filter: Value to match against decks.source column.

        Returns:
            DeckbuildingPatterns with aggregated statistics.
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            return self._analyze_decks(conn, source_filter)
        finally:
            conn.close()

    def _analyze_decks(
        self, conn: sqlite3.Connection, source_filter: str
    ) -> DeckbuildingPatterns:
        """Core analysis logic.

        Args:
            conn: Open SQLite connection.
            source_filter: Source column filter value.

        Returns:
            Aggregated DeckbuildingPatterns.
        """
        # Fetch all deck IDs for this source
        cursor = conn.execute(
            "SELECT id FROM decks WHERE source = ?", (source_filter,)
        )
        deck_ids = [row["id"] for row in cursor.fetchall()]

        if not deck_ids:
            logger.warning("No decks found for source '%s'", source_filter)
            return DeckbuildingPatterns()

        logger.info("Analyzing %d decks from '%s'", len(deck_ids), source_filter)

        land_counts: list[float] = []
        ramp_counts: list[float] = []
        draw_counts: list[float] = []
        removal_counts: list[float] = []
        wipe_counts: list[float] = []
        tutor_counts: list[float] = []
        avg_cmcs: list[float] = []
        curve_totals: dict[int, int] = Counter()
        color_totals: dict[str, int] = Counter()
        card_appearances: Counter[str] = Counter()
        total_nonland_cards = 0

        # New accumulators for theme/type analysis
        type_counts_per_deck: list[dict[str, int]] = []
        theme_counts_per_deck: list[dict[str, int]] = []
        dominant_themes: dict[str, str | None] = {}
        deck_features: dict[str, dict[str, float]] = {}

        for deck_id in deck_ids:
            cards = self._load_deck_cards(conn, deck_id)
            if not cards:
                continue

            # Separate lands and non-lands
            lands = [c for c in cards if "land" in (c.get("type_line") or "").lower()]
            non_lands = [c for c in cards if "land" not in (c.get("type_line") or "").lower()]

            land_val = float(len(lands))
            ramp_val = float(count_ramp_spells(cards))
            draw_val = float(count_card_draw(cards))
            removal_val = float(count_removal(cards))
            wipe_val = float(count_board_wipes(cards))
            tutor_val = float(count_tutors(cards))

            land_counts.append(land_val)
            ramp_counts.append(ramp_val)
            draw_counts.append(draw_val)
            removal_counts.append(removal_val)
            wipe_counts.append(wipe_val)
            tutor_counts.append(tutor_val)

            # Average CMC of non-land cards
            cmcs = [c.get("cmc", 0) or 0 for c in non_lands]
            cmc_avg = statistics.mean(cmcs) if cmcs else 0.0
            if cmcs:
                avg_cmcs.append(cmc_avg)

            # Mana curve (bucket by integer CMC)
            for cmc_val in cmcs:
                bucket = min(int(cmc_val), 7)  # 7+ grouped
                curve_totals[bucket] += 1

            # Color identity from commander
            commander_row = conn.execute(
                """SELECT c.color_identity FROM decks d
                   JOIN cards c ON d.commander_id = c.id
                   WHERE d.id = ?""",
                (deck_id,),
            ).fetchone()
            if commander_row and commander_row["color_identity"]:
                import json

                try:
                    colors = json.loads(commander_row["color_identity"])
                    for color in colors:
                        color_totals[color] += 1
                except (json.JSONDecodeError, TypeError):
                    pass

            # Track non-land card appearances
            for card in non_lands:
                card_name = card.get("name")
                if card_name:
                    card_appearances[card_name] += 1
            total_nonland_cards += len(non_lands)

            # Card type counts
            type_counts = self._count_card_types(cards)
            type_counts_per_deck.append(type_counts)

            # Theme analysis
            theme_vector = compute_deck_theme_vector(cards)
            theme_counts_per_deck.append(theme_vector)
            dominant_themes[deck_id] = classify_dominant_theme(theme_vector)

            # Build feature vector for correlations
            features: dict[str, float] = {
                "lands": land_val,
                "ramp": ramp_val,
                "draw": draw_val,
                "removal": removal_val,
                "wipes": wipe_val,
                "tutors": tutor_val,
                "avg_cmc": cmc_avg,
                "creatures": float(type_counts.get("creatures", 0)),
            }
            for theme_name, theme_count in theme_vector.items():
                features[f"theme_{theme_name}"] = float(theme_count)
            deck_features[deck_id] = features

        # Build mana curve as averages per deck
        deck_count = len(deck_ids)
        mana_curve = {
            bucket: round(total / deck_count, 2)
            for bucket, total in sorted(curve_totals.items())
        }

        # Most-played non-land cards (appear in >1 deck)
        most_played = [
            {
                "name": name,
                "deck_count": count,
                "percentage": round(count / deck_count * 100, 1),
            }
            for name, count in card_appearances.most_common(50)
            if count > 1
        ]

        # Mana base statistical analysis
        mana_base_analysis = self._analyze_mana_bases(conn, deck_ids)

        # New computations
        card_type_distribution = self._compute_card_type_distribution(
            type_counts_per_deck
        )
        theme_densities = self._compute_theme_densities(
            theme_counts_per_deck, deck_count
        )
        archetype_profiles = self._compute_archetype_profiles(
            conn, deck_ids, dominant_themes, deck_features
        )
        feature_correlations = self._compute_feature_correlations(deck_features)

        return DeckbuildingPatterns(
            deck_count=deck_count,
            land_counts=_compute_stats(land_counts),
            ramp_counts=_compute_stats(ramp_counts),
            draw_counts=_compute_stats(draw_counts),
            removal_counts=_compute_stats(removal_counts),
            wipe_counts=_compute_stats(wipe_counts),
            tutor_counts=_compute_stats(tutor_counts),
            avg_cmc=_compute_stats(avg_cmcs),
            mana_curve=mana_curve,
            color_distribution=dict(color_totals),
            most_played_cards=most_played,
            mana_base_analysis=mana_base_analysis,
            card_type_distribution=card_type_distribution,
            theme_densities=theme_densities,
            archetype_profiles=archetype_profiles,
            feature_correlations=feature_correlations,
        )

    def _analyze_mana_bases(
        self, conn: sqlite3.Connection, deck_ids: list[str]
    ) -> ManaBaseAnalysis:
        """Analyze mana base quality across decks using Karsten framework.

        Args:
            conn: Open SQLite connection.
            deck_ids: List of deck IDs to analyze.

        Returns:
            ManaBaseAnalysis with quality scores, color sources, ETB ratios,
            and archetype targets.
        """
        import json as _json

        quality_scores: list[float] = []
        etb_tapped_ratios: list[float] = []
        color_source_lists: dict[str, list[float]] = {}

        for deck_id in deck_ids:
            cards = self._load_deck_cards(conn, deck_id)
            if not cards:
                continue

            # Get commander colors
            commander_row = conn.execute(
                """SELECT c.color_identity FROM decks d
                   JOIN cards c ON d.commander_id = c.id
                   WHERE d.id = ?""",
                (deck_id,),
            ).fetchone()
            commander_colors: list[str] = []
            if commander_row and commander_row["color_identity"]:
                try:
                    commander_colors = _json.loads(commander_row["color_identity"])
                except (_json.JSONDecodeError, TypeError):
                    pass

            if not commander_colors:
                continue

            # Run mana base analysis
            mana_score = analyze_mana_base(cards, commander_colors)
            quality_scores.append(mana_score.score)

            # ETB-tapped ratio
            if mana_score.total_lands > 0:
                lands = [
                    c for c in cards
                    if "land" in (c.get("type_line") or "").lower()
                ]
                tapped = sum(
                    1 for c in lands
                    if "enters the battlefield tapped" in (c.get("oracle_text") or "").lower()
                )
                etb_tapped_ratios.append(tapped / mana_score.total_lands)

            # Per-color source counts
            for color, count in mana_score.color_sources.items():
                if color not in color_source_lists:
                    color_source_lists[color] = []
                color_source_lists[color].append(float(count))

        # Compute per-color stats
        color_source_stats: dict[str, ComponentStats] = {
            color: _compute_stats(values)
            for color, values in color_source_lists.items()
        }

        # Compute archetype targets
        archetype_targets = self._compute_archetype_targets()

        return ManaBaseAnalysis(
            quality_scores=_compute_stats(quality_scores),
            color_source_counts=color_source_stats,
            etb_tapped_ratio=_compute_stats(etb_tapped_ratios),
            archetype_targets=archetype_targets,
        )

    @staticmethod
    def _compute_archetype_targets() -> dict[str, dict[str, int]]:
        """Compute per-archetype color source targets using Karsten math.

        Creates synthetic spell lists reflecting typical pip requirements
        for each color count, then uses compute_color_targets() to determine
        how many sources of each color are needed.

        Returns:
            Dict mapping archetype label to {color: sources_needed}.
        """
        total_lands = 36  # Standard Commander land count

        # Synthetic spell lists for representative pip requirements
        # Each scenario has spells with typical pip distributions
        archetypes: dict[str, tuple[list[str], list[dict]]] = {
            "mono_color": (
                ["W"],
                [
                    # Heavy single-color commitment: 2-pip spells at CMC 3-4
                    {"mana_cost": "{W}{W}{1}", "cmc": 3.0, "type_line": "Creature"},
                    {"mana_cost": "{W}{W}{2}", "cmc": 4.0, "type_line": "Sorcery"},
                    {"mana_cost": "{W}{1}", "cmc": 2.0, "type_line": "Instant"},
                ],
            ),
            "two_color": (
                ["W", "U"],
                [
                    {"mana_cost": "{W}{W}{1}", "cmc": 3.0, "type_line": "Creature"},
                    {"mana_cost": "{U}{U}{1}", "cmc": 3.0, "type_line": "Sorcery"},
                    {"mana_cost": "{W}{U}{1}", "cmc": 3.0, "type_line": "Instant"},
                ],
            ),
            "three_color": (
                ["W", "U", "B"],
                [
                    {"mana_cost": "{W}{W}{2}", "cmc": 4.0, "type_line": "Creature"},
                    {"mana_cost": "{U}{1}", "cmc": 2.0, "type_line": "Instant"},
                    {"mana_cost": "{B}{B}{1}", "cmc": 3.0, "type_line": "Sorcery"},
                    {"mana_cost": "{W}{U}{B}", "cmc": 3.0, "type_line": "Enchantment"},
                ],
            ),
            "four_color": (
                ["W", "U", "B", "R"],
                [
                    {"mana_cost": "{W}{1}", "cmc": 2.0, "type_line": "Instant"},
                    {"mana_cost": "{U}{1}", "cmc": 2.0, "type_line": "Instant"},
                    {"mana_cost": "{B}{1}", "cmc": 2.0, "type_line": "Sorcery"},
                    {"mana_cost": "{R}{1}", "cmc": 2.0, "type_line": "Creature"},
                ],
            ),
            "five_color": (
                ["W", "U", "B", "R", "G"],
                [
                    {"mana_cost": "{W}{1}", "cmc": 2.0, "type_line": "Instant"},
                    {"mana_cost": "{U}{1}", "cmc": 2.0, "type_line": "Instant"},
                    {"mana_cost": "{B}{1}", "cmc": 2.0, "type_line": "Sorcery"},
                    {"mana_cost": "{R}{1}", "cmc": 2.0, "type_line": "Creature"},
                    {"mana_cost": "{G}{1}", "cmc": 2.0, "type_line": "Creature"},
                ],
            ),
        }

        result: dict[str, dict[str, int]] = {}
        for label, (colors, spells) in archetypes.items():
            targets = compute_color_targets(spells, colors, total_lands)
            result[label] = targets

        return result

    @staticmethod
    def _count_card_types(cards: list[dict]) -> dict[str, int]:
        """Count cards by type. Uses `if` not `elif` so multi-type cards count for each.

        Skips lands.

        Args:
            cards: List of card dicts with type_line.

        Returns:
            Dict mapping type name to count.
        """
        counts: dict[str, int] = {
            "creatures": 0,
            "instants": 0,
            "sorceries": 0,
            "enchantments": 0,
            "artifacts": 0,
            "planeswalkers": 0,
        }
        for card in cards:
            type_line = (card.get("type_line") or "").lower()
            if "land" in type_line:
                continue
            if "creature" in type_line:
                counts["creatures"] += 1
            if "instant" in type_line:
                counts["instants"] += 1
            if "sorcery" in type_line:
                counts["sorceries"] += 1
            if "enchantment" in type_line:
                counts["enchantments"] += 1
            if "artifact" in type_line:
                counts["artifacts"] += 1
            if "planeswalker" in type_line:
                counts["planeswalkers"] += 1
        return counts

    @staticmethod
    def _compute_card_type_distribution(
        type_counts_per_deck: list[dict[str, int]],
    ) -> CardTypeDistribution:
        """Compute per-type statistics across all decks.

        Args:
            type_counts_per_deck: List of per-deck type count dicts.

        Returns:
            CardTypeDistribution with stats for each type.
        """
        if not type_counts_per_deck:
            return CardTypeDistribution()

        type_names = ["creatures", "instants", "sorceries", "enchantments", "artifacts", "planeswalkers"]
        stats: dict[str, ComponentStats] = {}
        for t in type_names:
            values = [float(d.get(t, 0)) for d in type_counts_per_deck]
            stats[t] = _compute_stats(values)

        return CardTypeDistribution(**stats)

    @staticmethod
    def _compute_theme_densities(
        theme_counts_per_deck: list[dict[str, int]],
        deck_count: int,
        min_cards: int = 3,
    ) -> list[ThemeDensity]:
        """Compute theme density statistics across decks.

        Args:
            theme_counts_per_deck: List of per-deck theme vectors.
            deck_count: Total number of decks analyzed.
            min_cards: Minimum cards for a deck to "have" a theme.

        Returns:
            List of ThemeDensity for all themes, sorted by prevalence.
        """
        if not theme_counts_per_deck or deck_count == 0:
            return []

        from sabermetrics.analytics.theme_patterns import THEME_PATTERNS

        densities: list[ThemeDensity] = []
        for theme in THEME_PATTERNS:
            per_deck = [float(d.get(theme, 0)) for d in theme_counts_per_deck]
            decks_with = sum(1 for v in per_deck if v >= min_cards)
            densities.append(
                ThemeDensity(
                    theme_name=theme,
                    card_count_stats=_compute_stats(per_deck),
                    deck_count_with_theme=decks_with,
                    percentage_of_decks=round(decks_with / deck_count * 100, 1),
                )
            )

        densities.sort(key=lambda d: d.percentage_of_decks, reverse=True)
        return densities

    def _compute_archetype_profiles(
        self,
        conn: sqlite3.Connection,
        deck_ids: list[str],
        dominant_themes: dict[str, str | None],
        deck_features: dict[str, dict[str, float]],
    ) -> list[ArchetypeProfile]:
        """Compute composition profiles grouped by dominant theme.

        Only includes archetypes with >= 3 decks.

        Args:
            conn: Open SQLite connection.
            deck_ids: All analyzed deck IDs.
            dominant_themes: Mapping of deck_id -> dominant theme (or None).
            deck_features: Mapping of deck_id -> feature dict.

        Returns:
            List of ArchetypeProfile sorted by deck count descending.
        """
        # Group deck IDs by dominant theme
        theme_groups: dict[str, list[str]] = {}
        for did in deck_ids:
            theme = dominant_themes.get(did)
            if theme:
                theme_groups.setdefault(theme, []).append(did)

        profiles: list[ArchetypeProfile] = []
        for theme, group_ids in theme_groups.items():
            if len(group_ids) < 3:
                continue

            lands: list[float] = []
            ramp: list[float] = []
            draw: list[float] = []
            removal: list[float] = []
            wipes: list[float] = []
            creatures: list[float] = []
            cmcs: list[float] = []
            theme_densities: list[float] = []
            card_appearances: Counter[str] = Counter()

            for did in group_ids:
                feats = deck_features.get(did, {})
                lands.append(feats.get("lands", 0))
                ramp.append(feats.get("ramp", 0))
                draw.append(feats.get("draw", 0))
                removal.append(feats.get("removal", 0))
                wipes.append(feats.get("wipes", 0))
                creatures.append(feats.get("creatures", 0))
                cmcs.append(feats.get("avg_cmc", 0))
                theme_densities.append(feats.get(f"theme_{theme}", 0))

                # Top cards for this archetype
                cards = self._load_deck_cards(conn, did)
                for card in cards:
                    type_line = (card.get("type_line") or "").lower()
                    if "land" not in type_line and card.get("name"):
                        card_appearances[card["name"]] += 1

            top_cards = [
                {
                    "name": name,
                    "count": count,
                    "percentage": round(count / len(group_ids) * 100, 1),
                }
                for name, count in card_appearances.most_common(10)
            ]

            profiles.append(
                ArchetypeProfile(
                    archetype_name=theme,
                    deck_count=len(group_ids),
                    land_counts=_compute_stats(lands),
                    ramp_counts=_compute_stats(ramp),
                    draw_counts=_compute_stats(draw),
                    removal_counts=_compute_stats(removal),
                    wipe_counts=_compute_stats(wipes),
                    creature_counts=_compute_stats(creatures),
                    avg_cmc=_compute_stats(cmcs),
                    avg_theme_density=round(
                        statistics.mean(theme_densities), 2
                    ) if theme_densities else 0.0,
                    top_cards=top_cards,
                )
            )

        profiles.sort(key=lambda p: p.deck_count, reverse=True)
        return profiles

    @staticmethod
    def _compute_feature_correlations(
        deck_features: dict[str, dict[str, float]],
        min_abs_r: float = 0.3,
    ) -> list[FeatureCorrelation]:
        """Compute Pearson correlations across deck features.

        Requires >= 10 decks. Returns top 25 by |r|.

        Args:
            deck_features: Mapping of deck_id -> feature dict.
            min_abs_r: Minimum absolute correlation to include.

        Returns:
            List of FeatureCorrelation sorted by |r| descending.
        """
        if len(deck_features) < 10:
            return []

        # Build feature matrix
        deck_ids_ordered = list(deck_features.keys())
        all_features = set()
        for feats in deck_features.values():
            all_features.update(feats.keys())

        feature_names = sorted(all_features)
        if len(feature_names) < 2:
            return []

        matrix = np.zeros((len(deck_ids_ordered), len(feature_names)))
        for i, did in enumerate(deck_ids_ordered):
            for j, feat in enumerate(feature_names):
                matrix[i, j] = deck_features[did].get(feat, 0.0)

        # Compute correlation matrix
        # Suppress warnings for constant columns
        with np.errstate(invalid="ignore"):
            corr_matrix = np.corrcoef(matrix, rowvar=False)

        # Extract top correlations
        correlations: list[FeatureCorrelation] = []
        n = len(feature_names)
        for i in range(n):
            for j in range(i + 1, n):
                r = corr_matrix[i, j]
                if np.isnan(r):
                    continue
                if abs(r) >= min_abs_r:
                    desc = GameKnightsAnalyzer._describe_correlation(
                        feature_names[i], feature_names[j], r, deck_features
                    )
                    correlations.append(
                        FeatureCorrelation(
                            feature_a=feature_names[i],
                            feature_b=feature_names[j],
                            correlation_coefficient=round(r, 3),
                            description=desc,
                            sample_size=len(deck_ids_ordered),
                        )
                    )

        correlations.sort(key=lambda c: abs(c.correlation_coefficient), reverse=True)
        return correlations[:25]

    @staticmethod
    def _describe_correlation(
        feat_a: str, feat_b: str, r: float, deck_features: dict[str, dict[str, float]]
    ) -> str:
        """Generate human-readable description for a feature correlation.

        Args:
            feat_a: First feature name.
            feat_b: Second feature name.
            r: Pearson correlation coefficient.
            deck_features: Full feature dict (for context).

        Returns:
            Human-readable description string.
        """
        direction = "positively" if r > 0 else "negatively"
        strength = "strongly" if abs(r) >= 0.6 else "moderately"

        # Clean up feature names for display
        def _display(name: str) -> str:
            return name.replace("theme_", "").replace("_", " ")

        return (
            f"{_display(feat_a)} and {_display(feat_b)} are "
            f"{strength} {direction} correlated (r={round(r, 2)})"
        )

    def _load_deck_cards(
        self, conn: sqlite3.Connection, deck_id: str
    ) -> list[dict]:
        """Load all card data for a single deck, expanded by quantity.

        Each card is repeated according to its quantity in the deck,
        so that counts (lands, ramp, etc.) reflect actual deck composition.

        Args:
            conn: Open SQLite connection.
            deck_id: Deck ID to load cards for.

        Returns:
            List of card dicts with oracle_text, type_line, cmc, name, keywords.
            Cards with quantity > 1 appear multiple times.
        """
        cursor = conn.execute(
            """SELECT c.name, c.oracle_text, c.type_line, c.cmc, c.keywords,
                      dc.quantity
               FROM deck_cards dc
               JOIN cards c ON dc.card_id = c.id
               WHERE dc.deck_id = ?""",
            (deck_id,),
        )
        cards: list[dict] = []
        for row in cursor.fetchall():
            card = dict(row)
            qty = card.pop("quantity", 1) or 1
            for _ in range(qty):
                cards.append(card)
        return cards


