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
    count_board_wipes,
    count_card_draw,
    count_ramp_spells,
    count_removal,
    count_tutors,
)

logger = logging.getLogger(__name__)


class ComponentStats(BaseModel):
    """Descriptive statistics for a single deck metric."""

    mean: float = 0.0
    median: float = 0.0
    min: float = 0.0
    max: float = 0.0
    std_dev: float = 0.0


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
            "Decks with low average CMC or high ramp counts can go lower."
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
