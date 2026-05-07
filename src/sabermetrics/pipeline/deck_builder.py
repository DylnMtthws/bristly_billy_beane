"""Deck builder orchestrator (D6.2).

Runs the 10-step pipeline from SKILL-005:
1. Validate request
2. Acquire profile
3. Filter candidates
4. Embedding score (cosine similarity vs profile narrative)
5. Structural score (CVAR composite)
6. LLM fit reasoning (Haiku for top N)
7. Assemble 99 cards (slot assigner)
8. Synthesize narrative (Sonnet)
9. Classify bracket
10. Persist and return
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
        """Execute the full 10-step deck building pipeline.

        Args:
            request: Deck build parameters.

        Returns:
            DeckBuildResult with generated deck and metadata.
        """
        start_time = time.time()
        metrics: dict[str, float] = {}
        total_cost = 0.0

        # --- Step 1: Validate Request ---
        t = time.time()
        commander = self._validate_request(request)
        metrics["1_validate"] = time.time() - t

        # --- Step 2: Acquire Profile ---
        t = time.time()
        profile_result = self._acquire_profile(request, commander)
        profile = profile_result.profile
        total_cost += profile_result.generation_cost_usd
        metrics["2_profile"] = time.time() - t

        # --- Step 3: Filter Candidates ---
        t = time.time()
        candidates = self._filter_candidates(request, commander)
        metrics["3_filter"] = time.time() - t
        logger.info("Step 3: %d candidates after hard filters", len(candidates))

        # --- Step 4: Embedding Score ---
        t = time.time()
        candidates = self._embedding_score(candidates, profile)
        metrics["4_embedding"] = time.time() - t
        logger.info("Step 4: %d candidates after embedding filter", len(candidates))

        # --- Step 5: Structural Score ---
        t = time.time()
        candidates = self._structural_score(candidates, commander, request)
        metrics["5_structural"] = time.time() - t
        logger.info("Step 5: %d candidates after structural scoring", len(candidates))

        # --- Step 6: LLM Fit Reasoning ---
        t = time.time()
        scored_candidates, fit_cost = self._llm_fit_score(
            candidates, profile, commander
        )
        total_cost += fit_cost
        metrics["6_llm_fit"] = time.time() - t
        logger.info("Step 6: %d cards scored by LLM", len(scored_candidates))

        # --- Step 7: Assemble 99 Cards ---
        t = time.time()
        assembly = self._assemble_deck(scored_candidates, request, commander)
        metrics["7_assemble"] = time.time() - t
        logger.info(
            "Step 7: %d cards assembled, $%.2f total",
            len(assembly.assignments), assembly.total_price,
        )

        # --- Step 8: Synthesize Narrative ---
        t = time.time()
        narrative, synth_cost = self._synthesize_narrative(
            profile, assembly, request
        )
        total_cost += synth_cost
        metrics["8_narrative"] = time.time() - t

        # --- Step 9: Classify Bracket ---
        t = time.time()
        classification = self._classify_bracket(assembly)
        metrics["9_classify"] = time.time() - t

        # --- Step 10: Persist and Return ---
        t = time.time()
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
        metrics["10_persist"] = time.time() - t

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
        """Step 1: Validate the build request and load commander."""
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
        """Step 2: Get or generate commander profile."""
        from sabermetrics.reasoning.profiler import ProfileManager, ProfileRequest

        manager = ProfileManager(self.db_path)
        profile_request = ProfileRequest(
            commander_id=request.commander_id,
            user_intent=request.user_intent,
        )
        return manager.generate_profile(profile_request)

    def _filter_candidates(self, request, commander) -> list[dict]:
        """Step 3: Apply hard-rule filters."""
        from sabermetrics.analytics.filters import apply_hard_filters

        return apply_hard_filters(
            db_path=self.db_path,
            commander_id=request.commander_id,
            max_budget_usd=request.budget_usd,
        )

    def _embedding_score(
        self, candidates: list[dict], profile_result
    ) -> list[dict]:
        """Step 4: Score by embedding similarity to profile narrative.

        Keeps top N candidates by cosine similarity.
        """
        from sabermetrics.config import settings

        target = settings.pipeline.embedding_filter_target

        try:
            from sabermetrics.analytics.embeddings import get_embedding_service

            service = get_embedding_service()

            # Build profile text for embedding
            profile_text = (
                f"{profile_result.strategic_profile.primary_archetype} "
                f"{profile_result.strategic_profile.game_plan_summary} "
                + " ".join(
                    wc.description for wc in profile_result.strategic_profile.win_conditions
                )
            )
            profile_embedding = service.embed(profile_text)

            # Score each candidate
            scored: list[tuple[dict, float]] = []
            batch_size = 100
            for i in range(0, len(candidates), batch_size):
                batch = candidates[i:i + batch_size]
                texts = [
                    f"{c.get('name', '')} {c.get('type_line', '')} "
                    f"{c.get('oracle_text', '')}"
                    for c in batch
                ]
                embeddings = service.embed_batch(texts)
                for card, emb in zip(batch, embeddings):
                    sim = float(
                        np.dot(profile_embedding, emb)
                        / (np.linalg.norm(profile_embedding) * np.linalg.norm(emb) + 1e-8)
                    )
                    card["_embedding_score"] = sim
                    scored.append((card, sim))

            scored.sort(key=lambda x: x[1], reverse=True)
            return [card for card, _ in scored[:target]]

        except Exception as e:
            logger.warning(
                "Embedding scoring failed, using all candidates: %s", e
            )
            return candidates[:target]

    def _structural_score(
        self,
        candidates: list[dict],
        commander: Card,
        request: DeckBuildRequest,
    ) -> list[dict]:
        """Step 5: Score by CVAR composite and sort."""
        from sabermetrics.analytics.cvar import ScoringContext, compute_cvar
        from sabermetrics.config import settings

        target = settings.pipeline.structural_filter_target
        weights = request.weights or CVARWeights()

        context = ScoringContext(
            commander_id=commander.id,
            commander_name=commander.name,
            commander_colors=commander.color_identity,
            commander_keywords=commander.keywords or [],
            commander_oracle_text=commander.oracle_text,
            weights_synergy=weights.synergy,
            weights_mana_efficiency=weights.mana_efficiency,
            weights_replacement_value=weights.replacement_value,
            weights_price_efficiency=weights.price_efficiency,
            max_budget=request.budget_usd,
        )

        scored: list[tuple[dict, float]] = []
        for card in candidates:
            result = compute_cvar(card, context, self.db_path)
            card["_cvar_result"] = result.model_dump()
            card["_cvar_score"] = result.composite_score
            scored.append((card, result.composite_score))

        scored.sort(key=lambda x: x[1], reverse=True)

        # Keep more than target to have overflow for slot filling
        # Include all lands in the pool regardless of score
        keep = max(target, settings.llm.max_candidates_for_llm_fit)
        top_cards = [card for card, _ in scored[:keep]]

        # Also ensure we have enough lands
        land_cards = [
            card for card, _ in scored
            if "land" in (card.get("type_line") or "").lower()
        ]
        for lc in land_cards:
            if lc not in top_cards:
                top_cards.append(lc)

        return top_cards

    def _llm_fit_score(
        self,
        candidates: list[dict],
        profile_result,
        commander: Card,
    ) -> tuple[list[tuple[dict, dict]], float]:
        """Step 6: LLM fit scoring for top candidates.

        Returns:
            Tuple of (list of (card, scoring_dict), total_cost).
        """
        from sabermetrics.config import settings

        # Build profile summary for LLM
        profile_summary = (
            f"Commander: {profile_result.commander_name}\n"
            f"Archetype: {profile_result.strategic_profile.primary_archetype}\n"
            f"Game Plan: {profile_result.strategic_profile.game_plan_summary}\n"
            f"Win Conditions: "
            + ", ".join(wc.description for wc in profile_result.strategic_profile.win_conditions)
        )

        # Separate lands (don't LLM-score lands) from non-lands
        non_land_candidates = [
            c for c in candidates
            if "land" not in (c.get("type_line") or "").lower()
        ]
        land_candidates = [
            c for c in candidates
            if "land" in (c.get("type_line") or "").lower()
        ]

        # LLM-score top N non-land candidates
        max_llm = settings.llm.max_candidates_for_llm_fit
        to_score = non_land_candidates[:max_llm]

        total_cost = 0.0
        scored: list[tuple[dict, dict]] = []

        try:
            from sabermetrics.reasoning.fit import FitScorer

            scorer = FitScorer(self.db_path)
            results = scorer.score_cards(
                cards=to_score,
                profile_summary=profile_summary,
                archetype_definition=profile_result.strategic_profile.primary_archetype,
            )

            for card, fit_response in results:
                scoring = {
                    "cvar_score": card.get("_cvar_score", 0.0),
                    "llm_fit_score": fit_response.fit_score,
                    "slot_role": fit_response.slot_role,
                    "reasoning": fit_response.reasoning,
                    "cvar_result": card.get("_cvar_result", {}),
                }
                scored.append((card, scoring))

        except Exception as e:
            logger.warning("LLM fit scoring failed, using CVAR only: %s", e)
            for card in to_score:
                scoring = {
                    "cvar_score": card.get("_cvar_score", 0.0),
                    "llm_fit_score": 5,
                    "slot_role": _heuristic_role(card),
                    "reasoning": "LLM scoring unavailable; CVAR-only ranking.",
                    "cvar_result": card.get("_cvar_result", {}),
                }
                scored.append((card, scoring))

        # Add remaining non-land candidates not LLM-scored
        for card in non_land_candidates[max_llm:]:
            scoring = {
                "cvar_score": card.get("_cvar_score", 0.0),
                "llm_fit_score": 5,
                "slot_role": _heuristic_role(card),
                "reasoning": "Not LLM-scored; below candidate threshold.",
                "cvar_result": card.get("_cvar_result", {}),
            }
            scored.append((card, scoring))

        # Add lands with high default scores (lands are auto-included)
        for card in land_candidates:
            scoring = {
                "cvar_score": card.get("_cvar_score", 0.0),
                "llm_fit_score": 7,
                "slot_role": "land",
                "reasoning": "Land — included for mana base.",
                "cvar_result": card.get("_cvar_result", {}),
            }
            scored.append((card, scoring))

        return scored, total_cost

    def _assemble_deck(self, scored_candidates, request, commander):
        """Step 7: Slot-aware assembly of 99 cards."""
        from sabermetrics.config import settings
        from sabermetrics.pipeline.slot_assigner import (
            fill_slots,
            get_target_composition,
        )

        target_comp = get_target_composition(
            power_target=request.power_target,
            strategy=request.strategy,
        )

        return fill_slots(
            scored_candidates=scored_candidates,
            target_composition=target_comp,
            max_budget=request.budget_usd,
            commander_colors=commander.color_identity,
            alternatives_per_slot=settings.output.alternatives_per_slot,
        )

    def _synthesize_narrative(self, profile_result, assembly, request):
        """Step 8: Generate deck narrative via Sonnet."""
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
            # Use a placeholder bracket for narrative (real classification in step 9)
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
        """Step 9: Classify deck power bracket."""
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

            # Build Card model from dict
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
        mana_curve = [0] * 8  # 0-6+, index 7 = 7+
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

        # Detect game changers and combos
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
        """Step 10: Save to generated_decks table."""
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
                "estimated_bracket, generated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                ),
            )
            conn.commit()
            logger.info("Persisted deck %s", deck.id)
        finally:
            conn.close()


def _heuristic_role(card: dict) -> str:
    """Classify card role by heuristics when LLM is unavailable."""
    from sabermetrics.pipeline.slot_assigner import _classify_card_role
    return _classify_card_role(card)
