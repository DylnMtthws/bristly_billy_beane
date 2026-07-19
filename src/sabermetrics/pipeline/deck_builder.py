"""Deck builder orchestrator (D6.2, restructured for synergy optimizer).

8-stage pipeline:
1. Hard filters + role tag loading
2. Pareto filter (remove dominated cards per role)
3. Template derivation (profile-driven composition)
4. Infrastructure fill (4 deterministic generators)
5. Role targets + synergy matrix computation
6. Greedy optimizer + swap refinement
7. Budget rebalancing (spend-down, sell-one-buy-many, downgrade)
7b. Enforce Commander legality (exactly 99, singleton, in color identity)
8. LLM safety vet (one batched call, final gate)
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

# Per-variant empirical Pareto protection: a card in >= MIN_INCLUSION of the
# target variant's real decks is shielded from price-domination outright.
#
# Protection deliberately does NOT depend on how the dominator compares. An
# earlier version also required the dominator to be some margin rarer, on the
# theory that domination picks between substitutes and the corpus should break
# the tie. That is the wrong model: real decks run Pitiless Plunderer (65%) AND
# Deadly Dispute (55%) together. Cards that co-occur in most real decks are
# complements, so the margin between them is always small -- which meant the
# protection could never fire for the staples it existed to protect. Measured
# on Korvold, every one of the 6 eliminated staples was killed by another card
# from the same corpus, gaps ranging +0.02 to +0.18.
#
# The card's own inclusion rate is the whole signal: if it is in 65% of the
# variant's real decks, the corpus has already said it earns a slot. Only 68 of
# 1154 scored cards clear 0.30, so the exempt set stays small.
_EMPIRICAL_PROTECT_MIN_INCLUSION = 0.30


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
        # Observable degradation: which signals were live for this build.
        self._signals: dict[str, bool] = {}

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

        # --- Stage 4.5: Reserve empirical staples the generators didn't place ---
        # After Stage 4 so it can exclude cards already placed -- reserving only
        # the consensus engine pieces the role scorers reject, not good role
        # cards the generators took anyway. Reserved staples occupy differentiator
        # slots, so greedy (Stage 5+6) fills that many fewer (deck stays 99).
        placed_names = {a.card.get("name", "") for a in infrastructure}
        reserved = self._reserve_empirical_staples(
            candidates, request, template, exclude_names=placed_names,
        )
        infrastructure = list(reserved) + infrastructure
        budget_used += sum(
            float(a.card.get("price_usd", 0) or 0) for a in reserved
        )
        # Don't let swap_refine trade away a card the corpus told us to keep.
        self._protected_names |= {a.card.get("name", "") for a in reserved}
        logger.info(
            "Stage 4: %d cards placed ($%.2f), %d reserved as staples",
            len(infrastructure), budget_used, len(reserved),
        )

        # --- Stage 5+6: Synergy optimizer (role targets + matrix + greedy + swap) ---
        t = time.time()
        all_assignments, opt_metrics = self._optimize_differentiators(
            candidates, infrastructure, profile_result, commander,
            request, template, budget_used, reserved_count=len(reserved),
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

        # (Stage 7 budget rebalancing now runs inside _optimize_differentiators,
        # where the synergy matrix and role targets it evaluates against live.)

        # --- Stage 7b: Enforce Commander legality as a hard invariant ---
        all_assignments = self._enforce_legality(
            all_assignments, commander, protected_names=self._protected_names,
        )

        # --- Stage 8: Synthesis + Classify + Persist ---
        t = time.time()
        self._validate_no_commander_in_99(all_assignments, commander)
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
            pipeline_metrics={
                **metrics,
                "empirical_variant": getattr(self, "_empirical_variant", None),
            },
        )

    # --- Step implementations ---

    @staticmethod
    def _validate_no_commander_in_99(assignments: list, commander: Card) -> None:
        """Hard-fail if the commander leaked into the 99.

        The commander sharing a slot with itself violates the format's core
        rule, so this is a FatalError, not a warning. Matches by oracle_id
        (shared across printings -- excluding only the commander's own printing
        id once let a cheaper printing of the commander into its own deck) with
        name as the fallback for cards without one.

        Args:
            assignments: All slot assignments about to be assembled.
            commander: The commander card.

        Raises:
            FatalError: If any assignment is a printing of the commander.
        """
        for a in assignments:
            card = a.card
            same = (
                commander.oracle_id
                and card.get("oracle_id") == commander.oracle_id
            ) or card.get("name", "") == commander.name
            if same:
                raise FatalError(
                    f"Commander '{commander.name}' leaked into the 99 "
                    f"(printing {card.get('id')}, slot {a.slot_role}). "
                    "This is a generator bug; the deck is illegal."
                )

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

        # EDHREC behavioral corroboration is a live signal only if this
        # commander actually has inclusion data.
        if hasattr(self, "_signals"):
            self._signals["edhrec"] = bool(edhrec_top_cards)

        # Card Win Equity from tournament data (present only if TopDeck.gg
        # tournament data has been ingested for this commander).
        from sabermetrics.analytics.card_win_equity import load_cwe_for_commander
        cwe_by_card, cwe_sample_by_card = load_cwe_for_commander(
            self.db_path, commander.id
        )
        if hasattr(self, "_signals"):
            self._signals["tournament_cwe"] = bool(cwe_by_card)

        # Per-variant empirical grounding from the verified decklist corpus
        # (Phase 6). Sharper than pooled EDHREC; None when no corpus exists,
        # in which case scoring falls back cleanly to the pooled EDHREC signal.
        empirical = None
        try:
            from sabermetrics.analytics.empirical_valuation import (
                get_target_cluster_inclusion,
            )
            empirical = get_target_cluster_inclusion(
                self.db_path, commander.id, strategy=request.strategy,
            )
        except Exception as e:
            logger.warning("Empirical inclusion load failed: %s", e)
        self._empirical_variant = empirical.variant if empirical else None
        # Kept for later stages: template derivation reads the variant's
        # median composition (Stage 3), reservation its inclusion (Stage 4.5).
        self._empirical = empirical
        if empirical is not None:
            logger.info(
                "Empirical grounding active: variant='%s' from %d/%d decks, "
                "%d cards (%d reliable)",
                empirical.variant, empirical.variant_size, empirical.n_decks,
                len(empirical.inclusion), len(empirical.reliable),
            )

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
            empirical_inclusion=empirical.inclusion if empirical else {},
            empirical_reliable=empirical.reliable if empirical else set(),
            empirical_variant=empirical.variant if empirical else None,
            cwe_by_card=cwe_by_card,
            cwe_sample_by_card=cwe_sample_by_card,
            desired_card_traits=desired_traits,
            weights_synergy=weights.synergy,
            weights_mana_efficiency=weights.mana_efficiency,
            weights_replacement_value=weights.replacement_value,
            weights_price_efficiency=weights.price_efficiency,
            max_budget=request.budget_usd,
        )

        # Engine types for the anti-synergy veto: a card that mass-removes the
        # type the deck is built on ("destroy all enchantments" in an
        # enchantress shell) must never win a slot on text-match points.
        from sabermetrics.analytics.anti_synergy import engine_types, is_anti_engine
        from sabermetrics.analytics.oracle_patterns import is_combat_gated
        from sabermetrics.config import settings

        engine: set[str] = set()
        aura_engine = False
        few_attackers = False
        if empirical is not None and empirical.composition is not None:
            comp = empirical.composition
            engine = engine_types({
                "enchantment": comp.enchantments,
                "artifact": comp.artifacts,
            })
            aura_engine = (
                comp.enchantments > 0
                and comp.auras >= 0.6 * comp.enchantments
            )
            few_attackers = (
                comp.creatures < settings.scoring.combat_gated_creature_min
            )

        # Game-changer gate: bracket data exists (game_changers.yaml) but was
        # never consulted at selection -- Mana Vault-class fast mana has no
        # place in a power<=3 pool. Reuses the categorical-exclusion flag.
        gc_names: set[str] = set()
        if request.power_target <= 3:
            try:
                import yaml as _yaml
                _gc = _yaml.safe_load(
                    (Path(__file__).resolve().parents[3] / "config"
                     / "game_changers.yaml").read_text()
                ) or {}
                for v in (_gc.values() if isinstance(_gc, dict) else [_gc]):
                    if isinstance(v, list):
                        gc_names |= {str(x).lower() if not isinstance(x, dict)
                                     else str(x.get("name", "")).lower() for x in v}
            except Exception:
                pass
            gc_names.discard("sol ring")  # ubiquitous at every power level

        for card in candidates:
            card_name_lower = (card.get("name") or "").lower()
            if card_name_lower in gc_names:
                card["_anti_engine"] = True
            card["edhrec_inclusion_pct"] = edhrec_top_cards.get(
                card_name_lower, 0.0
            )
            if empirical is not None:
                card["_empirical_inclusion"] = empirical.rate(card_name_lower)
                card["_empirical_reliable"] = card_name_lower in empirical.reliable
            result = compute_cvar(card, context, self.db_path)
            card["_cvar_result"] = result.model_dump()
            card["_cvar_score"] = result.composite_score
            # SME value-inversion rule: in an aura-engine deck, any 1-2
            # mana Aura is "one mana to stop an attacker" -- playable
            # regardless of generic quality. Generic scoring rates Crippling
            # Blight-class cards near zero and Pareto kills them before any
            # later stage can save the engine fuel. Floor, don't boost:
            # multi-taskers still rank higher via synergy/empirical signals.
            if aura_engine:
                tl = (card.get("type_line") or "").lower()
                if "aura" in tl and float(card.get("cmc", 0) or 0) <= 2:
                    card["_cvar_score"] = max(card["_cvar_score"], 0.55)
                    card["_engine_fuel"] = True
            if engine and is_anti_engine(card, engine):
                card["_anti_engine"] = True
                card["_cvar_score"] = round(
                    card["_cvar_score"]
                    * settings.scoring.anti_synergy_penalty, 4,
                )
            # Combat-gated payoff discount: "attack with two or more
            # creatures" class conditions (prepared/battalion/raid) rarely
            # fire in a deck whose real lists run few attackers -- the
            # printed payoff is not the played payoff. Numeric-layer fix
            # for the Eiganjo class: three rounds of vet prompt tuning
            # scored it 7 -> 5 -> 5, never below the swap line, because
            # its inflated CVAR kept re-selecting it as top replacement.
            if few_attackers and is_combat_gated(card.get("oracle_text")):
                card["_combat_gated"] = True
                card["_cvar_score"] = round(
                    card["_cvar_score"]
                    * settings.scoring.combat_gated_discount, 4,
                )

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
                emp_saved = False
                card_edhrec = card.get("edhrec_inclusion_pct", 0.0)
                card_emp = card.get("_empirical_inclusion", 0.0)
                card_emp_reliable = card.get("_empirical_reliable", False)

                for f_card in frontier:
                    f_cvar = f_card.get("_cvar_score", 0)
                    f_price = float(f_card.get("price_usd", 0) or 0)
                    if f_cvar >= cvar and f_price <= price and (f_cvar > cvar or f_price < price):
                        # Empirical protection: a card common in the target
                        # variant's real decks earns its slot outright, whatever
                        # dominates it (per-variant, sharper than EDHREC).
                        if (
                            card_emp_reliable
                            and card_emp >= _EMPIRICAL_PROTECT_MIN_INCLUSION
                        ):
                            emp_saved = True
                            continue  # This frontier card can't dominate; check others
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
                    if emp_saved:
                        self._tracer.record(
                            card_name=card_name,
                            stage="pareto",
                            action="protected",
                            card_id=card.get("id"),
                            score=cvar,
                            reason=f"empirical protected ({card_emp * 100:.0f}% "
                                   "of variant decks)",
                        )
                    elif edhrec_saved:
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
        """Stage 3: Derive deck template from profile.

        When Stage 2 loaded a reliable decklist corpus, its median composition
        grounds the template (lands, avg CMC, type targets) instead of the
        power-target estimates.
        """
        from sabermetrics.reasoning.template_deriver import derive_deck_template

        empirical = getattr(self, "_empirical", None)
        composition = (
            empirical.composition
            if empirical is not None and empirical.reliable
            else None
        )
        return derive_deck_template(
            profile=profile,
            budget=request.budget_usd,
            power_target=request.power_target,
            db_path=self.db_path,
            empirical_composition=composition,
        )

    def _reserve_empirical_staples(
        self, candidates, request, template, exclude_names=None,
    ) -> list:
        """Stage 4.5: Reserve differentiator slots for strong-consensus cards.

        Runs AFTER the role generators so it can reserve only cards the corpus
        validates but the generators did not already place. This lands the
        engine pieces the role scorers reject -- a treasure or sacrifice payoff
        is not "ramp", so it is reserved as a differentiator, which is what it
        actually is -- without spending a reserved slot on a genuinely-good ramp
        card the ramp generator would have taken anyway (Birds of Paradise,
        Ignoble Hierarch). Excluding those frees the cap for the payoffs.

        Bounded on purpose (ADR-005, the moneyball goal): only cards at or above
        the inclusion floor, only up to a fraction of the differentiator budget.
        Most slots stay open for the reasoning engine's undervalued picks.

        Args:
            candidates: Scored candidate dicts (carry ``_empirical_inclusion``).
            request: The build request (for the budget ceiling).
            template: Derived template (for the differentiator budget).
            exclude_names: Card names already placed (by the generators), never
                reserved -- they are in the deck already.

        Returns:
            List of SlotAssignment for the reserved cards (may be empty).
        """
        from sabermetrics.config import settings
        from sabermetrics.pipeline.generators.ramp import _load_auto_includes
        from sabermetrics.pipeline.greedy_optimizer import is_playable_as_land
        from sabermetrics.pipeline.slot_assigner import SlotAssignment

        cfg = settings.scoring

        # Auto-includes are placed by the generators, so they arrive via
        # exclude_names; keep this as a fallback for the rare unplaced one.
        auto_names, _ = _load_auto_includes()
        already = set(exclude_names or set())
        for entries in auto_names.values():
            if isinstance(entries, list):
                already.update(e["name"] for e in entries)

        # Eligible: reliable, above the inclusion floor, not a land, not already
        # placed by a generator (or an auto-include), within budget.
        eligible = [
            c for c in candidates
            if c.get("_empirical_reliable")
            and float(c.get("_empirical_inclusion", 0.0) or 0.0)
            >= cfg.empirical_reserve_min_inclusion
            and not is_playable_as_land(c.get("type_line") or "")
            and c.get("name", "") not in already
        ]

        # The cap scales with corpus size: fixed max_slots for small corpora,
        # a fraction of the eligible staples for big ones (the sweep found
        # 29-80 consensus staples vs the fixed 12), always bounded by
        # max_fraction of the differentiator budget.
        cap = min(
            max(
                cfg.empirical_reserve_max_slots,
                int(len(eligible) * cfg.empirical_reserve_eligible_fraction),
            ),
            int(template.differentiator_slots * cfg.empirical_reserve_max_fraction),
        )
        if cap <= 0:
            return []
        eligible.sort(
            key=lambda c: float(c.get("_empirical_inclusion", 0.0) or 0.0),
            reverse=True,
        )

        reserved: list[SlotAssignment] = []
        spent = 0.0
        for card in eligible:
            if len(reserved) >= cap:
                break
            price = float(card.get("price_usd", 0) or 0)
            if spent + price > request.budget_usd:
                continue
            spent += price
            rate = float(card.get("_empirical_inclusion", 0.0) or 0.0)
            reserved.append(SlotAssignment(
                card=card, slot_role="utility", score=rate,
            ))
            self._tracer.record(
                card_name=card.get("name", ""),
                stage="empirical_reserve",
                action="placed",
                card_id=card.get("id"),
                score=rate,
                reason=f"empirical staple ({rate * 100:.0f}% of variant decks)",
                # Low-volume, load-bearing stage: trace every reservation
                # regardless of watchlist (like swap_refine), so the grounding
                # is auditable.
                force=True,
            )
        if reserved:
            logger.info(
                "Stage 4.5: reserved %d empirical staples: %s",
                len(reserved),
                ", ".join(a.card.get("name", "") for a in reserved),
            )
        return reserved

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

        # Gate inheritance for candidate-table loads: the tables are queried
        # straight from the DB and bypassed every pool-level gate (price
        # ceiling, NULL-price exclusion, game-changer gate, anti-engine flag)
        # -- how an $87 Mana Vault entered a $50-ceiling deck. Generators
        # intersect table rows with this index and inherit its flags/scores.
        pool_index = {c.get("name", ""): c for c in candidates}

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
            commander_cmc=float(commander.cmc or 0),
            pool_index=pool_index,
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
            pool_index=pool_index,
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
            pool_index=pool_index,
        )
        all_assignments.extend(protection)
        budget_used += sum(float(a.card.get("price_usd", 0) or 0) for a in protection)
        _trace_infra(protection, "infra_protection")
        self._protected_names |= prot_gen.protected_names

        # 5. Lands (last, so it knows what spells need color support).
        # Budget capped at the corpus's median land spend share (x1.25 slack):
        # price-neutral scoring otherwise buys premium mana bases -- one build
        # put ~$110 of a $200 budget into lands, topped by a $45 Gemstone
        # Caverns. Real decks of the variant define what lands should cost.
        # A missing corpus share (0.0) must cap at a typical share, not fall
        # through to the entire remaining budget: one Agatha build with
        # share=0 put $160 into lands, starved the spell stages, and shipped
        # a silently-backfilled 74-land deck. Observed corpus shares run
        # 0.13-0.24, so 0.20 is a representative default.
        share = template.land_budget_share or 0.20
        land_budget = min(
            request.budget_usd - budget_used,
            request.budget_usd * share * 1.25,
        )
        land_gen = LandPackageGenerator(self.db_path)
        lands = land_gen.generate(
            color_identity=colors,
            target_count=template.land_count,
            budget_remaining=land_budget,
            template=template,
            already_placed=placed_cards(),
            role_tag_pool=_land_pool(),
        )
        all_assignments.extend(lands)
        budget_used += sum(float(a.card.get("price_usd", 0) or 0) for a in lands)

        return all_assignments, budget_used

    def _optimize_differentiators(
        self, candidates, infrastructure, profile_result, commander,
        request, template, budget_used, reserved_count=0,
    ) -> tuple[list, dict]:
        """Stage 5+6: Synergy-aware greedy optimization.

        Replaces category_coverage + _fill_differentiators with:
        1. Compute role targets (hypergeometric reliability)
        2. Build synergy matrix (rules + co-occurrence + embeddings)
        3. Greedy fill differentiator slots
        4. Swap refinement (infrastructure cards eligible)
        5. LLM safety net on weakest picks

        Args:
            reserved_count: Differentiator slots already taken by empirical
                staples reserved in Stage 3.5, subtracted from the greedy fill.

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
        # Record which pairwise signals were live (rules / embeddings).
        if hasattr(self, "_signals"):
            self._signals.update(synergy.signals)

        # 3. Greedy fill (subtract protection slots placed in Stage 4 and the
        # empirical staples reserved in Stage 3.5 -- both occupy diff slots)
        protection_placed = sum(
            1 for a in infrastructure if a.slot_role == "protection"
        )
        diff_slots = max(
            0, template.differentiator_slots - protection_placed - reserved_count
        )
        diff_assignments = greedy_fill(
            shell=infrastructure,
            candidates=candidates,
            synergy=synergy,
            role_targets=role_targets,
            budget_remaining=request.budget_usd - budget_used,
            slots_remaining=diff_slots,
            tracer=self._tracer,
            profile_signals=prof_signals,
            type_targets=template.type_targets,
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

        # Note on Option A criterion 4 (no per-card LLM in the hot path):
        # the deterministic optimizer remains the selector. The vet below is
        # ONE batched call auditing the assembled deck, not a per-card scorer
        # in the selection loop -- SME-directed final gate after build 7-9
        # showed the numeric objective cannot read oracle text.
        # 5. Budget rebalancing: spend-down upgrades, sell-one-buy-many audit
        # of expensive picks, downgrade safety net. Runs BEFORE the LLM vet:
        # the numeric objective cannot read oracle text, and in one build it
        # re-admitted Paraselene ("destroy all enchantments") right after the
        # vet had removed it. The LLM must be the final gate nothing bypasses.
        from sabermetrics.pipeline.greedy_optimizer import rebalance_budget

        all_assignments, rebalance_stats = rebalance_budget(
            all_assignments, candidates, synergy, role_targets,
            budget=request.budget_usd, template=template,
            profile_signals=prof_signals, protected_names=protected,
            tracer=self._tracer,
        )

        # 5.5 Engine-floor repair: meet hard subtype minimums (engine-30
        # rule) that soft scoring pressure never reaches -- the type-need
        # multiplier is 1.15x within 75% of target, so selection equilibrates
        # at the corpus median even when the floor sits above it. Runs after
        # rebalance (which is not type-aware and could undo it) and before
        # the vet, so every swapped-in card still faces the LLM gate.
        all_assignments, floor_swaps = self._enforce_type_floors(
            all_assignments, candidates, template,
            budget=request.budget_usd, protected_names=protected,
        )

        # 6. LLM safety net LAST: one batched Sonnet call over the riskiest
        # ~14 picks, with corpus evidence in the prompt.
        llm_cost = 0.0
        try:
            all_assignments, llm_cost = self._llm_safety_check(
                all_assignments, candidates, synergy, role_targets,
                profile_result, request, n_weakest=99,  # full-deck review: every non-staple pick faces the gate
                protected_names=protected,
            )
        except Exception as e:
            # A silently-skipped vet produced the worst deck of the project
            # (build7: an AttributeError left 29 unreviewed swaps in). Log the
            # full traceback and surface the failure in pipeline metrics.
            logger.exception("LLM safety check FAILED — deck is unvetted: %s", e)
            llm_cost = -1.0

        # Add fit reasoning for cards that lack it
        for a in all_assignments:
            if "_fit_reasoning" not in a.card:
                a.card["_fit_reasoning"] = "Synergy-optimizer selected"

        metrics = {
            "synergy_matrix_size": len(synergy.card_id_to_index),
            "role_targets": {r: t.target_count for r, t in role_targets.items()},
            "cards_swapped": swaps,
            "llm_safety_cost": llm_cost,
            "llm_safety_failed": llm_cost < 0,
            "budget_utilization": rebalance_stats.get("utilization", 0.0),
            "rebalance_upgrades": rebalance_stats.get("upgrades", 0),
            "rebalance_unbundles": rebalance_stats.get("unbundles", 0),
            "type_floor_swaps": floor_swaps,
            "objective_score": deck_objective(
                [a.card for a in all_assignments], synergy, role_targets,
                template, profile_signals=prof_signals,
            ),
        }
        return all_assignments, metrics

    def _enforce_type_floors(
        self,
        assignments: list,
        candidates: list[dict],
        template,
        budget: float,
        protected_names: set[str] | None = None,
    ) -> tuple[list, int]:
        """Repair pass: swap toward hard engine-subtype minimums.

        For each floor (e.g. aura >= 30), replaces the weakest off-type,
        non-protected, non-land picks with the best unplaced on-type
        candidates until the floor is met, candidates run out, or the budget
        can't absorb the price delta. Mirrors _enforce_legality: repair
        deterministically, then let the LLM vet audit the result.

        Args:
            assignments: Current SlotAssignment list.
            candidates: Hard-filtered candidate pool (budget/legality gated).
            template: Deck template carrying type_floors.
            budget: Total deck budget in USD.
            protected_names: Names that must not be swapped out.

        Returns:
            (assignments, swap_count).
        """
        from sabermetrics.pipeline.greedy_optimizer import (
            _empirical_bonus,
            is_playable_as_land,
        )
        from sabermetrics.pipeline.slot_assigner import SlotAssignment

        floors = getattr(template, "type_floors", None)
        if not floors:
            return assignments, 0

        protected = protected_names or set()
        deck_names = {a.card.get("name", "") for a in assignments}
        budget_left = budget - sum(
            float(a.card.get("price_usd", 0) or 0) for a in assignments
        )
        swaps = 0

        for type_name, floor in floors.items():
            def _on_type(card: dict) -> bool:
                return type_name in (card.get("type_line") or "").lower()

            count = sum(1 for a in assignments if _on_type(a.card))
            if count >= floor:
                continue

            pool = sorted(
                (
                    c for c in candidates
                    if _on_type(c)
                    and c.get("name", "") not in deck_names
                    and not c.get("_anti_engine")
                    and not is_playable_as_land(c.get("type_line") or "")
                ),
                key=lambda c: (
                    float(c.get("_cvar_score", 0) or 0) + _empirical_bonus(c)
                ),
                reverse=True,
            )
            removable = sorted(
                (
                    a for a in assignments
                    if a.card.get("name", "") not in protected
                    and not _on_type(a.card)
                    and a.slot_role != "land"
                    and not is_playable_as_land(
                        a.card.get("type_line") or ""
                    )
                ),
                key=lambda a: a.score,
            )

            for incoming in pool:
                if count >= floor or not removable:
                    break
                price_in = float(incoming.get("price_usd", 0) or 0)
                outgoing = removable[0]
                price_out = float(outgoing.card.get("price_usd", 0) or 0)
                if price_in - price_out > budget_left:
                    continue  # try a cheaper on-type candidate
                removable.pop(0)
                idx = assignments.index(outgoing)
                score = round(
                    min(
                        1.0,
                        float(incoming.get("_cvar_score", 0) or 0)
                        + _empirical_bonus(incoming),
                    ),
                    4,
                )
                assignments[idx] = SlotAssignment(
                    card=incoming,
                    slot_role=outgoing.slot_role,
                    score=score,
                    alternatives=[],
                )
                deck_names.discard(outgoing.card.get("name", ""))
                deck_names.add(incoming.get("name", ""))
                budget_left -= price_in - price_out
                count += 1
                swaps += 1
                if self._tracer is not None:
                    reason = (
                        f"type floor: {type_name} {count - 1} < {floor}"
                    )
                    self._tracer.record(
                        card_name=outgoing.card.get("name", ""),
                        stage="type_floor", action="swapped_out",
                        card_id=outgoing.card.get("id"),
                        score=outgoing.score, reason=reason, force=True,
                    )
                    self._tracer.record(
                        card_name=incoming.get("name", ""),
                        stage="type_floor", action="swapped_in",
                        card_id=incoming.get("id"), score=score,
                        reason=f"replaced {outgoing.card.get('name', '')}",
                        force=True,
                    )

            if count < floor:
                logger.warning(
                    "Type floor unmet after repair: %s %d < %d",
                    type_name, count, floor,
                )

        return assignments, swaps

    @staticmethod
    def _safety_review_order(indexed, corpus_active: bool, threshold: float):
        """Order review candidates: uncorroborated picks first, then weakest.

        The weakest-N ordering missed the real failure mode -- cards the
        synergy matrix ranked highly on rule/embedding text matches with zero
        support in the variant's real decks (Tallowisp "fetches Auras" but
        needs Spirits; Yiazmat matched on nothing but embedding noise). With a
        reliable corpus, those uncorroborated picks are the highest-risk
        cohort, so they are reviewed before merely weak corroborated ones.

        Args:
            indexed: (deck_index, assignment) pairs eligible for review.
            corpus_active: Whether a reliable empirical corpus exists.
            threshold: Inclusion rate below which a pick is uncorroborated.

        Returns:
            The pairs sorted for review.
        """
        def sort_key(pair):
            _, a = pair
            emp = float(a.card.get("_empirical_inclusion", 0.0) or 0.0)
            corroborated = 1 if (not corpus_active or emp >= threshold) else 0
            return (corroborated, a.score)

        return sorted(indexed, key=sort_key)

    @staticmethod
    def _best_replacement(
        candidates,
        deck_names: set[str],
        max_price: float | None = None,
        corpus_active: bool = False,
        corroboration_threshold: float = 0.0,
        corroborated_only: bool = False,
    ):
        """Pick the strongest eligible replacement, not the first in list order.

        Ranked by (corroboration tier, CVAR + empirical bonus). With a
        reliable corpus, any candidate real decks actually play outranks every
        uncorroborated text-matcher regardless of numeric score -- Eiganjo
        Dynastorian ("return all enchantments" text, 0% inclusion, an attack
        condition the scorer can't read) twice entered as a vet replacement
        this way. Uncorroborated cards remain eligible when nothing
        corroborated is affordable, so this is a preference, not a penalty
        (ADR-005 absence-neutrality holds for general scoring).
        """
        from sabermetrics.analytics.empirical_valuation import empirical_bonus
        from sabermetrics.config import settings

        best, best_key = None, (-1, -1.0)
        for c in candidates:
            if (
                c.get("name", "") in deck_names
                or "land" in (c.get("type_line") or "").lower()
                or c.get("_anti_engine")
            ):
                continue
            if max_price is not None and float(c.get("price_usd", 0) or 0) > max_price:
                continue
            value = float(c.get("_cvar_score", 0.0) or 0.0) + empirical_bonus(
                c,
                settings.scoring.marginal_empirical_weight,
                settings.scoring.marginal_empirical_noisy_weight,
            )
            inclusion = float(c.get("_empirical_inclusion", 0.0) or 0.0)
            tier = (
                1 if not corpus_active or inclusion >= corroboration_threshold
                else 0
            )
            # corroborated_only: hard gate, not a preference. Used by the
            # re-vet round, whose picks are accepted without further review
            # -- an unreviewed slot may only be filled by a card real decks
            # play (Agatha: 33 unreviewed text-matchers entered this way).
            if corroborated_only and corpus_active and tier == 0:
                continue
            if (tier, value) > best_key:
                best, best_key = c, (tier, value)
        return best

    def _llm_safety_check(
        self, deck, candidates, synergy, role_targets,
        profile_result, request, n_weakest=8,
        protected_names: set[str] | None = None,
    ) -> tuple[list, float]:
        """Score the riskiest picks via Haiku and swap out poor fits.

        Args:
            deck: Current deck assignments.
            candidates: Full candidate pool.
            synergy: Synergy matrix.
            role_targets: Role targets.
            profile_result: Commander profile result.
            request: Build request.
            n_weakest: Number of cards to check.
            protected_names: Card names that cannot be replaced (staple protection).

        Returns:
            Tuple of (possibly-modified deck, LLM cost).
        """
        from sabermetrics.config import settings
        from sabermetrics.pipeline.slot_assigner import SlotAssignment

        protected = protected_names or set()

        # Review candidates: non-land, non-protected
        indexed = [
            (i, a) for i, a in enumerate(deck)
            if a.slot_role != "land"
            and "land" not in (a.card.get("type_line") or "").lower()
            and a.card.get("name", "") not in protected
        ]
        empirical = getattr(self, "_empirical", None)
        corpus_active = empirical is not None and bool(empirical.reliable)
        indexed = self._safety_review_order(
            indexed, corpus_active,
            settings.scoring.safety_uncorroborated_max_inclusion,
        )
        weakest = indexed[:n_weakest]

        if not weakest:
            return deck, 0.0

        profile_summary = self._build_profile_summary(profile_result)
        total_cost = 0.0

        try:
            from sabermetrics.reasoning.fit import FitScorer

            scorer = FitScorer(self.db_path)
            weak_cards = [deck[i].card for i, _ in weakest]

            results = scorer.score_cards_batch(
                cards=weak_cards,
                profile_summary=profile_summary,
                archetype_definition=profile_result.profile.strategic_profile.primary_archetype,
                partial_deck=[a.card for a in deck],
                empirical_variant=getattr(self, "_empirical_variant", None),
            )

            # Replace cards scored <= 3 with next-best candidate
            deck_names = {a.card.get("name", "") for a in deck}
            corroboration_threshold = (
                settings.scoring.safety_uncorroborated_max_inclusion
            )

            def _replace_bad_fits(
                scored, stage: str, corroborated_only: bool = False,
            ) -> list[int]:
                """Swap out picks the LLM scored <= 3; return swap-in indices."""
                swapped_in: list[int] = []
                for deck_idx, card, fit_response in scored:
                    card["_fit_reasoning"] = fit_response.reasoning
                    self._tracer.record(
                        card_name=card.get("name", ""),
                        stage=stage,
                        action="considered",
                        card_id=card.get("id"),
                        score=float(fit_response.fit_score),
                        reason=fit_response.reasoning,
                        force=True,
                    )
                    if fit_response.fit_score > 3:
                        continue
                    budget_left = request.budget_usd - sum(
                        float(a.card.get("price_usd", 0) or 0) for a in deck
                    )
                    old_price = float(card.get("price_usd", 0) or 0)
                    replacement = self._best_replacement(
                        candidates, deck_names,
                        max_price=old_price + max(0.0, budget_left),
                        corpus_active=corpus_active,
                        corroboration_threshold=corroboration_threshold,
                        corroborated_only=corroborated_only,
                    )
                    if not replacement:
                        continue
                    old_name = card.get("name", "")
                    new_name = replacement.get("name", "")
                    role = _heuristic_role(replacement)
                    replacement["_fit_reasoning"] = (
                        f"Replaced {old_name} (LLM score {fit_response.fit_score})"
                    )
                    deck[deck_idx] = SlotAssignment(
                        card=replacement,
                        slot_role=role,
                        score=replacement.get("_cvar_score", 0.0),
                        alternatives=[],
                    )
                    deck_names.discard(old_name)
                    deck_names.add(new_name)
                    self._tracer.record(
                        card_name=old_name,
                        stage=stage,
                        action="swapped_out",
                        card_id=card.get("id"),
                        score=float(fit_response.fit_score),
                        reason=f"LLM fit score {fit_response.fit_score} <= 3",
                        force=True,
                    )
                    self._tracer.record(
                        card_name=new_name,
                        stage=stage,
                        action="swapped_in",
                        card_id=replacement.get("id"),
                        score=replacement.get("_cvar_score", 0.0),
                        reason=f"replaced {old_name} (LLM score {fit_response.fit_score})",
                        force=True,
                    )
                    logger.info(
                        "LLM safety (%s): replaced %s (score %d) with %s",
                        stage, old_name, fit_response.fit_score, new_name,
                    )
                    swapped_in.append(deck_idx)
                return swapped_in

            scored = [
                (deck_idx, card, fit_response)
                for (deck_idx, _assignment), (card, fit_response)
                in zip(weakest, results)
            ]
            swap_in_idxs = _replace_bad_fits(scored, "llm_safety")

            # Re-vet round: the replacements above entered unreviewed (the
            # pipeline's last unvetted door -- Eiganjo Dynastorian came
            # through it in two consecutive builds). One more small batched
            # call over just the swap-ins; their own replacements are
            # corroboration-ranked and accepted without a third round.
            if swap_in_idxs:
                revet_results = scorer.score_cards_batch(
                    cards=[deck[i].card for i in swap_in_idxs],
                    profile_summary=profile_summary,
                    archetype_definition=profile_result.profile.strategic_profile.primary_archetype,
                    partial_deck=[a.card for a in deck],
                    empirical_variant=getattr(self, "_empirical_variant", None),
                )
                revet_scored = [
                    (deck_idx, card, fit_response)
                    for deck_idx, (card, fit_response)
                    in zip(swap_in_idxs, revet_results)
                ]
                # Round-2 replacements are accepted without further review,
                # so they are restricted to corpus-corroborated cards: an
                # unreviewed slot never gets an unreviewed text-matcher.
                second_round = _replace_bad_fits(
                    revet_scored, "llm_safety_revet", corroborated_only=True,
                )
                if second_round:
                    logger.info(
                        "LLM safety re-vet: %d second-round replacements "
                        "(accepted without further review)", len(second_round),
                    )

        except Exception as e:
            logger.warning("LLM safety net scoring failed: %s", e)

        return deck, total_cost

    def _build_profile_summary(self, profile_result) -> str:
        """Build the profile summary string for LLM fit scoring."""
        # Unwrap: profile_result is ProfileResult, .profile is CommanderProfile
        profile = profile_result.profile
        sp = profile.strategic_profile
        profile_summary = (
            f"Commander: {profile.commander_name}\n"
            f"Archetype: {sp.primary_archetype}\n"
            f"Game Plan: {sp.game_plan_summary}\n"
            f"Win Conditions: "
            + ", ".join(
                wc.description for wc in sp.win_conditions
            )
        )

        # Add value inversions
        if sp.value_inversions:
            inversions = sp.value_inversions
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
        if hasattr(sp, "engine_dependencies"):
            deps = sp.engine_dependencies
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
        if hasattr(sp, "mispriced_card_examples"):
            examples = sp.mispriced_card_examples
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
        # This is a last-resort repair for a card or two -- a large fill
        # means an upstream stage under-produced (a $160 land overspend once
        # starved the spell stages into a silently-shipped 74-land deck), so
        # it must be LOUD: traced and warned, never invisible.
        if len(kept) < 99:
            shortfall = 99 - len(kept)
            if shortfall > 2:
                logger.warning(
                    "Legality repair backfilling %d basics -- an upstream "
                    "stage under-produced; inspect the build", shortfall,
                )
            self._tracer.record(
                card_name=f"{shortfall}x basic land", stage="legality",
                action="placed", reason="backfill to 99 (upstream shortfall)",
                force=True,
            )
            kept.extend(
                _make_basic_lands(shortfall, commander.color_identity or [])
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
            if hasattr(self, "_signals"):
                self._signals["narrative"] = True
            return narrative, cost
        except Exception as e:
            logger.warning("Narrative synthesis failed: %s", e)
            if hasattr(self, "_signals"):
                self._signals["narrative"] = False
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
                signals_used=sorted(k for k, v in self._signals.items() if v),
                signals_unavailable=sorted(
                    k for k, v in self._signals.items() if not v
                ),
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
                "signals_used": deck.meta.signals_used,
                "signals_unavailable": deck.meta.signals_unavailable,
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
        # uuid suffix avoids id collisions with basics minted elsewhere (e.g.
        # the mana-base builder), which also use a "basic-<name>-<n>" scheme.
        out.append(SlotAssignment(
            card={
                "id": f"basic-{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:8]}",
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
