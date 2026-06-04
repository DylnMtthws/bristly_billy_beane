"""Knowledge base document generation from deckbuilding patterns.

Renders a structured Markdown knowledge base from a
:class:`~sabermetrics.analytics.deck_stats.DeckbuildingPatterns` aggregate (as
produced by :class:`~sabermetrics.analytics.deck_stats.GameKnightsAnalyzer`),
combined with EDHREC article guidance, for RAG grounding.

Split out of the former ``deck_patterns`` module, which now re-exports this
class for backward compatibility.
"""

from sabermetrics.analytics.deck_stats import DeckbuildingPatterns
from sabermetrics.pipeline.mana_base import KARSTEN_SOURCES_99


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

        # New statistical sections (conditional on data presence)
        if patterns.card_type_distribution is not None:
            sections.append(self._card_type_distribution_section(patterns))
        if patterns.archetype_profiles:
            sections.append(self._archetype_profiles_section(patterns))
        if patterns.theme_densities:
            sections.append(self._theme_density_section(patterns))

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
    def _card_type_distribution_section(patterns: DeckbuildingPatterns) -> str:
        """Generate the Card Type Distribution section."""
        ctd = patterns.card_type_distribution
        if ctd is None:
            return ""
        lines = [
            "## Card Type Distribution",
            "",
            f"Per-deck averages across {patterns.deck_count} Game Knights decks:",
            f"- Creatures: mean {ctd.creatures.mean}, median {ctd.creatures.median} (range {ctd.creatures.min}-{ctd.creatures.max})",
            f"- Instants: mean {ctd.instants.mean}, median {ctd.instants.median} (range {ctd.instants.min}-{ctd.instants.max})",
            f"- Sorceries: mean {ctd.sorceries.mean}, median {ctd.sorceries.median} (range {ctd.sorceries.min}-{ctd.sorceries.max})",
            f"- Enchantments: mean {ctd.enchantments.mean}, median {ctd.enchantments.median} (range {ctd.enchantments.min}-{ctd.enchantments.max})",
            f"- Artifacts: mean {ctd.artifacts.mean}, median {ctd.artifacts.median} (range {ctd.artifacts.min}-{ctd.artifacts.max})",
            f"- Planeswalkers: mean {ctd.planeswalkers.mean}, median {ctd.planeswalkers.median} (range {ctd.planeswalkers.min}-{ctd.planeswalkers.max})",
            "",
            "Guidance: Use these baselines to calibrate card type ratios. "
            "Strategy-specific decks deviate significantly — see Archetype "
            "Profiles for theme-conditioned distributions.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _archetype_profiles_section(patterns: DeckbuildingPatterns) -> str:
        """Generate the Deckbuilding Archetype Profiles section."""
        if not patterns.archetype_profiles:
            return ""

        lines = [
            "## Deckbuilding Archetype Profiles",
            "",
            "Strategy-conditioned composition breakdowns from Game Knights decks. "
            "Use these to adjust baselines when building for a specific theme.",
        ]

        for profile in patterns.archetype_profiles:
            display_name = profile.archetype_name.replace("_", " ").title()
            lines.append("")
            lines.append(f"### {display_name} ({profile.deck_count} decks)")
            lines.append(f"- Lands: mean {profile.land_counts.mean}")
            lines.append(f"- Ramp: mean {profile.ramp_counts.mean}")
            lines.append(f"- Draw: mean {profile.draw_counts.mean}")
            lines.append(f"- Removal: mean {profile.removal_counts.mean}")
            lines.append(f"- Board wipes: mean {profile.wipe_counts.mean}")
            lines.append(f"- Creatures: mean {profile.creature_counts.mean}")
            lines.append(f"- Avg CMC: mean {profile.avg_cmc.mean}")
            lines.append(f"- Theme density: {profile.avg_theme_density} cards/deck")
            if profile.top_cards:
                lines.append("- Top cards:")
                for card in profile.top_cards[:5]:
                    lines.append(
                        f"  - {card['name']} ({card['percentage']}% of {display_name} decks)"
                    )

        return "\n".join(lines)

    @staticmethod
    def _theme_density_section(patterns: DeckbuildingPatterns) -> str:
        """Generate the Theme Density Patterns section."""
        if not patterns.theme_densities:
            return ""

        lines = [
            "## Theme Density Patterns",
            "",
            "How frequently each mechanic theme appears across Game Knights decks "
            f"({patterns.deck_count} total). Themes detected by oracle text pattern matching.",
            "",
            "| Theme | Avg Cards/Deck | Decks with 3+ | % of Decks |",
            "|---|---|---|---|",
        ]

        for td in patterns.theme_densities:
            display = td.theme_name.replace("_", " ").title()
            lines.append(
                f"| {display} | {td.card_count_stats.mean} | "
                f"{td.deck_count_with_theme} | {td.percentage_of_decks}% |"
            )

        # Top correlations
        if patterns.feature_correlations:
            lines.append("")
            lines.append("### Notable Feature Correlations")
            lines.append("")
            top_n = min(10, len(patterns.feature_correlations))
            for corr in patterns.feature_correlations[:top_n]:
                lines.append(
                    f"- {corr.description} (n={corr.sample_size})"
                )

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
