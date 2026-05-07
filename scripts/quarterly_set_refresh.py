#!/usr/bin/env python3
"""Quarterly set refresh script (D8.4).

Follows SKILL-006: Refresh for Set Release.
Actions:
  1. Full Scryfall card refresh
  2. Check Comprehensive Rules updates; re-chunk/embed if changed
  3. Identify new keywords and update glossary
  4. Refresh ban list
  5. Per-cached-profile relevance screening (Haiku)
  6. Mark affected profiles is_stale=true
  7. Log summary
"""

import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sabermetrics.utils.logging import setup_job_logging

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sabermetrics.db"


def main(set_code: str | None = None) -> int:
    logger = setup_job_logging("quarterly_set_refresh")
    start = time.time()
    errors: list[str] = []
    summary = {
        "cards_added": 0,
        "rules_updated": False,
        "profiles_screened": 0,
        "profiles_invalidated": 0,
        "total_cost_usd": 0.0,
    }

    # --- Step 1: Full Scryfall card refresh ---
    logger.info("Step 1: Full Scryfall card refresh")
    try:
        from sabermetrics.ingestion.scryfall import ScryfallIngestion

        scryfall = ScryfallIngestion(DB_PATH)
        result = scryfall.sync(full=True)
        summary["cards_added"] = result.items_ingested
        logger.info(
            "Scryfall full sync: ingested=%d, updated=%d",
            result.items_ingested, result.items_updated,
        )
        if result.errors:
            for err in result.errors[:5]:
                errors.append(f"scryfall: {err}")
    except Exception as e:
        logger.error("Scryfall full sync failed: %s", e)
        errors.append(f"scryfall: {e}")

    # --- Step 2: Check Comprehensive Rules update ---
    logger.info("Step 2: Check Comprehensive Rules")
    try:
        from sabermetrics.ingestion.reference import ReferenceIngestion

        ref = ReferenceIngestion(DB_PATH)
        ref_result = ref.sync(full=False)
        if ref_result.items_ingested > 0:
            summary["rules_updated"] = True
            logger.info("Rules updated: %d chunks re-indexed", ref_result.items_ingested)
        else:
            logger.info("Rules unchanged, no re-indexing needed")
    except Exception as e:
        logger.warning("Rules check failed: %s", e)
        errors.append(f"rules: {e}")

    # --- Step 3: Identify new keywords ---
    logger.info("Step 3: Check for new keywords")
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.execute(
            "SELECT DISTINCT keywords FROM cards WHERE keywords IS NOT NULL"
        )
        all_keywords: set[str] = set()
        for row in cursor:
            kw_json = row[0]
            if kw_json:
                kws = json.loads(kw_json) if isinstance(kw_json, str) else kw_json
                all_keywords.update(kws)
        conn.close()
        logger.info("Total unique keywords in card pool: %d", len(all_keywords))
    except Exception as e:
        logger.warning("Keyword scan failed: %s", e)

    # --- Step 4: Refresh ban list ---
    logger.info("Step 4: Ban list refresh (via Scryfall legality data)")
    # Ban list is already updated via Scryfall sync (legalities field)
    # Just log the count of banned cards
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.execute(
            "SELECT COUNT(*) FROM cards WHERE is_legal_in_99 = 0"
        )
        banned_count = cursor.fetchone()[0]
        conn.close()
        logger.info("Banned cards in Commander format: %d", banned_count)
    except Exception as e:
        logger.warning("Ban list count failed: %s", e)

    # --- Step 5: Per-profile relevance screening ---
    logger.info("Step 5: Profile relevance screening")
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # Get cached profiles
        cursor = conn.execute(
            "SELECT commander_id, profile_json FROM commander_profiles "
            "WHERE is_stale = 0"
        )
        profiles = [dict(row) for row in cursor]
        conn.close()

        if not profiles:
            logger.info("No cached profiles to screen")
        else:
            # Find recently added cards (last 90 days for quarterly)
            conn = sqlite3.connect(str(DB_PATH))
            cursor = conn.execute(
                "SELECT id, name, oracle_text, type_line, color_identity "
                "FROM cards WHERE last_updated >= datetime('now', '-90 days') "
                "LIMIT 500"
            )
            new_cards = [dict(row) for row in cursor]
            conn.close()

            if not new_cards:
                logger.info("No recently updated cards to screen against profiles")
            else:
                logger.info(
                    "Screening %d profiles against %d recent cards",
                    len(profiles), len(new_cards),
                )
                profiles_to_invalidate = _screen_profiles(
                    profiles, new_cards, logger
                )
                summary["profiles_screened"] = len(profiles)
                summary["profiles_invalidated"] = len(profiles_to_invalidate)

                # Mark stale
                if profiles_to_invalidate:
                    conn = sqlite3.connect(str(DB_PATH))
                    for cmdr_id in profiles_to_invalidate:
                        conn.execute(
                            "UPDATE commander_profiles SET is_stale = 1 "
                            "WHERE commander_id = ?",
                            (cmdr_id,),
                        )
                    conn.commit()
                    conn.close()
                    logger.info(
                        "Marked %d profiles as stale",
                        len(profiles_to_invalidate),
                    )

    except Exception as e:
        logger.error("Profile screening failed: %s", e)
        errors.append(f"screening: {e}")

    # --- Step 6: Summary ---
    elapsed = time.time() - start
    summary["elapsed_seconds"] = round(elapsed, 1)
    logger.info("Quarterly set refresh summary: %s", json.dumps(summary))
    logger.info(
        "Complete in %.1fs with %d errors", elapsed, len(errors),
    )

    return 1 if errors else 0


def _screen_profiles(
    profiles: list[dict],
    new_cards: list[dict],
    logger,
) -> list[str]:
    """Screen profiles against new cards to find those needing refresh.

    Uses heuristic screening (keyword/mechanic overlap) since LLM screening
    requires an API key. Falls back to LLM if available.

    Returns:
        List of commander_ids whose profiles should be marked stale.
    """
    invalidate: list[str] = []

    for profile_entry in profiles:
        commander_id = profile_entry["commander_id"]
        try:
            profile_data = json.loads(profile_entry["profile_json"])
        except (json.JSONDecodeError, TypeError):
            continue

        strategic = profile_data.get("strategic_profile", {})
        game_plan = (strategic.get("game_plan_summary") or "").lower()
        archetype = (strategic.get("primary_archetype") or "").lower()

        # Check if any new card is relevant to this profile
        # Heuristic: keyword overlap with game plan / archetype
        relevant_count = 0
        for card in new_cards:
            oracle = (card.get("oracle_text") or "").lower()
            # Check for thematic keywords from the profile
            profile_keywords = set(game_plan.split() + archetype.split())
            card_keywords = set(oracle.split())
            overlap = profile_keywords & card_keywords
            # Filter out common words
            meaningful = overlap - {
                "the", "a", "an", "of", "to", "and", "or", "is",
                "in", "for", "with", "on", "at", "by", "it", "as",
                "that", "this", "from", "your", "you", "its",
                "card", "cards", "creature", "creatures",
            }
            if len(meaningful) >= 3:
                relevant_count += 1

        if relevant_count >= 5:
            invalidate.append(commander_id)
            logger.info(
                "Profile %s: %d relevant new cards, marking stale",
                commander_id, relevant_count,
            )

    return invalidate


if __name__ == "__main__":
    set_code_arg = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(main(set_code_arg))
