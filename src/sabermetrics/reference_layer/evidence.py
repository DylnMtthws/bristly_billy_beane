"""Evidence aggregator for profile generation (D5.4).

Composes an EvidencePackage from multiple data sources:
- Card data from Scryfall (DB)
- Rulings from mtgapi (DB)
- EDHREC data (DB)
- Tournament data (DB)
- Reddit threads (on-demand)
- Reference chunks (embedding search)
"""

import json
import logging
import sqlite3
from pathlib import Path

from sabermetrics.db import row_to_card
from sabermetrics.errors import CommanderNotFoundError
from sabermetrics.models.card import Card, CardRuling
from sabermetrics.models.evidence import (
    EvidencePackage,
    PrimerArticle,
    ReferenceChunk,
    RedditThread,
)

logger = logging.getLogger(__name__)


class EvidenceAggregator:
    """Aggregates evidence from all sources for profile generation."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def aggregate(
        self,
        commander_id: str,
        user_intent: str | None = None,
        skip_reddit: bool = False,
    ) -> EvidencePackage:
        """Compose a complete evidence package for a commander.

        Args:
            commander_id: Scryfall card ID.
            user_intent: Optional user-provided build direction.
            skip_reddit: Skip Reddit search (for testing/offline).

        Returns:
            EvidencePackage with all available evidence.

        Raises:
            CommanderNotFoundError: If commander_id not in DB.
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row

        try:
            # 1. Get commander card data
            commander = self._get_commander(conn, commander_id)

            # 2. Get rulings
            rulings = self._get_rulings(conn, commander.oracle_id)

            # 3. Get EDHREC data
            edhrec_data = self._get_edhrec_data(conn, commander_id)

            # 4. Get tournament data
            tournament_data = self._get_tournament_data(conn, commander_id)

            # 5. Reddit threads (on-demand, optional)
            reddit_threads: list[RedditThread] = []
            if not skip_reddit:
                reddit_threads = self._search_reddit(commander.name)

            # 6. Reference chunks (rules relevant to commander)
            reference_chunks = self._get_reference_chunks(commander)

            # 7. Extract referenced keywords/mechanics from oracle text
            from sabermetrics.analytics.oracle_keywords import (
                extract_referenced_keywords,
                extract_referenced_mechanics,
            )

            ref_keywords = extract_referenced_keywords(commander.oracle_text)
            ref_mechanics = extract_referenced_mechanics(commander.oracle_text)

            return EvidencePackage(
                commander=commander,
                rulings=rulings,
                edhrec_data=edhrec_data,
                tournament_data=tournament_data,
                reddit_threads=reddit_threads,
                primer_articles=[],  # Articles deferred
                reference_chunks=reference_chunks,
                user_intent=user_intent,
                referenced_keywords=ref_keywords,
                referenced_mechanics=ref_mechanics,
            )

        finally:
            conn.close()

    def _get_commander(
        self, conn: sqlite3.Connection, commander_id: str
    ) -> Card:
        """Load commander card from database."""
        cursor = conn.execute(
            "SELECT * FROM cards WHERE id = ?", (commander_id,)
        )
        row = cursor.fetchone()
        if row is None:
            raise CommanderNotFoundError(
                f"Commander not found in DB: {commander_id}"
            )

        price_row = conn.execute(
            "SELECT price_usd FROM card_prices "
            "WHERE card_id = ? ORDER BY snapshot_date DESC LIMIT 1",
            (commander_id,),
        ).fetchone()
        price = price_row["price_usd"] if price_row else None

        return row_to_card(row, price_usd=price)

    def _get_rulings(
        self, conn: sqlite3.Connection, oracle_id: str
    ) -> list[CardRuling]:
        """Get card rulings from database."""
        cursor = conn.execute(
            "SELECT ruling_date, ruling_text, source FROM card_rulings "
            "WHERE card_oracle_id = ?",
            (oracle_id,),
        )
        return [
            CardRuling(
                ruling_date=row["ruling_date"],
                ruling_text=row["ruling_text"],
                source=row["source"],
            )
            for row in cursor
        ]

    def _get_edhrec_data(
        self, conn: sqlite3.Connection, commander_id: str
    ) -> dict | None:
        """Get EDHREC data for commander."""
        cursor = conn.execute(
            "SELECT * FROM edhrec_commander_data WHERE commander_id = ?",
            (commander_id,),
        )
        row = cursor.fetchone()
        if row is None:
            # Try matching by name
            name_cursor = conn.execute(
                "SELECT name FROM cards WHERE id = ?", (commander_id,)
            )
            name_row = name_cursor.fetchone()
            if name_row:
                # Search by name in edhrec
                cursor = conn.execute(
                    "SELECT e.* FROM edhrec_commander_data e "
                    "JOIN cards c ON e.commander_id = c.id "
                    "WHERE c.name = ?",
                    (name_row["name"],),
                )
                row = cursor.fetchone()

        if row is None:
            return None

        data = dict(row)
        # Parse JSON fields
        for field in ("themes", "top_cards"):
            val = data.get(field, "[]")
            if isinstance(val, str):
                data[field] = json.loads(val)
        return data

    def _get_tournament_data(
        self, conn: sqlite3.Connection, commander_id: str
    ) -> dict | None:
        """Get tournament performance data."""
        cursor = conn.execute(
            "SELECT COUNT(*) as count, "
            "AVG(win_rate) as avg_wr, "
            "SUM(games_won) as total_wins, "
            "SUM(games_played) as total_games "
            "FROM tournament_results "
            "WHERE commander_id = ?",
            (commander_id,),
        )
        row = cursor.fetchone()
        if row is None or row["count"] == 0:
            return None

        return {
            "tournament_count": row["count"],
            "average_win_rate": row["avg_wr"],
            "total_wins": row["total_wins"],
            "total_games": row["total_games"],
        }

    def _search_reddit(self, commander_name: str) -> list[RedditThread]:
        """Search Reddit for commander discussions."""
        try:
            from sabermetrics.ingestion.reddit import RedditSearch

            search = RedditSearch()
            threads = search.search_commander(
                commander_name, top_k=10, min_upvotes=10
            )
            logger.info(
                "Found %d Reddit threads for %s", len(threads), commander_name
            )
            return threads
        except Exception as e:
            logger.warning("Reddit search failed for %s: %s", commander_name, e)
            return []

    def _get_reference_chunks(self, commander: Card) -> list[ReferenceChunk]:
        """Retrieve relevant reference chunks via embedding search."""
        try:
            from sabermetrics.analytics.oracle_keywords import (
                extract_referenced_keywords,
                extract_referenced_mechanics,
            )
            from sabermetrics.reference_layer.retriever import (
                ReferenceQuery,
                ReferenceRetriever,
            )

            retriever = ReferenceRetriever(self.db_path)

            # Query for commander-relevant rules
            queries = [
                f"{commander.name} commander strategy",
                f"color identity {' '.join(commander.color_identity)}",
            ]

            # Add keyword-specific queries
            if commander.keywords:
                for kw in commander.keywords[:3]:
                    queries.append(f"{kw} keyword ability rules")

            # Add oracle-text-referenced keyword queries
            ref_keywords = extract_referenced_keywords(commander.oracle_text)
            for ref_kw in ref_keywords[:3]:
                queries.append(f"{ref_kw} keyword ability rules")
                queries.append(f"creatures with {ref_kw} strategy")

            # Add mechanic-based queries
            ref_mechanics = extract_referenced_mechanics(commander.oracle_text)
            mechanic_query_map = {
                "toughness_matters": "toughness matters combat damage strategy",
                "artifact_creature": "artifact creature synergy strategy",
                "enchantment_creature": "enchantment creature synergy strategy",
                "tap_synergy": "tap untap creatures freeze lock down strategy",
                "face_down_synergy": "morph manifest disguise face-down creature strategy",
                "sacrifice_synergy": "sacrifice aristocrats death trigger strategy",
                "cost_reduction": "cost reduction cheat mana expensive spells strategy",
                "counters_matter": "plus one counters proliferate strategy",
                "death_trigger": "death trigger dies aristocrats strategy",
                "graveyard_synergy": "graveyard recursion flashback unearth strategy",
                "token_synergy": "token generation populate strategy",
                "spellslinger": "instant sorcery spellslinger prowess strategy",
            }
            for mech in ref_mechanics:
                query_text = mechanic_query_map.get(mech)
                if query_text:
                    queries.append(query_text)

            all_chunks: list[ReferenceChunk] = []
            seen_ids: set[str] = set()

            for query_text in queries:
                query = ReferenceQuery(
                    query_text=query_text, tier_filter=[1, 2], top_k=5
                )
                results = retriever.retrieve(query)
                for r in results:
                    if r.id not in seen_ids:
                        seen_ids.add(r.id)
                        all_chunks.append(
                            ReferenceChunk(
                                id=r.id,
                                document=r.document,
                                section=r.section,
                                tier=r.tier,
                                content=r.content,
                            )
                        )

            logger.info(
                "Retrieved %d reference chunks for %s",
                len(all_chunks), commander.name,
            )
            return all_chunks[:15]  # Cap at 15 chunks

        except Exception as e:
            logger.warning("Reference retrieval failed: %s", e)
            return []
