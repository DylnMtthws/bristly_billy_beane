"""Commander profile generator (D5.5).

Generates or retrieves cached commander profiles via LLM synthesis.
Follows api_contracts.md Section 1.4 (ProfileManager contract).
"""

import hashlib
import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from sabermetrics.errors import CommanderNotFoundError, DegradableError
from sabermetrics.models.profile import CommanderProfile
from sabermetrics.reference_layer.evidence import EvidenceAggregator

logger = logging.getLogger(__name__)


class ProfileRequest(BaseModel):
    """Request for commander profile generation."""

    commander_id: str
    user_intent: str | None = None
    force_refresh: bool = False


class ProfileResult(BaseModel):
    """Result of profile generation."""

    profile: CommanderProfile
    cache_hit: bool
    generation_cost_usd: float
    generation_time_seconds: float


class ProfileManager:
    """Manages commander profile generation with caching."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._evidence_aggregator = EvidenceAggregator(db_path)

    def generate_profile(self, request: ProfileRequest) -> ProfileResult:
        """Get or generate a commander profile.

        Workflow:
            1. Check cache (commander_id + user_intent_hash + set_version)
            2. If cache hit and not force_refresh: return cached
            3. Aggregate evidence
            4. Retrieve reference chunks
            5. Call Sonnet via AnthropicClient
            6. Validate against CommanderProfile schema
            7. Persist to commander_profiles table
            8. Return result

        Args:
            request: Profile generation request.

        Returns:
            ProfileResult with profile and metadata.

        Raises:
            CommanderNotFoundError: If commander not in DB.
            DegradableError: LLM unavailable, returns cached if available.
        """
        start_time = time.time()

        # Compute cache key
        intent_hash = (
            hashlib.sha256(request.user_intent.encode()).hexdigest()[:16]
            if request.user_intent
            else None
        )

        # Check cache
        if not request.force_refresh:
            cached = self._get_cached_profile(
                request.commander_id, intent_hash
            )
            if cached is not None:
                elapsed = time.time() - start_time
                logger.info(
                    "Cache hit for commander %s (%.1fms)",
                    request.commander_id, elapsed * 1000,
                )
                return ProfileResult(
                    profile=cached,
                    cache_hit=True,
                    generation_cost_usd=0.0,
                    generation_time_seconds=elapsed,
                )

        # Aggregate evidence
        logger.info("Aggregating evidence for %s", request.commander_id)
        evidence = self._evidence_aggregator.aggregate(
            request.commander_id,
            user_intent=request.user_intent,
        )

        # Generate profile via LLM
        try:
            profile, cost = self._generate_via_llm(evidence, request)
        except Exception as e:
            # Try returning cached profile on LLM failure
            cached = self._get_cached_profile(
                request.commander_id, intent_hash
            )
            if cached is not None:
                logger.warning(
                    "LLM failed, returning stale cache: %s", e
                )
                elapsed = time.time() - start_time
                return ProfileResult(
                    profile=cached,
                    cache_hit=True,
                    generation_cost_usd=0.0,
                    generation_time_seconds=elapsed,
                )
            raise DegradableError(
                f"Profile generation failed and no cache available: {e}"
            ) from e

        # Persist
        self._store_profile(profile, request.user_intent, intent_hash)

        elapsed = time.time() - start_time
        logger.info(
            "Profile generated for %s in %.1fs ($%.4f)",
            evidence.commander.name, elapsed, cost,
        )

        return ProfileResult(
            profile=profile,
            cache_hit=False,
            generation_cost_usd=cost,
            generation_time_seconds=elapsed,
        )

    def _get_cached_profile(
        self, commander_id: str, intent_hash: str | None
    ) -> CommanderProfile | None:
        """Check for cached profile in database."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            if intent_hash:
                cursor = conn.execute(
                    "SELECT profile_json FROM commander_profiles "
                    "WHERE commander_id = ? AND user_intent_hash = ? "
                    "AND is_stale = 0",
                    (commander_id, intent_hash),
                )
            else:
                cursor = conn.execute(
                    "SELECT profile_json FROM commander_profiles "
                    "WHERE commander_id = ? AND user_intent_hash IS NULL "
                    "AND is_stale = 0",
                    (commander_id,),
                )

            row = cursor.fetchone()
            if row is None:
                return None

            profile_data = json.loads(row["profile_json"])
            return CommanderProfile(**profile_data)
        except Exception as e:
            logger.warning("Cache read failed: %s", e)
            return None
        finally:
            conn.close()

    def _generate_via_llm(
        self,
        evidence: "EvidencePackage",  # type: ignore[name-defined]
        request: ProfileRequest,
    ) -> tuple[CommanderProfile, float]:
        """Generate profile via Anthropic API.

        Returns:
            Tuple of (CommanderProfile, cost_usd).
        """
        from sabermetrics.config import settings
        from sabermetrics.reasoning.client import AnthropicClient
        from sabermetrics.reasoning.prompts import load_prompt

        client = AnthropicClient.get_instance(self.db_path)
        template = load_prompt("profile_synthesis")

        # Format evidence into template variables
        reference_text = "\n\n".join(
            f"[{c.document}/{c.section or 'general'}]: {c.content}"
            for c in evidence.reference_chunks
        )

        rulings_text = "\n".join(
            f"- {r.ruling_text}" for r in evidence.rulings
        ) or "No specific rulings found."

        # EDHREC data formatting
        edhrec = evidence.edhrec_data or {}
        themes = edhrec.get("themes", [])
        if isinstance(themes, str):
            themes = json.loads(themes)
        top_cards = edhrec.get("top_cards", [])
        if isinstance(top_cards, str):
            top_cards = json.loads(top_cards)

        top_cards_text = "\n".join(
            f"- {tc.get('card_name', tc.get('name', '?'))}: "
            f"{tc.get('inclusion_pct', '?')}%"
            for tc in top_cards[:30]
        ) or "No EDHREC data available."

        # Tournament data
        tourney = evidence.tournament_data or {}
        tourney_wr = tourney.get("average_win_rate")
        tourney_wr_str = f"{tourney_wr:.1%}" if tourney_wr else "N/A"
        tourney_sample = tourney.get("tournament_count", 0)

        # Reddit topics
        reddit_topics = "\n".join(
            f"- {t.title} ({t.upvotes} upvotes)"
            for t in evidence.reddit_threads[:10]
        ) or "No Reddit discussions found."

        # User intent section
        user_intent_section = ""
        if evidence.user_intent:
            user_intent_section = (
                f"<user_intent>\n"
                f"The user has specified: {evidence.user_intent}\n"
                f"Adjust the profile to reflect this intent while noting "
                f"any divergence from consensus strategies.\n"
                f"</user_intent>"
            )

        # Profile schema (simplified for the LLM)
        profile_schema = json.dumps({
            "commander_id": "string (Scryfall ID)",
            "commander_name": "string",
            "generated_at": "ISO datetime",
            "set_version": "string (latest set code)",
            "card_analysis": {
                "mana_cost": "string", "color_identity": ["string"],
                "core_mechanic": "string",
                "triggered_abilities": ["string"],
                "activated_abilities": ["string"],
                "static_abilities": ["string"],
                "evasion_or_protection": "string or null",
            },
            "behavioral_signals": {
                "total_decks_tracked": "int",
                "edhrec_themes": ["string"],
                "most_included_cards": [{"card_name": "str", "inclusion_pct": 0.0}],
                "average_deck_price_usd": 0.0,
                "average_cmc": 0.0,
                "tournament_win_rate": "float or null",
                "tournament_sample_size": 0,
            },
            "community_signals": {
                "reddit_thread_count": "int",
                "named_archetypes": ["string"],
                "primer_articles_referenced": ["string"],
                "emerging_strategies": ["string"],
            },
            "strategic_profile": {
                "primary_archetype": "string",
                "game_plan_summary": "string",
                "win_conditions": [{"description": "str", "key_cards": ["str"], "reliability": "primary|secondary|backup"}],
                "build_paths": [{"name": "str", "description": "str", "consensus_status": "mainstream|emerging|underexplored", "key_card_categories": ["str"]}],
                "synergy_priorities": {"high": ["str"], "medium": ["str"], "low": ["str"]},
                "anti_synergies": [{"description": "str", "cards_to_avoid": ["str"], "reasoning": "str"}],
                "strategic_constraints": {"mana_base_requirements": "str", "interaction_density": "high|medium|low", "speed_tier": "fast|midrange|slow"},
                "power_indicators": {"estimated_ceiling_bracket": "1-5", "estimated_floor_bracket": "1-5", "notes": "str"},
            },
            "user_intent": {
                "provided": "bool",
                "description": "string or null",
                "divergence_from_consensus": "string or null",
            },
            "sources": {
                "rules_chunks_referenced": ["string"],
                "articles_referenced": ["string"],
                "evidence_freshness": {
                    "edhrec_last_updated": "datetime or null",
                    "topdeck_last_updated": "datetime or null",
                    "reddit_last_searched": "datetime or null",
                },
            },
        }, indent=2)

        # Format prompt
        commander = evidence.commander
        prompt_text = template.format(
            reference_chunks=reference_text,
            commander_name=commander.name,
            mana_cost=commander.mana_cost or "N/A",
            type_line=commander.type_line,
            oracle_text=commander.oracle_text or "No oracle text",
            color_identity=", ".join(commander.color_identity),
            keywords=", ".join(commander.keywords) if commander.keywords else "None",
            commander_rulings=rulings_text,
            deck_count=edhrec.get("deck_count", 0),
            edhrec_themes=", ".join(themes) if themes else "None identified",
            top_cards_list=top_cards_text,
            avg_price=f"{edhrec.get('avg_deck_price', 0):.2f}",
            avg_cmc=f"{edhrec.get('avg_cmc', 0):.2f}",
            tourney_winrate=tourney_wr_str,
            tourney_sample=tourney_sample,
            named_archetypes=", ".join(themes[:5]) if themes else "None",
            reddit_topics=reddit_topics,
            primer_summaries="None available",
            user_intent_section=user_intent_section,
            profile_schema=profile_schema,
        )

        # System prompt (cached)
        system = (
            "You are an expert Magic: The Gathering Commander format "
            "strategist. You generate structured profiles for commanders "
            "based on evidence triangulation. Always output valid JSON."
        )

        # Make API call
        result = client.call_with_cache(
            model=settings.llm.profile_model,
            system=system,
            messages=[{"role": "user", "content": prompt_text}],
            cache_breakpoints=[],
            max_tokens=8000,
            temperature=0.0,
            call_type="profile_synthesis",
        )

        # Parse and validate response
        response_text = result.content.strip()
        # Extract JSON from potential markdown code blocks
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            json_lines = []
            in_block = False
            for line in lines:
                if line.startswith("```") and not in_block:
                    in_block = True
                    continue
                elif line.startswith("```") and in_block:
                    break
                elif in_block:
                    json_lines.append(line)
            response_text = "\n".join(json_lines)

        profile_data = json.loads(response_text)

        # Ensure required fields
        profile_data.setdefault("commander_id", evidence.commander.id)
        profile_data.setdefault("commander_name", evidence.commander.name)
        profile_data.setdefault("generated_at", datetime.now().isoformat())
        profile_data.setdefault("set_version", evidence.commander.set_code)

        # Ensure user_intent
        if "user_intent" not in profile_data:
            profile_data["user_intent"] = {
                "provided": bool(request.user_intent),
                "description": request.user_intent,
            }

        # Ensure sources
        if "sources" not in profile_data:
            profile_data["sources"] = {
                "rules_chunks_referenced": [
                    c.section or c.id for c in evidence.reference_chunks
                ],
                "articles_referenced": [],
                "evidence_freshness": {
                    "edhrec_last_updated": None,
                    "topdeck_last_updated": None,
                    "reddit_last_searched": datetime.now().isoformat()
                    if evidence.reddit_threads
                    else None,
                },
            }

        profile = CommanderProfile(**profile_data)
        return profile, result.cost_usd

    def _store_profile(
        self,
        profile: CommanderProfile,
        user_intent: str | None,
        intent_hash: str | None,
    ) -> None:
        """Persist profile to commander_profiles table."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            profile_json = profile.model_dump_json()

            conn.execute(
                "INSERT OR REPLACE INTO commander_profiles "
                "(commander_id, profile_json, user_intent, user_intent_hash, "
                "set_version, generated_at, is_stale, schema_version) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                (
                    profile.commander_id,
                    profile_json,
                    user_intent,
                    intent_hash,
                    profile.set_version,
                    profile.generated_at.isoformat(),
                    profile.schema_version,
                ),
            )
            conn.commit()
        finally:
            conn.close()
