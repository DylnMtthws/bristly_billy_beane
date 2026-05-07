"""Deck pattern analysis and knowledge base generation.

Aggregates deckbuilding patterns across Game Knights decklists and
combines with EDHREC article guidance to produce a structured
knowledge base document for RAG grounding.
"""

import logging
import sqlite3
import statistics
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from sabermetrics.analytics.components import (
    analyze_mana_base,
    count_board_wipes,
    count_card_draw,
    count_ramp_spells,
    count_removal,
    count_tutors,
)
from sabermetrics.pipeline.mana_base import (
    KARSTEN_SOURCES_99,
    compute_color_targets,
)

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

        for deck_id in deck_ids:
            cards = self._load_deck_cards(conn, deck_id)
            if not cards:
                continue

            # Separate lands and non-lands
            lands = [c for c in cards if "land" in (c.get("type_line") or "").lower()]
            non_lands = [c for c in cards if "land" not in (c.get("type_line") or "").lower()]

            land_counts.append(float(len(lands)))
            ramp_counts.append(float(count_ramp_spells(cards)))
            draw_counts.append(float(count_card_draw(cards)))
            removal_counts.append(float(count_removal(cards)))
            wipe_counts.append(float(count_board_wipes(cards)))
            tutor_counts.append(float(count_tutors(cards)))

            # Average CMC of non-land cards
            cmcs = [c.get("cmc", 0) or 0 for c in non_lands]
            if cmcs:
                avg_cmcs.append(statistics.mean(cmcs))

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


class KnowledgeBaseBuilder:
    """Builds a structured Markdown knowledge base document.

    Combines Game Knights deck analysis with EDHREC article excerpts
    to produce a RAG-ready reference document.
    """

    def build(
        self,
        patterns: DeckbuildingPatterns,
        edhrec_article_texts: list[str] | None = None,
    ) -> str:
        """Generate a structured Markdown knowledge base document.

        Args:
            patterns: Aggregated deckbuilding patterns from analysis.
            edhrec_article_texts: Optional list of EDHREC article texts
                to triangulate with Game Knights data.

        Returns:
            Markdown-formatted knowledge base string with section headers
            separated by double newlines for clean chunking.
        """
        articles = edhrec_article_texts or []
        combined_articles = "\n".join(articles)
        sections: list[str] = []

        sections.append(self._header_section(patterns))
        sections.append(self._land_count_section(patterns, combined_articles))
        sections.append(self._mana_base_math_section())
        sections.append(self._color_source_targets_section(patterns))
        sections.append(self._ramp_section(patterns, combined_articles))
        sections.append(self._card_draw_section(patterns, combined_articles))
        sections.append(self._removal_section(patterns, combined_articles))
        sections.append(self._mana_curve_section(patterns, combined_articles))
        sections.append(self._power_level_section(patterns))
        sections.append(self._most_popular_section(patterns))
        sections.append(self._budget_section(patterns, combined_articles))

        return "\n\n".join(sections)

    def _header_section(self, patterns: DeckbuildingPatterns) -> str:
        """Generate the document header."""
        return (
            "# Deckbuilding Knowledge Base\n\n"
            "This document combines empirical data from Game Knights decklists "
            f"({patterns.deck_count} decks analyzed) with community deckbuilding "
            "guidance from EDHREC articles. Use these patterns as baselines "
            "when evaluating Commander deck construction."
        )

    def _land_count_section(
        self, patterns: DeckbuildingPatterns, articles: str
    ) -> str:
        """Generate the Land Count section."""
        s = patterns.land_counts
        lines = [
            "## Land Count",
            "",
            f"Game Knights data ({patterns.deck_count} decks):",
            f"- Mean: {s.mean} lands",
            f"- Median: {s.median} lands",
            f"- Range: {s.min} - {s.max} lands",
            f"- Std dev: {s.std_dev}",
        ]
        guidance = self._extract_guidance(articles, ["land count", "lands", "mana base"])
        if guidance:
            lines.append("")
            lines.append("EDHREC guidance:")
            lines.append(guidance)
        lines.append("")
        lines.append(
            "Recommendation: Most Commander decks run 35-38 lands. "
            "Decks with low average CMC or high ramp counts can go lower. "
            "The Karsten hypergeometric model (see Mana Base Mathematics section) "
            "recommends 34-38 total lands for decks at average CMC 2.5-3.5, "
            "adjusted by ramp density."
        )
        return "\n".join(lines)

    @staticmethod
    def _mana_base_math_section() -> str:
        """Generate the Mana Base Mathematics section with Karsten table."""
        lines = [
            "## Mana Base Mathematics",
            "",
            "This section uses Frank Karsten's hypergeometric probability framework, "
            "adapted for 99-card Commander decks, to determine optimal color source "
            "requirements for ~90% on-curve cast probability.",
            "",
            "Karsten Source Requirements (99-card deck, ~36 lands):",
            "| Colored Pips | Cast by Turn | Sources Needed |",
            "|---|---|---|",
        ]

        # Format KARSTEN_SOURCES_99 as a readable table
        pip_labels = {1: "1 pip", 2: "2 pips", 3: "3 pips"}
        for (pips, turn), sources in sorted(KARSTEN_SOURCES_99.items()):
            pip_label = pip_labels.get(pips, f"{pips} pips")
            lines.append(f"| {pip_label} | Turn {turn} | {sources} |")

        lines.append("")
        lines.append(
            "How to read: If a spell costs {U}{U}{1} (2 blue pips, CMC 3), "
            "you want to cast it on turn 3, so look up (2 pips, turn 3) = 23 "
            "blue sources needed. This means ~23 of your ~36 lands should "
            "produce blue mana."
        )
        lines.append("")
        lines.append(
            "Key insight: Colored pip density matters more than total CMC. "
            "A {U}{U}{U} spell at CMC 5 needs 23 blue sources, while "
            "a {U}{4} spell at CMC 5 needs only 13. Multicolor decks must "
            "prioritize dual lands and color fixing over raw land count."
        )
        return "\n".join(lines)

    @staticmethod
    def _color_source_targets_section(patterns: DeckbuildingPatterns) -> str:
        """Generate the Color Source Requirements section."""
        lines = [
            "## Color Source Requirements",
            "",
            "Archetype targets (computed from Karsten framework):",
        ]

        archetype_descriptions = {
            "mono_color": "Mono-color",
            "two_color": "Two-color",
            "three_color": "Three-color",
            "four_color": "Four-color",
            "five_color": "Five-color",
        }

        mba = patterns.mana_base_analysis
        if mba and mba.archetype_targets:
            for key, label in archetype_descriptions.items():
                targets = mba.archetype_targets.get(key)
                if targets:
                    source_range = sorted(targets.values())
                    if len(source_range) == 1:
                        lines.append(
                            f"- {label}: {source_range[0]}+ sources of your color "
                            "(virtually all lands produce it)"
                        )
                    else:
                        lines.append(
                            f"- {label}: {source_range[0]}-{source_range[-1]} "
                            f"sources per color"
                        )
        else:
            # Fallback static guidance when no analysis data
            lines.extend([
                "- Mono-color: 22+ sources of your color (virtually all lands produce it)",
                "- Two-color: 17-19 sources of primary, 15-17 of secondary",
                "- Three-color: 13-17 sources per color (heavy use of duals/tri-lands)",
                "- Four/five-color: 11-13 per color; requires mana-fixing lands",
            ])

        # Observed quality from Game Knights data
        if mba and mba.quality_scores.mean > 0:
            lines.append("")
            lines.append("Game Knights observed mana base quality:")
            lines.append(
                f"- Mean quality score: {mba.quality_scores.mean} (scale 0-1)"
            )
            if mba.color_source_counts:
                avg_sources = [
                    s.mean for s in mba.color_source_counts.values() if s.mean > 0
                ]
                if avg_sources:
                    overall_avg = round(
                        sum(avg_sources) / len(avg_sources), 1
                    )
                    lines.append(
                        f"- Average color sources per commander color: {overall_avg}"
                    )
            if mba.etb_tapped_ratio.mean > 0:
                pct = round(mba.etb_tapped_ratio.mean * 100, 1)
                lines.append(f"- Average ETB-tapped ratio: {pct}%")

        lines.append("")
        lines.append(
            "Guidance: Prioritize untapped dual lands and fetch lands in "
            "multicolor decks. ETB-tapped lands are acceptable in budget "
            "builds but should be kept below 30% of the mana base for "
            "consistent early-game plays."
        )
        return "\n".join(lines)

    def _ramp_section(
        self, patterns: DeckbuildingPatterns, articles: str
    ) -> str:
        """Generate the Ramp section."""
        s = patterns.ramp_counts
        lines = [
            "## Ramp",
            "",
            f"Game Knights data ({patterns.deck_count} decks):",
            f"- Mean: {s.mean} ramp sources",
            f"- Median: {s.median} ramp sources",
            f"- Range: {s.min} - {s.max}",
            f"- Std dev: {s.std_dev}",
        ]
        guidance = self._extract_guidance(articles, ["ramp", "mana acceleration", "mana rock"])
        if guidance:
            lines.append("")
            lines.append("EDHREC guidance:")
            lines.append(guidance)
        lines.append("")
        lines.append(
            "Recommendation: 10-12 ramp sources is the standard baseline. "
            "Include a mix of mana rocks, dorks, and land-based ramp."
        )
        return "\n".join(lines)

    def _card_draw_section(
        self, patterns: DeckbuildingPatterns, articles: str
    ) -> str:
        """Generate the Card Draw section."""
        s = patterns.draw_counts
        lines = [
            "## Card Draw",
            "",
            f"Game Knights data ({patterns.deck_count} decks):",
            f"- Mean: {s.mean} draw sources",
            f"- Median: {s.median} draw sources",
            f"- Range: {s.min} - {s.max}",
            f"- Std dev: {s.std_dev}",
        ]
        guidance = self._extract_guidance(articles, ["card draw", "card advantage", "draw engine"])
        if guidance:
            lines.append("")
            lines.append("EDHREC guidance:")
            lines.append(guidance)
        lines.append("")
        lines.append(
            "Recommendation: 10+ dedicated draw sources keep your hand full. "
            "Prioritize repeatable draw engines over one-shot effects."
        )
        return "\n".join(lines)

    def _removal_section(
        self, patterns: DeckbuildingPatterns, articles: str
    ) -> str:
        """Generate the Removal & Interaction section."""
        r = patterns.removal_counts
        w = patterns.wipe_counts
        t = patterns.tutor_counts
        lines = [
            "## Removal & Interaction",
            "",
            f"Game Knights data ({patterns.deck_count} decks):",
            f"- Targeted removal: mean {r.mean}, median {r.median} (range {r.min}-{r.max})",
            f"- Board wipes: mean {w.mean}, median {w.median} (range {w.min}-{w.max})",
            f"- Tutors: mean {t.mean}, median {t.median} (range {t.min}-{t.max})",
        ]
        guidance = self._extract_guidance(articles, ["removal", "interaction", "board wipe"])
        if guidance:
            lines.append("")
            lines.append("EDHREC guidance:")
            lines.append(guidance)
        lines.append("")
        lines.append(
            "Recommendation: 8-12 targeted removal pieces and 2-4 board wipes. "
            "Include a mix of instant-speed interaction and flexible answers."
        )
        return "\n".join(lines)

    def _mana_curve_section(
        self, patterns: DeckbuildingPatterns, articles: str
    ) -> str:
        """Generate the Mana Curve section."""
        a = patterns.avg_cmc
        lines = [
            "## Mana Curve",
            "",
            f"Game Knights data ({patterns.deck_count} decks):",
            f"- Average CMC: mean {a.mean}, median {a.median}",
            f"- Range: {a.min} - {a.max}",
            "",
            "Average cards per CMC bucket (per deck):",
        ]
        for bucket, avg_count in sorted(patterns.mana_curve.items()):
            label = f"{bucket}+" if bucket == 7 else str(bucket)
            lines.append(f"- CMC {label}: {avg_count}")
        guidance = self._extract_guidance(articles, ["mana curve", "cmc", "mana value"])
        if guidance:
            lines.append("")
            lines.append("EDHREC guidance:")
            lines.append(guidance)
        lines.append("")
        lines.append(
            "Recommendation: Keep average CMC between 2.5 and 3.5. "
            "Heavy top-end requires proportionally more ramp."
        )
        return "\n".join(lines)

    def _power_level_section(self, patterns: DeckbuildingPatterns) -> str:
        """Generate the Power Level section."""
        lines = [
            "## Power Level Context",
            "",
            "Game Knights decks are built for entertainment and showcase play. "
            "They typically target bracket 2-3 (mid-power) with splashy, "
            "interactive game plans. These patterns reflect decks designed "
            "for a fun viewing experience, not competitive optimization.",
        ]
        if patterns.color_distribution:
            lines.append("")
            lines.append("Color distribution across decks:")
            for color, count in sorted(
                patterns.color_distribution.items(),
                key=lambda x: x[1],
                reverse=True,
            ):
                lines.append(f"- {color}: {count} decks")
        return "\n".join(lines)

    def _most_popular_section(self, patterns: DeckbuildingPatterns) -> str:
        """Generate the Most Popular Cards section."""
        lines = [
            "## Most Popular Cards",
            "",
            f"Non-land cards appearing in multiple Game Knights decks "
            f"({patterns.deck_count} total):",
        ]
        for entry in patterns.most_played_cards[:25]:
            lines.append(
                f"- {entry['name']}: {entry['deck_count']} decks "
                f"({entry['percentage']}%)"
            )
        if not patterns.most_played_cards:
            lines.append("- No cards appeared in multiple decks")
        return "\n".join(lines)

    def _budget_section(
        self, patterns: DeckbuildingPatterns, articles: str
    ) -> str:
        """Generate the Budget section."""
        lines = [
            "## Budget Considerations",
            "",
            "Game Knights decks have access to premium cards but still "
            "follow coherent deckbuilding principles. The patterns above "
            "apply at any budget — the ratios (lands, ramp, draw, removal) "
            "matter more than the specific cards chosen.",
        ]
        guidance = self._extract_guidance(articles, ["budget", "price", "affordable"])
        if guidance:
            lines.append("")
            lines.append("EDHREC guidance:")
            lines.append(guidance)
        return "\n".join(lines)

    @staticmethod
    def _extract_guidance(articles_text: str, keywords: list[str]) -> str:
        """Extract relevant sentences from article text matching keywords.

        Args:
            articles_text: Combined text from EDHREC articles.
            keywords: Keywords to search for in sentences.

        Returns:
            Extracted relevant text (max ~500 chars), or empty string.
        """
        if not articles_text:
            return ""

        sentences = articles_text.replace("\n", " ").split(".")
        relevant: list[str] = []
        total_len = 0

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence or len(sentence) < 20:
                continue
            lower = sentence.lower()
            if any(kw in lower for kw in keywords):
                relevant.append(sentence + ".")
                total_len += len(sentence)
                if total_len > 500:
                    break

        return " ".join(relevant[:5])
