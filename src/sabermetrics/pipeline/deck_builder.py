"""Deck builder orchestrator (D6.2, restructured for 6.5.5).

8-stage pipeline:
1. Hard filters + role tag loading
2. Pareto filter (remove dominated cards per role)
3. Template derivation (profile-driven composition)
4. Infrastructure fill (4 deterministic generators)
5. Category coverage analysis
6. Differentiator fill (deck-context-aware LLM scoring)
7. Budget redistribution (upgrade/downgrade passes)
8. Synthesis + classify + persist
"""

import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from sabermetrics.errors import FatalError
from sabermetrics.models.card import Card
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


class DeckBuildResult(BaseModel):
    """Result of deck generation."""

    deck: GeneratedDeck
    profile_was_generated: bool
    total_cost_usd: float
    total_time_seconds: float
    pipeline_metrics: dict


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

        # --- Stage 5: Category Coverage ---
        t = time.time()
        from sabermetrics.pipeline.category_coverage import analyze_category_coverage

        slot_intents = analyze_category_coverage(
            profile=profile,
            partial_deck=[a.card for a in infrastructure],
            remaining_slots=template.differentiator_slots,
            remaining_budget=request.budget_usd - budget_used,
        )
        metrics["7_coverage"] = time.time() - t

        # --- Stage 6: Differentiator Fill (LLM scoring) ---
        t = time.time()
        all_assignments, fit_cost = self._fill_differentiators(
            candidates, infrastructure, profile_result, commander,
            request, template, slot_intents, budget_used,
        )
        total_cost += fit_cost
        metrics["8_differentiator"] = time.time() - t
        logger.info(
            "Stage 6: %d total cards, $%.4f LLM cost",
            len(all_assignments), fit_cost,
        )

        # --- Stage 7: Budget Redistribution ---
        t = time.time()
        all_assignments = self._redistribute_budget(
            all_assignments, request.budget_usd, candidates,
        )
        metrics["9_budget"] = time.time() - t

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
            card_matches_referenced_keywords,
            extract_referenced_keywords,
            extract_referenced_mechanics,
        )
        from sabermetrics.config import settings

        target = settings.pipeline.structural_filter_target
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
        """
        from sabermetrics.config import settings

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

        for role, group in role_groups.items():
            if role == "land":
                kept.extend(group)  # Don't Pareto-filter lands
                continue

            # Sort by CVAR descending
            group.sort(key=lambda c: c.get("_cvar_score", 0), reverse=True)

            # Keep card if no other card strictly dominates it
            frontier: list[dict] = []
            for card in group:
                cvar = card.get("_cvar_score", 0)
                price = float(card.get("price_usd", 0) or 0)

                dominated = False
                for f_card in frontier:
                    f_cvar = f_card.get("_cvar_score", 0)
                    f_price = float(f_card.get("price_usd", 0) or 0)
                    if f_cvar >= cvar and f_price <= price and (f_cvar > cvar or f_price < price):
                        dominated = True
                        break

                if not dominated:
                    frontier.append(card)
                else:
                    removed += 1

            kept.extend(frontier)

        # Ensure we keep enough candidates
        min_keep = max(
            settings.pipeline.structural_filter_target,
            settings.llm.max_candidates_for_llm_fit,
        )
        if len(kept) < min_keep and removed > 0:
            # Re-add removed cards sorted by CVAR
            all_sorted = sorted(
                candidates, key=lambda c: c.get("_cvar_score", 0), reverse=True
            )
            kept_ids = {id(c) for c in kept}
            for card in all_sorted:
                if len(kept) >= min_keep:
                    break
                if id(card) not in kept_ids:
                    kept.append(card)
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
            pool = []
            for card in candidates:
                type_line = (card.get("type_line") or "").lower()
                if "land" in type_line and "creature" not in type_line:
                    pool.append(card)
            return pool

        placed_cards = lambda: [a.card for a in all_assignments]

        # 1. Ramp (first, so land generator knows what spells are in deck)
        ramp_gen = RampPackageGenerator(self.db_path)
        ramp = ramp_gen.generate(
            color_identity=colors,
            target_count=template.ramp_count,
            budget_remaining=request.budget_usd - budget_used,
            template=template,
            already_placed=placed_cards(),
            role_tag_pool=_pool_by_role("ramp"),
        )
        all_assignments.extend(ramp)
        budget_used += sum(float(a.card.get("price_usd", 0) or 0) for a in ramp)

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
        )
        all_assignments.extend(removal)
        budget_used += sum(float(a.card.get("price_usd", 0) or 0) for a in removal)

        # 4. Lands (last, so it knows what spells need color support)
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

    def _fill_differentiators(
        self, candidates, infrastructure, profile_result, commander,
        request, template, slot_intents, budget_used,
    ) -> tuple[list, float]:
        """Stage 6: LLM-score differentiator candidates with deck context.

        Returns:
            Tuple of (all assignments including infrastructure, LLM cost).
        """
        from sabermetrics.config import settings
        from sabermetrics.pipeline.slot_assigner import SlotAssignment

        # Build profile summary (same as v1)
        profile_summary = self._build_profile_summary(profile_result)

        # Identify non-land, non-infrastructure candidates
        infra_names = {a.card.get("name", "") for a in infrastructure}
        diff_candidates = [
            c for c in candidates
            if c.get("name", "") not in infra_names
            and "land" not in (c.get("type_line") or "").lower()
        ]

        # Sort by CVAR and take top N for LLM scoring
        diff_candidates.sort(
            key=lambda c: c.get("_cvar_score", 0), reverse=True,
        )
        max_llm = settings.llm.max_candidates_for_llm_fit
        to_score = diff_candidates[:max_llm]

        total_cost = 0.0
        scored: list[tuple[dict, dict]] = []

        try:
            from sabermetrics.reasoning.fit import FitScorer

            scorer = FitScorer(self.db_path)
            results = scorer.score_cards(
                cards=to_score,
                profile_summary=profile_summary,
                archetype_definition=profile_result.strategic_profile.primary_archetype,
                partial_deck=[a.card for a in infrastructure],
                slot_intents=slot_intents,
            )

            for card, fit_response in results:
                card["_fit_reasoning"] = fit_response.reasoning
                scoring = {
                    "cvar_score": card.get("_cvar_score", 0.0),
                    "llm_fit_score": fit_response.fit_score,
                    "slot_role": fit_response.slot_role,
                    "reasoning": fit_response.reasoning,
                }
                scored.append((card, scoring))

        except Exception as e:
            logger.warning("LLM fit scoring failed, using CVAR only: %s", e)
            for card in to_score:
                card["_fit_reasoning"] = "LLM scoring unavailable; CVAR-only ranking."
                scoring = {
                    "cvar_score": card.get("_cvar_score", 0.0),
                    "llm_fit_score": 5,
                    "slot_role": _heuristic_role(card),
                    "reasoning": "LLM scoring unavailable; CVAR-only ranking.",
                }
                scored.append((card, scoring))

        # Add remaining non-scored candidates
        for card in diff_candidates[max_llm:]:
            card["_fit_reasoning"] = "Not LLM-scored; below candidate threshold."
            scoring = {
                "cvar_score": card.get("_cvar_score", 0.0),
                "llm_fit_score": 5,
                "slot_role": _heuristic_role(card),
                "reasoning": "Not LLM-scored; below candidate threshold.",
            }
            scored.append((card, scoring))

        # Fill differentiator slots from scored candidates
        diff_assignments: list[SlotAssignment] = []
        used_names = infra_names.copy()
        running_price = budget_used
        target_diff = template.differentiator_slots

        # Sort by combined score
        scored.sort(
            key=lambda x: 0.6 * x[1].get("cvar_score", 0)
            + 0.4 * (x[1].get("llm_fit_score", 5) / 10.0),
            reverse=True,
        )

        for card, scoring in scored:
            if len(diff_assignments) >= target_diff:
                break

            name = card.get("name", "")
            if name in used_names:
                continue

            price = float(card.get("price_usd", 0) or 0)
            if request.budget_usd and running_price + price > request.budget_usd:
                continue

            combined = (
                0.6 * scoring.get("cvar_score", 0)
                + 0.4 * (scoring.get("llm_fit_score", 5) / 10.0)
            )
            role = scoring.get("slot_role", "utility")
            if role == "land":
                role = "utility"

            diff_assignments.append(SlotAssignment(
                card=card,
                slot_role=role,
                score=round(combined, 4),
                alternatives=[],
            ))
            used_names.add(name)
            running_price += price

        # Combine infrastructure + differentiators
        all_assignments = list(infrastructure) + diff_assignments
        return all_assignments, total_cost

    def _redistribute_budget(
        self,
        deck: list,
        budget: float,
        candidate_pool: list[dict],
    ) -> list:
        """Stage 7: Two-pass budget optimization.

        Pass 1 — Upgrade: if budget remains, replace weak cards with better ones.
        Pass 2 — Downgrade: if over budget, replace expensive cards with cheaper ones.
        """
        from sabermetrics.pipeline.slot_assigner import SlotAssignment

        total_price = sum(
            float(a.card.get("price_usd", 0) or 0) for a in deck
        )
        used_names = {a.card.get("name", "") for a in deck}

        # Build upgrade pool indexed by role
        pool_by_role: dict[str, list[dict]] = {}
        for card in candidate_pool:
            if card.get("name", "") in used_names:
                continue
            type_line = (card.get("type_line") or "").lower()
            if "land" in type_line:
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
                        logger.info(
                            "Budget downgrade: %s → %s (-$%.2f)",
                            old_name, alt_name, savings,
                        )
                        break

        return deck

    def _build_profile_summary(self, profile_result) -> str:
        """Build the profile summary string for LLM fit scoring."""
        profile_summary = (
            f"Commander: {profile_result.commander_name}\n"
            f"Archetype: {profile_result.strategic_profile.primary_archetype}\n"
            f"Game Plan: {profile_result.strategic_profile.game_plan_summary}\n"
            f"Win Conditions: "
            + ", ".join(
                wc.description for wc in profile_result.strategic_profile.win_conditions
            )
        )

        # Add value inversions
        if profile_result.strategic_profile.value_inversions:
            inversions = profile_result.strategic_profile.value_inversions
            inversion_text = (
                "\n\nVALUE INVERSIONS "
                "(cards with these traits are stronger than they appear):\n"
            )
            for vi in inversions:
                inversion_text += (
                    f"- {vi.normal_heuristic} → {vi.inverted_value}\n"
                    f"  Look for: {', '.join(vi.desired_characteristics)}\n"
                    f"  Evaluation: {vi.evaluation_guidance}\n"
                )
            profile_summary += inversion_text

        # Add engine dependencies
        if hasattr(profile_result.strategic_profile, "engine_dependencies"):
            deps = profile_result.strategic_profile.engine_dependencies
            if deps:
                dep_text = (
                    "\n\nENGINE DEPENDENCIES "
                    "(cards must feed the engine, not just match outputs):\n"
                )
                for dep in deps:
                    dep_text += (
                        f"- Engine: {dep.engine}\n"
                        f"  Engine card traits: "
                        f"{', '.join(dep.engine_card_traits)}\n"
                        f"  Dependent outputs: "
                        f"{', '.join(dep.dependent_outputs)}\n"
                        f"  FALSE SYNERGY WARNING: "
                        f"{dep.false_synergy_warning}\n"
                    )
                profile_summary += dep_text

        # Add mispriced card examples
        if hasattr(profile_result.strategic_profile, "mispriced_card_examples"):
            examples = profile_result.strategic_profile.mispriced_card_examples
            if examples:
                example_text = (
                    "\n\nMISPRICED CARDS "
                    "(these cards are better than they appear for this commander):\n"
                )
                for ex in examples:
                    example_text += f"- {ex.card_name}: {ex.why_undervalued}\n"
                example_text += (
                    "\nCards similar to these mispriced examples should score 7-9. "
                    "Use these as calibration anchors for the full scoring range.\n"
                )
                profile_summary += example_text

        return profile_summary

    def _synthesize_narrative(self, profile_result, assembly, request):
        """Generate deck narrative via Sonnet."""
        profile_summary = (
            f"Commander: {profile_result.commander_name}\n"
            f"Archetype: {profile_result.strategic_profile.primary_archetype}\n"
            f"Game Plan: {profile_result.strategic_profile.game_plan_summary}"
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
                game_plan=profile_result.strategic_profile.game_plan_summary,
                key_synergies=[
                    wc.description
                    for wc in profile_result.strategic_profile.win_conditions
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
