"""Deck builder orchestrator (D6.2, restructured for synergy optimizer).

8-stage pipeline:
1. Hard filters + role tag loading
2. Pareto filter (remove dominated cards per role)
3. Template derivation (profile-driven composition)
4. Infrastructure fill (4 deterministic generators)
5. Role targets + synergy matrix computation
6. Greedy optimizer + swap refinement (deterministic; no per-card LLM)
7. Budget redistribution (upgrade/downgrade passes)
7b. Enforce Commander legality (exactly 99, singleton, in color identity)
8. Synthesis + classify + persist
"""

import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from sabermetrics.errors import FatalError
from sabermetrics.models.card import Card
from sabermetrics.pipeline.trace import GenerationTracer
from sabermetrics.models.deck import (
    CVARWeights,
    CardSubScores,
    ComponentCounts,
    DeckCard,
    DeckClassification,
    DeckComposition,
    DeckNarrative,
    DeckParameters,
    GeneratedDeck,
    GenerationMeta,
    LLMFit,
)

logger = logging.getLogger(__name__)


class DeckBuildRequest(BaseModel):
    """Request for deck generation."""

    commander_id: str
    budget_usd: float = 200.0
    power_target: int = Field(default=3, ge=1, le=5)
    strategy: str | None = None
    weights: CVARWeights | None = None
    user_intent: str | None = None
    deck_name: str | None = None
    trace_cards: list[str] | None = None


class DeckBuildResult(BaseModel):
    """Result of deck generation."""

    deck: GeneratedDeck
    profile_was_generated: bool
    total_cost_usd: float
    total_time_seconds: float
    pipeline_metrics: dict


def _tokenize_engine_traits(raw_traits: list[str]) -> list[str]:
    """Extract matchable MTG keywords from LLM-generated trait descriptions.

    Profile engine_card_traits are full sentences (e.g. "Has the 'defender'
    keyword in oracle text") that never match substring checks against card
    oracle text. This tokenizes them into short MTG keywords like "defender".

    Args:
        raw_traits: LLM-generated trait description strings.

    Returns:
        Sorted list of unique matchable keyword strings.
    """
    from sabermetrics.analytics.oracle_keywords import MTG_KEYWORD_ABILITIES

    tokens: set[str] = set()
    type_keywords = {"wall", "artifact", "enchantment", "creature", "instant", "sorcery"}
    for trait in raw_traits:
        trait_lower = trait.lower()
        for kw in MTG_KEYWORD_ABILITIES:
            if kw in trait_lower:
                tokens.add(kw)
        for type_kw in type_keywords:
            if type_kw in trait_lower:
                tokens.add(type_kw)
    return sorted(tokens)


class DeckBuilder:
    """Orchestrates end-to-end deck generation."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def build(self, request: DeckBuildRequest) -> DeckBuildResult:
        """Execute the 8-stage deck building pipeline.

        Args:
            request: Deck build parameters.

        Returns:
            DeckBuildResult with generated deck and metadata.
        """
        start_time = time.time()
        metrics: dict[str, float] = {}
        total_cost = 0.0

        # --- Build trace watchlist and create tracer ---
        watchlist: set[str] = set()
        if request.trace_cards:
            watchlist.update(request.trace_cards)
        # Add all auto-include card names
        try:
            from sabermetrics.pipeline.generators.ramp import _load_auto_includes
            auto_inc, _ = _load_auto_includes()
            for section_entries in auto_inc.values():
                if isinstance(section_entries, list):
                    for entry in section_entries:
                        watchlist.add(entry["name"])
        except Exception:
            pass
        self._tracer = GenerationTracer(
            generation_id="pending",
            watchlist=watchlist,
        )

        # --- Validate & Profile (unchanged) ---
        t = time.time()
        commander = self._validate_request(request)
        metrics["1_validate"] = time.time() - t

        t = time.time()
        profile_result = self._acquire_profile(request, commander)
        profile = profile_result.profile
        total_cost += profile_result.generation_cost_usd
        metrics["2_profile"] = time.time() - t

        # --- Stage 1: Hard Filters + Role Tag Loading ---
        t = time.time()
        candidates = self._filter_candidates(request, commander)
        candidates = self._load_role_tags(candidates)
        metrics["3_filter"] = time.time() - t
        logger.info("Stage 1: %d candidates after hard filters", len(candidates))

        # --- Stage 2: Pareto Filter ---
        t = time.time()
        candidates = self._structural_score(
            candidates, commander, request, profile_result
        )
        candidates = self._pareto_filter(candidates)
        metrics["4_pareto"] = time.time() - t
        logger.info("Stage 2: %d candidates after Pareto filter", len(candidates))

        # --- Stage 3: Template Derivation ---
        t = time.time()
        template = self._derive_template(profile, request)
        metrics["5_template"] = time.time() - t
        logger.info(
            "Stage 3: Template derived (%d land, %d ramp, %d draw, "
            "%d removal, %d diff)",
            template.land_count, template.ramp_count, template.draw_count,
            template.removal_count, template.differentiator_slots,
        )

        # --- Stage 4: Infrastructure Fill ---
        t = time.time()
        infrastructure, budget_used = self._fill_infrastructure(
            candidates, commander, request, template
        )
        metrics["6_infrastructure"] = time.time() - t
        logger.info(
            "Stage 4: %d infrastructure cards placed ($%.2f)",
            len(infrastructure), budget_used,
        )

        # --- Stage 5+6: Synergy optimizer (role targets + matrix + greedy + swap) ---
        t = time.time()
        all_assignments, opt_metrics = self._optimize_differentiators(
            candidates, infrastructure, profile_result, commander,
            request, template, budget_used,
        )
        total_cost += opt_metrics.get("llm_safety_cost", 0.0)
        metrics["7_optimizer"] = time.time() - t
        metrics.update({
            f"opt_{k}": v for k, v in opt_metrics.items()
            if k != "role_targets"
        })
        logger.info(
            "Stage 5+6: %d total cards, %d swaps, obj=%.4f",
            len(all_assignments),
            opt_metrics.get("cards_swapped", 0),
            opt_metrics.get("objective_score", 0),
        )

        # --- Stage 7: Budget Redistribution ---
        t = time.time()
        protected = getattr(self, "_protected_names", None) or set()
        all_assignments = self._redistribute_budget(
            all_assignments, request.budget_usd, candidates,
            protected_names=protected,
        )
        metrics["9_budget"] = time.time() - t

        # --- Stage 7b: Enforce Commander legality as a hard invariant ---
        all_assignments = self._enforce_legality(
            all_assignments, commander, protected_names=protected,
        )

        # --- Stage 8: Synthesis + Classify + Persist ---
        t = time.time()
        total_price = sum(
            float(a.card.get("price_usd", 0) or 0) for a in all_assignments
        )

        # Build AssemblyResult-compatible wrapper
        from sabermetrics.pipeline.slot_assigner import AssemblyResult
        target_comp = template.to_composition()
        actual_comp: dict[str, int] = {}
        for a in all_assignments:
            actual_comp[a.slot_role] = actual_comp.get(a.slot_role, 0) + 1

        assembly = AssemblyResult(
            assignments=all_assignments,
            composition=actual_comp,
            target_composition=target_comp,
            total_price=round(total_price, 2),
            warnings=[],
        )

        if len(all_assignments) < 99:
            assembly.warnings.append(
                f"Only {len(all_assignments)} cards, need 99."
            )

        narrative, synth_cost = self._synthesize_narrative(
            profile_result, assembly, request
        )
        total_cost += synth_cost

        classification = self._classify_bracket(assembly)

        deck = self._build_deck_model(
            commander=commander,
            request=request,
            profile=profile,
            assembly=assembly,
            narrative=narrative,
            classification=classification,
            total_cost=total_cost,
            start_time=start_time,
        )
        self._persist_deck(deck)

        # Flush trace events keyed to the real deck ID
        self._tracer.set_generation_id(deck.id)
        trace_count = self._tracer.flush(self.db_path)
        if trace_count:
            logger.info("Flushed %d trace events for deck %s", trace_count, deck.id)

        metrics["10_synthesis"] = time.time() - t

        total_time = time.time() - start_time
        logger.info(
            "Deck built for %s in %.1fs ($%.4f)",
            commander.name, total_time, total_cost,
        )

        return DeckBuildResult(
            deck=deck,
            profile_was_generated=not profile_result.cache_hit,
            total_cost_usd=round(total_cost, 4),
            total_time_seconds=round(total_time, 2),
            pipeline_metrics=metrics,
        )

    # --- Step implementations ---

    def _validate_request(self, request: DeckBuildRequest) -> Card:
        """Validate the build request and load commander."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                "SELECT * FROM cards WHERE id = ? AND is_legal_commander = 1",
                (request.commander_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise FatalError(
                    f"Commander not found or not legal: {request.commander_id}"
                )

            row_dict = dict(row)
            for field in ("color_identity", "keywords"):
                val = row_dict.get(field, "[]")
                if isinstance(val, str):
                    row_dict[field] = json.loads(val)

            # Get price
            price_cursor = conn.execute(
                "SELECT price_usd FROM card_prices "
                "WHERE card_id = ? ORDER BY snapshot_date DESC LIMIT 1",
                (request.commander_id,),
            )
            price_row = price_cursor.fetchone()
            if price_row:
                row_dict["current_price_usd"] = price_row["price_usd"]

            return Card(
                id=row_dict["id"],
                oracle_id=row_dict["oracle_id"],
                name=row_dict["name"],
                mana_cost=row_dict.get("mana_cost"),
                cmc=row_dict["cmc"],
                type_line=row_dict["type_line"],
                oracle_text=row_dict.get("oracle_text"),
                color_identity=row_dict["color_identity"],
                keywords=row_dict.get("keywords", []),
                is_legal_commander=True,
                is_legal_in_99=bool(row_dict.get("is_legal_in_99", True)),
                set_code=row_dict["set_code"],
                rarity=row_dict["rarity"],
                image_uri=row_dict.get("image_uri"),
                last_updated=row_dict.get("last_updated", datetime.now()),
                current_price_usd=row_dict.get("current_price_usd"),
            )
        finally:
            conn.close()

    def _acquire_profile(self, request, commander):
        """Get or generate commander profile."""
        from sabermetrics.reasoning.profiler import ProfileManager, ProfileRequest

        manager = ProfileManager(self.db_path)
        profile_request = ProfileRequest(
            commander_id=request.commander_id,
            user_intent=request.user_intent,
        )
        return manager.generate_profile(profile_request)

    def _filter_candidates(self, request, commander) -> list[dict]:
        """Stage 1: Apply hard-rule filters."""
        from sabermetrics.analytics.filters import apply_hard_filters

        return apply_hard_filters(
            db_path=self.db_path,
            commander_id=request.commander_id,
            max_budget_usd=request.budget_usd,
        )

    def _load_role_tags(self, candidates: list[dict]) -> list[dict]:
        """Load role_tags and functional_categories for candidates from DB."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            # Check if columns exist
            cursor = conn.execute("PRAGMA table_info(cards)")
            columns = {row[1] for row in cursor.fetchall()}
            if "role_tags" not in columns:
                return candidates

            card_ids = [c.get("id", "") for c in candidates]
            if not card_ids:
                return candidates

            # Batch load role tags
            tag_map: dict[str, tuple[str, str]] = {}
            batch_size = 500
            for i in range(0, len(card_ids), batch_size):
                batch = card_ids[i:i + batch_size]
                placeholders = ",".join("?" * len(batch))
                cursor = conn.execute(
                    f"SELECT id, role_tags, functional_categories "
                    f"FROM cards WHERE id IN ({placeholders})",
                    batch,
                )
                for row in cursor:
                    tag_map[row["id"]] = (
                        row["role_tags"] or "[]",
                        row["functional_categories"] or "[]",
                    )

            for card in candidates:
                cid = card.get("id", "")
                if cid in tag_map:
                    card["role_tags"] = tag_map[cid][0]
                    card["functional_categories"] = tag_map[cid][1]
        finally:
            conn.close()

        return candidates

    def _structural_score(
        self,
        candidates: list[dict],
        commander: Card,
        request: DeckBuildRequest,
        profile_result=None,
    ) -> list[dict]:
        """Score by CVAR composite (reused from v1)."""
        from sabermetrics.analytics.cvar import ScoringContext, compute_cvar
        from sabermetrics.analytics.oracle_keywords import (
            extract_referenced_keywords,
            extract_referenced_mechanics,
        )
        weights = request.weights or CVARWeights()

        ref_keywords = extract_referenced_keywords(commander.oracle_text)
        ref_mechanics = extract_referenced_mechanics(commander.oracle_text)

        # Extract engine keywords from profile
        engine_keywords: list[str] = []
        output_keywords: list[str] = []
        if profile_result is not None:
            sp = getattr(
                getattr(profile_result, "profile", None),
                "strategic_profile",
                None,
            )
            if sp is not None:
                for dep in getattr(sp, "engine_dependencies", []):
                    engine_keywords.extend(dep.engine_card_traits)
                    output_keywords.extend(dep.dependent_outputs)

        # Tokenize LLM-generated trait descriptions into matchable keywords
        engine_keywords = _tokenize_engine_traits(engine_keywords)

        # Extract desired card traits from value inversions
        desired_traits: list[str] = []
        if profile_result is not None:
            sp = getattr(
                getattr(profile_result, "profile", None),
                "strategic_profile",
                None,
            )
            if sp is not None:
                for vi in getattr(sp, "value_inversions", []):
                    desired_traits.extend(vi.desired_characteristics)

        # Load EDHREC top cards
        edhrec_top_cards: dict[str, float] = {}
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT top_cards FROM edhrec_commander_data "
                "WHERE commander_id = ?",
                (commander.id,),
            )
            row = cursor.fetchone()
            if row and row["top_cards"]:
                for entry in json.loads(row["top_cards"]):
                    name = (entry.get("card_name") or "").lower()
                    pct = float(entry.get("inclusion_pct", 0))
                    if name and pct > 0:
                        edhrec_top_cards[name] = pct
            conn.close()
        except Exception as e:
            logger.warning("Failed to load EDHREC data: %s", e)

        context = ScoringContext(
            commander_id=commander.id,
            commander_name=commander.name,
            commander_colors=commander.color_identity,
            commander_keywords=commander.keywords or [],
            commander_oracle_text=commander.oracle_text,
            referenced_keywords=ref_keywords,
            referenced_mechanics=ref_mechanics,
            engine_keywords=[kw.lower() for kw in engine_keywords],
            output_keywords=[kw.lower() for kw in output_keywords],
            edhrec_top_cards=edhrec_top_cards,
            desired_card_traits=desired_traits,
            weights_synergy=weights.synergy,
            weights_mana_efficiency=weights.mana_efficiency,
            weights_replacement_value=weights.replacement_value,
            weights_price_efficiency=weights.price_efficiency,
            max_budget=request.budget_usd,
        )

        for card in candidates:
            card_name_lower = (card.get("name") or "").lower()
            card["edhrec_inclusion_pct"] = edhrec_top_cards.get(
                card_name_lower, 0.0
            )
            result = compute_cvar(card, context, self.db_path)
            card["_cvar_result"] = result.model_dump()
            card["_cvar_score"] = result.composite_score

        return candidates

    def _pareto_filter(self, candidates: list[dict]) -> list[dict]:
        """Stage 2: Remove dominated cards within each role.

        A card is dominated if another card in the same role has both
        a higher CVAR score and a lower price.

        Auto-include staples (from auto_include_cards.yaml) are never
        eliminated, even if dominated — they are kept unconditionally
        so infrastructure generators can find them.
        """
        from sabermetrics.config import settings
        from sabermetrics.pipeline.generators.ramp import _load_auto_includes

        # Load auto-include names to protect from Pareto elimination
        auto_includes, _ = _load_auto_includes()
        auto_include_names: set[str] = set()
        for section_entries in auto_includes.values():
            if not isinstance(section_entries, list):
                continue
            for entry in section_entries:
                auto_include_names.add(entry["name"])

        # Group by primary role
        role_groups: dict[str, list[dict]] = {}
        for card in candidates:
            role_tags_raw = card.get("role_tags", "[]")
            if isinstance(role_tags_raw, str):
                try:
                    role_tags = json.loads(role_tags_raw)
                except (json.JSONDecodeError, TypeError):
                    role_tags = ["utility"]
            else:
                role_tags = role_tags_raw or ["utility"]

            primary_role = role_tags[0] if role_tags else "utility"
            if primary_role not in role_groups:
                role_groups[primary_role] = []
            role_groups[primary_role].append(card)

        # Pareto filter within each role
        kept: list[dict] = []
        removed = 0
        # Minimum candidates to keep per non-land role (ensures enough
        # diversity for infrastructure generators and differentiator fill)
        min_per_role = 30

        for role, group in role_groups.items():
            if role == "land":
                kept.extend(group)  # Don't Pareto-filter lands
                continue

            # Sort by CVAR descending
            group.sort(key=lambda c: c.get("_cvar_score", 0), reverse=True)

            # Keep card if no other card strictly dominates it
            # Auto-include staples are never eliminated
            frontier: list[dict] = []
            for card in group:
                card_name = card.get("name", "")
                if card_name in auto_include_names:
                    frontier.append(card)
                    self._tracer.record(
                        card_name=card_name,
                        stage="pareto",
                        action="protected",
                        card_id=card.get("id"),
                        score=card.get("_cvar_score"),
                        reason="auto-include exempt",
                    )
                    continue

                cvar = card.get("_cvar_score", 0)
                price = float(card.get("price_usd", 0) or 0)

                dominated = False
                dominator_name = ""
                edhrec_saved = False
                card_edhrec = card.get("edhrec_inclusion_pct", 0.0)

                for f_card in frontier:
                    f_cvar = f_card.get("_cvar_score", 0)
                    f_price = float(f_card.get("price_usd", 0) or 0)
                    if f_cvar >= cvar and f_price <= price and (f_cvar > cvar or f_price < price):
                        # EDHREC protection: a card with strong empirical inclusion
                        # cannot be dominated by one the community doesn't use.
                        f_edhrec = f_card.get("edhrec_inclusion_pct", 0.0)
                        if card_edhrec >= 30.0 and (card_edhrec - f_edhrec) >= 25.0:
                            edhrec_saved = True
                            continue  # This frontier card can't dominate; check others
                        dominated = True
                        dominator_name = f_card.get("name", "")
                        break

                if not dominated:
                    frontier.append(card)
                    if edhrec_saved:
                        self._tracer.record(
                            card_name=card_name,
                            stage="pareto",
                            action="protected",
                            card_id=card.get("id"),
                            score=cvar,
                            reason=f"EDHREC protected ({card_edhrec:.0f}% inclusion)",
                        )
                    else:
                        self._tracer.record(
                            card_name=card_name,
                            stage="pareto",
                            action="considered",
                            card_id=card.get("id"),
                            score=cvar,
                            reason="survived Pareto",
                        )
                else:
                    removed += 1
                    self._tracer.record(
                        card_name=card_name,
                        stage="pareto",
                        action="rejected",
                        card_id=card.get("id"),
                        score=cvar,
                        reason=f"dominated by {dominator_name} (cvar={f_cvar:.3f}, price=${f_price:.2f})",
                    )

            # Ensure minimum per-role diversity: if frontier is too small,
            # re-add top CVAR cards that were dominated
            if len(frontier) < min_per_role and len(group) > len(frontier):
                frontier_ids = {id(c) for c in frontier}
                for card in group:
                    if len(frontier) >= min_per_role:
                        break
                    if id(card) not in frontier_ids:
                        frontier.append(card)
                        frontier_ids.add(id(card))

            kept.extend(frontier)

        # Global floor: ensure enough non-land candidates total
        non_land_kept = [
            c for c in kept
            if "land" not in (c.get("type_line") or "").lower()
        ]
        min_non_land = max(
            settings.pipeline.structural_filter_target,
            settings.llm.max_candidates_for_llm_fit * 3,
        )
        if len(non_land_kept) < min_non_land:
            # Re-add top non-land cards by CVAR
            non_land_all = [
                c for c in candidates
                if "land" not in (c.get("type_line") or "").lower()
            ]
            non_land_all.sort(
                key=lambda c: c.get("_cvar_score", 0), reverse=True
            )
            kept_ids = {id(c) for c in kept}
            for card in non_land_all:
                if len(non_land_kept) >= min_non_land:
                    break
                if id(card) not in kept_ids:
                    kept.append(card)
                    non_land_kept.append(card)
                    kept_ids.add(id(card))

        logger.info(
            "Pareto filter: %d kept, %d removed (across %d roles)",
            len(kept), removed, len(role_groups),
        )
        return kept

    def _derive_template(self, profile, request):
        """Stage 3: Derive deck template from profile."""
        from sabermetrics.reasoning.template_deriver import derive_deck_template

        return derive_deck_template(
            profile=profile,
            budget=request.budget_usd,
            power_target=request.power_target,
            db_path=self.db_path,
        )

    def _fill_infrastructure(
        self, candidates, commander, request, template,
    ) -> tuple[list, float]:
        """Stage 4: Fill infrastructure slots with deterministic generators.

        Returns:
            Tuple of (list of SlotAssignment, total budget used).
        """
        from sabermetrics.pipeline.generators import (
            DrawPackageGenerator,
            LandPackageGenerator,
            ProtectionPackageGenerator,
            RampPackageGenerator,
            RemovalPackageGenerator,
        )
        from sabermetrics.pipeline.slot_assigner import SlotAssignment

        all_assignments: list[SlotAssignment] = []
        budget_used = 0.0
        colors = commander.color_identity

        # Helper to get cards with a specific role tag
        def _pool_by_role(role: str) -> list[dict]:
            pool = []
            for card in candidates:
                rt_raw = card.get("role_tags", "[]")
                if isinstance(rt_raw, str):
                    try:
                        rt = json.loads(rt_raw)
                    except (json.JSONDecodeError, TypeError):
                        rt = []
                else:
                    rt = rt_raw or []
                if role in rt:
                    pool.append(card)
            return pool

        def _land_pool() -> list[dict]:
            from sabermetrics.pipeline.greedy_optimizer import is_playable_as_land
            pool = []
            for card in candidates:
                type_line = card.get("type_line") or ""
                if is_playable_as_land(type_line) and "creature" not in type_line.lower():
                    pool.append(card)
            return pool

        def placed_cards() -> list:
            return [a.card for a in all_assignments]

        def _trace_infra(assignments: list, stage: str) -> None:
            """Emit trace events for infrastructure placements."""
            for a in assignments:
                self._tracer.record(
                    card_name=a.card.get("name", ""),
                    stage=stage,
                    action="placed",
                    card_id=a.card.get("id"),
                    score=a.score,
                    reason=f"infrastructure {stage.removeprefix('infra_')}",
                )

        # 1. Ramp (first, so land generator knows what spells are in deck)
        ramp_gen = RampPackageGenerator(self.db_path)
        ramp = ramp_gen.generate(
            color_identity=colors,
            target_count=template.ramp_count,
            budget_remaining=request.budget_usd - budget_used,
            template=template,
            already_placed=placed_cards(),
            role_tag_pool=_pool_by_role("ramp"),
            commander_colors=colors,
            avg_cmc=template.avg_cmc_target,
        )
        all_assignments.extend(ramp)
        budget_used += sum(float(a.card.get("price_usd", 0) or 0) for a in ramp)
        _trace_infra(ramp, "infra_ramp")
        # Capture protected names from ramp generator for swap_refine
        self._protected_names = ramp_gen.protected_names

        # 2. Draw
        draw_gen = DrawPackageGenerator(self.db_path)
        draw = draw_gen.generate(
            color_identity=colors,
            target_count=template.draw_count,
            budget_remaining=request.budget_usd - budget_used,
            template=template,
            already_placed=placed_cards(),
            role_tag_pool=_pool_by_role("draw"),
        )
        all_assignments.extend(draw)
        budget_used += sum(float(a.card.get("price_usd", 0) or 0) for a in draw)
        _trace_infra(draw, "infra_draw")

        # 3. Removal + board wipes
        removal_pool = _pool_by_role("removal") + _pool_by_role("board_wipe")
        # Deduplicate
        seen = set()
        deduped_removal = []
        for c in removal_pool:
            cid = c.get("id", id(c))
            if cid not in seen:
                seen.add(cid)
                deduped_removal.append(c)

        removal_gen = RemovalPackageGenerator(self.db_path)
        removal = removal_gen.generate(
            color_identity=colors,
            target_count=template.removal_count,
            budget_remaining=request.budget_usd - budget_used,
            template=template,
            already_placed=placed_cards(),
            role_tag_pool=deduped_removal,
            board_wipe_target=template.board_wipe_count,
            commander_colors=colors,
            avg_cmc=template.avg_cmc_target,
        )
        all_assignments.extend(removal)
        budget_used += sum(float(a.card.get("price_usd", 0) or 0) for a in removal)
        _trace_infra(removal, "infra_removal")
        self._protected_names |= removal_gen.protected_names

        # 4. Protection (before lands; slots come from differentiator pool)
        protection_pool = _pool_by_role("protection")
        # Default 3 protection slots; role_targets will refine in Stage 5+6
        protection_target = min(4, max(2, template.differentiator_slots // 10))
        prot_gen = ProtectionPackageGenerator(self.db_path)
        protection = prot_gen.generate(
            color_identity=colors,
            target_count=protection_target,
            budget_remaining=request.budget_usd - budget_used,
            template=template,
            already_placed=placed_cards(),
            role_tag_pool=protection_pool,
            commander_colors=colors,
            avg_cmc=template.avg_cmc_target,
        )
        all_assignments.extend(protection)
        budget_used += sum(float(a.card.get("price_usd", 0) or 0) for a in protection)
        _trace_infra(protection, "infra_protection")
        self._protected_names |= prot_gen.protected_names

        # 5. Lands (last, so it knows what spells need color support)
        land_gen = LandPackageGenerator(self.db_path)
        lands = land_gen.generate(
            color_identity=colors,
            target_count=template.land_count,
            budget_remaining=request.budget_usd - budget_used,
            template=template,
            already_placed=placed_cards(),
            role_tag_pool=_land_pool(),
        )
        all_assignments.extend(lands)
        budget_used += sum(float(a.card.get("price_usd", 0) or 0) for a in lands)

        return all_assignments, budget_used

    def _optimize_differentiators(
        self, candidates, infrastructure, profile_result, commander,
        request, template, budget_used,
    ) -> tuple[list, dict]:
        """Stage 5+6: Synergy-aware greedy optimization.

        Replaces category_coverage + _fill_differentiators with:
        1. Compute role targets (hypergeometric reliability)
        2. Build synergy matrix (rules + co-occurrence + embeddings)
        3. Greedy fill differentiator slots
        4. Swap refinement (infrastructure cards eligible)
        5. LLM safety net on weakest picks

        Returns:
            Tuple of (all_assignments including infrastructure, optimizer_metrics).
        """
        from sabermetrics.analytics.oracle_keywords import (
            extract_referenced_keywords,
            extract_referenced_mechanics,
        )
        from sabermetrics.analytics.role_targets import compute_role_targets
        from sabermetrics.analytics.synergy_matrix import build_synergy_matrix
        from sabermetrics.pipeline.greedy_optimizer import (
            ProfileSignals,
            deck_objective,
            greedy_fill,
            swap_refine,
        )

        profile = profile_result.profile

        # Build profile signals for alignment scoring
        prof_signals = ProfileSignals(
            referenced_keywords=extract_referenced_keywords(commander.oracle_text),
            referenced_mechanics=extract_referenced_mechanics(commander.oracle_text),
        )

        # 1. Compute role targets
        role_targets = compute_role_targets(profile, template)

        # 2. Build synergy matrix
        synergy = build_synergy_matrix(
            candidates, commander.id, self.db_path,
        )

        # 3. Greedy fill (subtract protection slots already placed in Stage 4)
        protection_placed = sum(
            1 for a in infrastructure if a.slot_role == "protection"
        )
        diff_slots = max(0, template.differentiator_slots - protection_placed)
        diff_assignments = greedy_fill(
            shell=infrastructure,
            candidates=candidates,
            synergy=synergy,
            role_targets=role_targets,
            budget_remaining=request.budget_usd - budget_used,
            slots_remaining=diff_slots,
            tracer=self._tracer,
            profile_signals=prof_signals,
        )
        all_assignments = list(infrastructure) + diff_assignments

        # 4. Swap refinement (infrastructure cards eligible for swap)
        protected = getattr(self, "_protected_names", None) or set()
        all_assignments, swaps = swap_refine(
            deck=all_assignments,
            candidates=candidates,
            synergy=synergy,
            role_targets=role_targets,
            budget=request.budget_usd,
            protect_lands=True,
            protected_names=protected,
            tracer=self._tracer,
            profile_signals=prof_signals,
        )

        # Option A criterion 4: no per-card LLM in the selection hot path. The
        # deterministic synergy optimizer (greedy_fill + swap_refine) is the
        # selector; the LLM is a narrator/auditor only (profile synthesis and
        # deck narrative), never a per-card scorer.
        for a in all_assignments:
            if "_fit_reasoning" not in a.card:
                a.card["_fit_reasoning"] = "Synergy-optimizer selected"

        metrics = {
            "synergy_matrix_size": len(synergy.card_id_to_index),
            "role_targets": {r: t.target_count for r, t in role_targets.items()},
            "cards_swapped": swaps,
            "llm_safety_cost": 0.0,
            "objective_score": deck_objective(
                [a.card for a in all_assignments], synergy, role_targets,
                template, profile_signals=prof_signals,
            ),
        }
        return all_assignments, metrics

    def _redistribute_budget(
        self,
        deck: list,
        budget: float,
        candidate_pool: list[dict],
        protected_names: set[str] | None = None,
    ) -> list:
        """Stage 7: Two-pass budget optimization.

        Pass 1 — Upgrade: if budget remains, replace weak cards with better ones.
        Pass 2 — Downgrade: if over budget, replace expensive cards with cheaper ones.

        Cards in protected_names are never replaced.
        """
        protected = protected_names or set()
        from sabermetrics.pipeline.slot_assigner import SlotAssignment

        total_price = sum(
            float(a.card.get("price_usd", 0) or 0) for a in deck
        )
        used_names = {a.card.get("name", "") for a in deck}

        # Build upgrade pool indexed by role
        from sabermetrics.pipeline.greedy_optimizer import is_playable_as_land
        pool_by_role: dict[str, list[dict]] = {}
        for card in candidate_pool:
            if card.get("name", "") in used_names:
                continue
            if is_playable_as_land(card.get("type_line") or ""):
                continue
            role = _heuristic_role(card)
            if role not in pool_by_role:
                pool_by_role[role] = []
            pool_by_role[role].append(card)

        for role in pool_by_role:
            pool_by_role[role].sort(
                key=lambda c: c.get("_cvar_score", 0), reverse=True
            )

        # Pass 1: Upgrade (if budget remains)
        budget_remaining = budget - total_price
        if budget_remaining > 0:
            # Sort deck by score ascending (weakest first)
            indexed = [(i, a) for i, a in enumerate(deck) if a.slot_role != "land"]
            indexed.sort(key=lambda x: x[1].score)

            for idx, assignment in indexed[:5]:  # Check 5 weakest
                if assignment.card.get("name", "") in protected:
                    self._tracer.record(
                        card_name=assignment.card.get("name", ""),
                        stage="budget_redist",
                        action="protected",
                        card_id=assignment.card.get("id"),
                        reason="staple protection — exempt from budget swap",
                        force=True,
                    )
                    continue
                role = assignment.slot_role
                if role not in pool_by_role:
                    continue

                current_score = assignment.score
                current_price = float(assignment.card.get("price_usd", 0) or 0)

                for upgrade_card in pool_by_role[role]:
                    up_name = upgrade_card.get("name", "")
                    if up_name in used_names:
                        continue
                    up_price = float(upgrade_card.get("price_usd", 0) or 0)
                    up_score = upgrade_card.get("_cvar_score", 0)

                    price_diff = up_price - current_price
                    if price_diff <= budget_remaining and up_score > current_score + 0.1:
                        # Replace
                        old_name = assignment.card.get("name", "")
                        deck[idx] = SlotAssignment(
                            card=upgrade_card,
                            slot_role=role,
                            score=round(up_score, 4),
                            alternatives=[],
                        )
                        used_names.discard(old_name)
                        used_names.add(up_name)
                        budget_remaining -= price_diff
                        total_price += price_diff
                        self._tracer.record(
                            card_name=old_name,
                            stage="budget_redist",
                            action="swapped_out",
                            card_id=assignment.card.get("id"),
                            score=current_score,
                            reason=f"upgrade: +${price_diff:.2f}, +{up_score - current_score:.2f} score",
                            force=True,
                        )
                        self._tracer.record(
                            card_name=up_name,
                            stage="budget_redist",
                            action="swapped_in",
                            card_id=upgrade_card.get("id"),
                            score=round(up_score, 4),
                            reason=f"upgrade: +${price_diff:.2f}, +{up_score - current_score:.2f} score",
                            force=True,
                        )
                        logger.info(
                            "Budget upgrade: %s → %s (+$%.2f, +%.2f score)",
                            old_name, up_name, price_diff,
                            up_score - current_score,
                        )
                        break

        # Pass 2: Downgrade (if over budget)
        if total_price > budget:
            # Sort by score descending (strongest first = last to cut)
            indexed = [(i, a) for i, a in enumerate(deck) if a.slot_role != "land"]
            indexed.sort(key=lambda x: x[1].score)

            for idx, assignment in indexed:  # Weakest first
                if total_price <= budget:
                    break

                if assignment.card.get("name", "") in protected:
                    self._tracer.record(
                        card_name=assignment.card.get("name", ""),
                        stage="budget_redist",
                        action="protected",
                        card_id=assignment.card.get("id"),
                        reason="staple protection — exempt from budget downgrade",
                        force=True,
                    )
                    continue
                role = assignment.slot_role
                if role not in pool_by_role:
                    continue

                current_price = float(assignment.card.get("price_usd", 0) or 0)
                if current_price < 1.0:
                    continue  # Not worth downgrading cheap cards

                for alt_card in reversed(pool_by_role.get(role, [])):
                    alt_name = alt_card.get("name", "")
                    if alt_name in used_names:
                        continue
                    alt_price = float(alt_card.get("price_usd", 0) or 0)

                    if alt_price < current_price:
                        savings = current_price - alt_price
                        old_name = assignment.card.get("name", "")
                        alt_score = alt_card.get("_cvar_score", 0)
                        deck[idx] = SlotAssignment(
                            card=alt_card,
                            slot_role=role,
                            score=round(alt_score, 4),
                            alternatives=[],
                        )
                        used_names.discard(old_name)
                        used_names.add(alt_name)
                        total_price -= savings
                        self._tracer.record(
                            card_name=old_name,
                            stage="budget_redist",
                            action="swapped_out",
                            card_id=assignment.card.get("id"),
                            score=assignment.score,
                            reason=f"downgrade: -${savings:.2f}",
                            force=True,
                        )
                        self._tracer.record(
                            card_name=alt_name,
                            stage="budget_redist",
                            action="swapped_in",
                            card_id=alt_card.get("id"),
                            score=round(alt_score, 4),
                            reason=f"downgrade: -${savings:.2f}",
                            force=True,
                        )
                        logger.info(
                            "Budget downgrade: %s → %s (-$%.2f)",
                            old_name, alt_name, savings,
                        )
                        break

        return deck

    def _enforce_legality(
        self,
        deck: list,
        commander: Card,
        protected_names: set[str] | None = None,
    ) -> list:
        """Stage 7b: enforce Commander legality as a hard invariant.

        Guarantees on return:
          * exactly 99 non-commander cards;
          * singleton — no duplicate card names except basic lands;
          * every card's color identity is a subset of the commander's.

        Repairs rather than warns: out-of-identity cards and the commander
        itself are dropped, duplicate nonbasics are collapsed to the highest
        scoring copy, an over-full deck is trimmed weakest-first (basics, then
        non-protected non-lands, then non-protected lands), and a short deck is
        filled with basic lands in the commander's colors.

        Args:
            deck: Current SlotAssignment list (may be ≠99, may have dupes).
            commander: The commander card (excluded from the 99).
            protected_names: Names that must not be trimmed (staples).

        Returns:
            Exactly 99 legal SlotAssignments.
        """
        protected = protected_names or set()
        commander_colors = set(commander.color_identity or [])

        def _is_basic(name: str) -> bool:
            return name in _BASIC_LAND_NAMES

        def _is_land(a) -> bool:
            return (
                a.slot_role == "land"
                or "land" in (a.card.get("type_line") or "").lower()
            )

        # Pass 1: drop commander/dupes/out-of-identity, keeping best per name.
        by_score = sorted(deck, key=lambda a: a.score, reverse=True)
        seen: set[str] = set()
        kept: list = []
        for a in by_score:
            name = a.card.get("name", "")
            if not name or name == commander.name:
                continue
            if not _is_basic(name):
                if name in seen:
                    continue  # singleton violation — drop the weaker copy
                ci = _parse_color_identity(a.card)
                if not ci <= commander_colors:
                    self._tracer.record(
                        card_name=name, stage="legality", action="rejected",
                        card_id=a.card.get("id"), reason="out of color identity",
                        force=True,
                    )
                    continue
                seen.add(name)
            kept.append(a)

        # Pass 2: trim to 99 if over (basics → weak non-land → weak land).
        if len(kept) > 99:
            def _removable_rank(a) -> tuple[int, float]:
                name = a.card.get("name", "")
                if _is_basic(name):
                    return (0, a.score)          # basics first
                if name in protected:
                    return (3, a.score)          # protected last
                return (1 if not _is_land(a) else 2, a.score)

            # Remove highest-rank / lowest-score first until exactly 99.
            kept.sort(key=_removable_rank)  # ascending: first = most removable
            excess = len(kept) - 99
            for a in kept[:excess]:
                self._tracer.record(
                    card_name=a.card.get("name", ""), stage="legality",
                    action="swapped_out", card_id=a.card.get("id"),
                    score=a.score, reason="trimmed to reach 99", force=True,
                )
            kept = kept[excess:]

        # Pass 3: fill to 99 with basic lands in the commander's colors.
        if len(kept) < 99:
            kept.extend(
                _make_basic_lands(99 - len(kept), commander.color_identity or [])
            )

        if len(kept) != 99:  # invariant must hold
            logger.error(
                "Legality repair produced %d cards (expected 99)", len(kept)
            )
        return kept

    def _synthesize_narrative(self, profile_result, assembly, request):
        """Generate deck narrative via Sonnet."""
        profile = profile_result.profile
        sp = profile.strategic_profile
        profile_summary = (
            f"Commander: {profile.commander_name}\n"
            f"Archetype: {sp.primary_archetype}\n"
            f"Game Plan: {sp.game_plan_summary}"
        )

        deck_cards_with_reasoning = []
        for assignment in assembly.assignments:
            deck_cards_with_reasoning.append({
                "name": assignment.card.get("name", "Unknown"),
                "slot_role": assignment.slot_role,
                "fit_score": round(assignment.score * 10, 1),
                "reasoning": "",
            })

        try:
            from sabermetrics.reasoning.synthesis import DeckSynthesizer

            synthesizer = DeckSynthesizer(self.db_path)
            synthesis, cost = synthesizer.synthesize(
                profile_summary=profile_summary,
                deck_cards_with_reasoning=deck_cards_with_reasoning,
                bracket=request.power_target,
                bracket_reasoning="Target power level",
            )
            narrative = DeckNarrative(
                game_plan=synthesis.game_plan,
                key_synergies=synthesis.key_synergies,
                weaknesses=synthesis.weaknesses,
                suggested_play_pattern=synthesis.suggested_play_pattern,
            )
            return narrative, cost
        except Exception as e:
            logger.warning("Narrative synthesis failed: %s", e)
            narrative = DeckNarrative(
                game_plan=profile_result.profile.strategic_profile.game_plan_summary,
                key_synergies=[
                    wc.description
                    for wc in profile_result.profile.strategic_profile.win_conditions
                ],
                weaknesses=["Unable to generate narrative — LLM unavailable."],
                suggested_play_pattern="Follow the core game plan.",
            )
            return narrative, 0.0

    def _classify_bracket(self, assembly):
        """Classify deck power bracket."""
        from sabermetrics.analytics.brackets import classify_bracket

        cards = [a.card for a in assembly.assignments]
        bracket_result = classify_bracket(
            cards=cards,
            db_path=self.db_path,
        )
        return DeckClassification(
            estimated_bracket=bracket_result.bracket,
            bracket_reasoning="; ".join(bracket_result.reasoning),
        )

    def _build_deck_model(
        self,
        commander: Card,
        request: DeckBuildRequest,
        profile,
        assembly,
        narrative: DeckNarrative,
        classification: DeckClassification,
        total_cost: float,
        start_time: float,
    ) -> GeneratedDeck:
        """Build the final GeneratedDeck model."""
        weights = request.weights or CVARWeights()

        # Build DeckCard list
        deck_cards: list[DeckCard] = []
        for assignment in assembly.assignments:
            card_data = assignment.card
            cvar_data = card_data.get("_cvar_result", {})

            ci = card_data.get("color_identity", "[]")
            if isinstance(ci, str):
                ci = json.loads(ci)
            kw = card_data.get("keywords", "[]")
            if isinstance(kw, str):
                kw = json.loads(kw)

            card_model = Card(
                id=card_data.get("id", ""),
                oracle_id=card_data.get("oracle_id", ""),
                name=card_data.get("name", ""),
                mana_cost=card_data.get("mana_cost"),
                cmc=float(card_data.get("cmc", 0)),
                type_line=card_data.get("type_line", ""),
                oracle_text=card_data.get("oracle_text"),
                color_identity=ci,
                keywords=kw,
                is_legal_commander=bool(card_data.get("is_legal_commander", False)),
                is_legal_in_99=bool(card_data.get("is_legal_in_99", True)),
                set_code=card_data.get("set_code", ""),
                rarity=card_data.get("rarity", "common"),
                image_uri=card_data.get("image_uri"),
                last_updated=card_data.get("last_updated", datetime.now()),
                current_price_usd=card_data.get("price_usd"),
            )

            sub_scores = CardSubScores(
                synergy=cvar_data.get("synergy_score", 0.0),
                mana_efficiency=cvar_data.get("mana_efficiency_score", 0.0),
                replacement_value=cvar_data.get("replacement_value_score", 0.0),
                price_efficiency=cvar_data.get("price_efficiency_score", 0.0),
                card_win_equity=cvar_data.get("card_win_equity"),
            )

            deck_cards.append(DeckCard(
                card=card_model,
                slot_role=assignment.slot_role,
                cvar_score=assignment.score,
                sub_scores=sub_scores,
                llm_fit=LLMFit(
                    score=max(1, min(10, round(assignment.score * 10))),
                    reasoning=card_data.get("_fit_reasoning", "Auto-scored"),
                ),
                alternatives=assignment.alternatives,
            ))

        # Composition stats
        from sabermetrics.analytics.components import (
            count_board_wipes,
            count_card_draw,
            count_ramp_spells,
            count_removal,
            count_tutors,
        )
        from sabermetrics.analytics.brackets import _detect_combos

        all_card_dicts = [a.card for a in assembly.assignments]

        # Mana curve
        mana_curve = [0] * 8
        color_dist: dict[str, int] = {}
        type_dist: dict[str, int] = {}
        for card in all_card_dicts:
            cmc = int(float(card.get("cmc", 0)))
            mana_curve[min(cmc, 7)] += 1

            ci = card.get("color_identity", "[]")
            if isinstance(ci, str):
                ci = json.loads(ci)
            for c in ci:
                color_dist[c] = color_dist.get(c, 0) + 1

            type_line = card.get("type_line", "")
            for t in ["Creature", "Instant", "Sorcery", "Artifact",
                       "Enchantment", "Planeswalker", "Land"]:
                if t in type_line:
                    type_dist[t] = type_dist.get(t, 0) + 1

        non_lands = [
            c for c in all_card_dicts
            if "land" not in (c.get("type_line") or "").lower()
        ]
        cmcs = [float(c.get("cmc", 0)) for c in non_lands if c.get("cmc")]
        avg_cmc = sum(cmcs) / len(cmcs) if cmcs else 0.0

        gc_names = []
        try:
            from sabermetrics.analytics.brackets import _load_game_changers
            game_changers = _load_game_changers()
            for card in all_card_dicts:
                name = (card.get("name") or "").lower()
                if name in game_changers:
                    gc_names.append(card.get("id", ""))
        except Exception:
            pass

        combos = _detect_combos(all_card_dicts, self.db_path)
        combo_ids = [c["id"] for c in combos]

        composition = DeckComposition(
            total_price_usd=assembly.total_price,
            average_cmc=round(avg_cmc, 2),
            color_distribution=color_dist,
            type_distribution=type_dist,
            mana_curve=mana_curve,
            component_counts=ComponentCounts(
                ramp=count_ramp_spells(non_lands),
                draw=count_card_draw(non_lands),
                removal=count_removal(non_lands),
                board_wipes=count_board_wipes(non_lands),
                tutors=count_tutors(non_lands),
                win_conditions=sum(
                    1 for a in assembly.assignments if a.slot_role == "wincon"
                ),
            ),
            game_changers_present=gc_names,
            detected_combos=combo_ids,
        )

        deck_id = str(uuid.uuid4())
        elapsed = time.time() - start_time

        return GeneratedDeck(
            id=deck_id,
            commander=commander,
            generated_at=datetime.now(),
            parameters=DeckParameters(
                budget_usd=request.budget_usd,
                power_target=request.power_target,
                strategy=request.strategy,
                weights=weights,
                deck_name=request.deck_name,
            ),
            cards=deck_cards,
            composition=composition,
            classification=classification,
            narrative=narrative,
            meta=GenerationMeta(
                generation_time_seconds=round(elapsed, 2),
                llm_cost_usd=round(total_cost, 4),
                source_profile_id=profile.commander_id,
            ),
        )

    def _persist_deck(self, deck: GeneratedDeck) -> None:
        """Save to generated_decks table."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cards_json = json.dumps([
                {
                    "card_id": dc.card.id,
                    "name": dc.card.name,
                    "slot_role": dc.slot_role,
                    "cvar_score": dc.cvar_score,
                    "fit_score": dc.llm_fit.score,
                    "reasoning": dc.llm_fit.reasoning,
                    "alternatives": dc.alternatives,
                }
                for dc in deck.cards
            ])

            rationale = json.dumps({
                "narrative": deck.narrative.model_dump(),
                "composition": deck.composition.model_dump(),
            })

            conn.execute(
                "INSERT OR REPLACE INTO generated_decks "
                "(id, commander_id, profile_id, budget_usd, power_target, "
                "strategy, cards_json, rationale, cvar_score, "
                "estimated_bracket, generated_at, deck_name) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    deck.id,
                    deck.commander.id,
                    deck.meta.source_profile_id,
                    deck.parameters.budget_usd,
                    deck.parameters.power_target,
                    deck.parameters.strategy,
                    cards_json,
                    rationale,
                    sum(dc.cvar_score for dc in deck.cards) / len(deck.cards)
                    if deck.cards else 0.0,
                    deck.classification.estimated_bracket,
                    deck.generated_at.isoformat(),
                    deck.parameters.deck_name,
                ),
            )
            conn.commit()
            logger.info("Persisted deck %s", deck.id)
        finally:
            conn.close()


# Basic land names that are exempt from the singleton rule.
_BASIC_LAND_NAMES: set[str] = {
    "Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes",
    "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp",
    "Snow-Covered Mountain", "Snow-Covered Forest",
}


def _parse_color_identity(card: dict) -> set[str]:
    """Parse a card's color identity into a set, tolerating JSON-string storage."""
    ci = card.get("color_identity", "[]")
    if isinstance(ci, str):
        try:
            ci = json.loads(ci)
        except (json.JSONDecodeError, TypeError):
            ci = []
    return set(ci or [])


def _make_basic_lands(count: int, commander_colors: list[str]) -> list:
    """Create `count` basic-land SlotAssignments in the commander's colors.

    Distributes evenly round-robin across the commander's colored basics;
    a colorless commander gets Wastes. Basic lands carry empty color identity,
    so they are legal in any deck.

    Args:
        count: Number of basics to create (>= 0).
        commander_colors: Commander color identity (e.g. ["W", "U"]).

    Returns:
        List of `count` SlotAssignment objects with slot_role "land".
    """
    from sabermetrics.pipeline.mana_base import COLOR_TO_BASIC
    from sabermetrics.pipeline.slot_assigner import SlotAssignment

    names = [COLOR_TO_BASIC[c] for c in commander_colors if c in COLOR_TO_BASIC]
    if not names:
        names = ["Wastes"]

    out: list = []
    for i in range(max(0, count)):
        name = names[i % len(names)]
        out.append(SlotAssignment(
            card={
                "id": f"basic-{name.lower().replace(' ', '-')}-{i}",
                "name": name,
                "type_line": f"Basic Land — {name}",
                "oracle_text": "",
                "mana_cost": "",
                "cmc": 0.0,
                "color_identity": "[]",
                "price_usd": 0.0,
                "rarity": "common",
            },
            slot_role="land",
            score=0.5,
            alternatives=[],
        ))
    return out


def _is_ramp(type_line: str, oracle_text: str) -> bool:
    """Check if a card is a mana-producing ramp spell."""
    if "add" in oracle_text and ("mana" in oracle_text or "{" in oracle_text):
        return True
    if "search your library for a" in oracle_text and "land" in oracle_text:
        return True
    if "put" in oracle_text and "land" in oracle_text and "battlefield" in oracle_text:
        return True
    return False


def _heuristic_role(card: dict) -> str:
    """Classify card role by heuristics when LLM is unavailable."""
    from sabermetrics.pipeline.slot_assigner import _classify_card_role
    return _classify_card_role(card)
