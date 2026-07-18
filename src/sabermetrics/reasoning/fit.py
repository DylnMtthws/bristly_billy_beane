"""Per-card fit scorer (D5.6, updated for 6.5.7 deck-context awareness).

Scores candidate cards against a commander profile using Haiku.
Uses prompt caching so the profile context is shared across all 50 calls.
The deck composition context is passed in the per-request section for
deck-context-aware scoring.
"""

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from sabermetrics.models.llm_responses import CardFitResponse
from sabermetrics.models.template import SlotIntent

if TYPE_CHECKING:
    from sabermetrics.reasoning.client import AnthropicClient

logger = logging.getLogger(__name__)


class FitScorer:
    """Score candidate cards for fit with a commander profile."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def score_cards(
        self,
        cards: list[dict],
        profile_summary: str,
        archetype_definition: str = "",
        relevant_rules: str = "",
        partial_deck: list[dict] | None = None,
        slot_intents: list[SlotIntent] | None = None,
    ) -> list[tuple[dict, CardFitResponse]]:
        """Score a batch of cards against a commander profile.

        Args:
            cards: List of card dicts to evaluate.
            profile_summary: Compressed profile text (cached across calls).
            archetype_definition: Archetype text from reference layer.
            relevant_rules: Rule excerpts from reference layer.
            partial_deck: Infrastructure cards already placed (for context).
            slot_intents: Category coverage intents (what the deck still needs).

        Returns:
            List of (card_dict, CardFitResponse) tuples.
        """
        from sabermetrics.config import settings
        from sabermetrics.reasoning.client import AnthropicClient
        from sabermetrics.reasoning.prompts import load_prompt

        client = AnthropicClient.get_instance(self.db_path)
        template = load_prompt("card_fit")

        # Build cached system prompt
        system = (
            "You are evaluating individual Magic: The Gathering cards for "
            "inclusion in a Commander deck. Score each card 1-10 for fit "
            "with the given strategy. Always output valid JSON."
        )

        results: list[tuple[dict, CardFitResponse]] = []

        # Build deck composition context (shared across all card evaluations)
        deck_context = _build_deck_composition_context(partial_deck, slot_intents)

        for i, card in enumerate(cards):
            try:
                fit_response = self._score_single_card(
                    client=client,
                    template=template,
                    system=system,
                    card=card,
                    profile_summary=profile_summary,
                    archetype_definition=archetype_definition,
                    relevant_rules=relevant_rules,
                    model=settings.llm.fit_model,
                    deck_composition_context=deck_context,
                )
                results.append((card, fit_response))
                logger.debug(
                    "Card %d/%d: %s → score %d",
                    i + 1, len(cards),
                    card.get("name", "?"), fit_response.fit_score,
                )
            except Exception as e:
                logger.warning(
                    "Failed to score card %s: %s",
                    card.get("name", "?"), e,
                )
                # Provide default score on failure
                results.append((
                    card,
                    CardFitResponse(
                        fit_score=5,
                        reasoning="Scoring failed; default score assigned.",
                        slot_role="other",
                    ),
                ))

        logger.info("Scored %d/%d cards successfully", len(results), len(cards))
        return results

    def _score_single_card(
        self,
        client: "AnthropicClient",  # type: ignore[name-defined]
        template: str,
        system: str,
        card: dict,
        profile_summary: str,
        archetype_definition: str,
        relevant_rules: str,
        model: str,
        deck_composition_context: str = "",
    ) -> CardFitResponse:
        """Score a single card via LLM call."""
        # Format the prompt
        prompt_text = template.format(
            archetype_definition=archetype_definition or "No specific archetype definition available.",
            relevant_rule_excerpts=relevant_rules or "No specific rule excerpts.",
            profile_summary=profile_summary,
            card_name=card.get("name", "Unknown"),
            mana_cost=card.get("mana_cost", "N/A"),
            type_line=card.get("type_line", "Unknown"),
            oracle_text=card.get("oracle_text", "No text"),
            price=f"{card.get('price_usd', 0) or 0:.2f}",
            inclusion_pct=f"{card.get('edhrec_inclusion_pct', 0) or 0:.1f}",
            cwe_score=f"{card.get('cwe_score', 'N/A')}",
            cooccurrence_avg=f"{card.get('cooccurrence_avg', 0) or 0:.2f}",
            deck_composition_context=deck_composition_context or "No deck context available yet.",
        )

        # The cached section is the profile + archetype + rules (message 0)
        # Per-card data is in the same message but can't be split further
        # with this template structure. Cache the first message.
        result = client.call_with_cache(
            model=model,
            system=system,
            messages=[{"role": "user", "content": prompt_text}],
            cache_breakpoints=[0],
            max_tokens=500,
            temperature=0.0,
            call_type="card_fit",
        )

        # Parse response
        response_text = result.content.strip()
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

        data = json.loads(response_text)
        return CardFitResponse(**data)


    def score_cards_batch(
        self,
        cards: list[dict],
        profile_summary: str,
        archetype_definition: str = "",
        partial_deck: list[dict] | None = None,
        empirical_variant: str | None = None,
    ) -> list[tuple[dict, CardFitResponse]]:
        """Score all cards in ONE call, with per-card corpus evidence.

        Replaces the per-card loop for the safety-net vet. One call sends the
        shared context once instead of N times (the per-card shape cost ~3x
        more at the same model tier) and lets the model judge the docket
        holistically -- cards are compared against each other and the deck,
        which is exactly the "is this worth a slot HERE" question.

        Each card line carries the evidence the vet previously never saw:
        corpus inclusion in the target variant (the Gravebreaker Lamia miss --
        0 of 59 real decks ran it, and the vet scored it 4/10 because nobody
        told it), price, and roles.

        Args:
            cards: Cards under review (carry _empirical_inclusion etc.).
            profile_summary: Compressed profile text.
            archetype_definition: Archetype text from the reference layer.
            partial_deck: Current deck for composition context.
            empirical_variant: Name of the corpus variant, for the evidence line.

        Returns:
            List of (card, CardFitResponse) aligned with the input order.
        """
        from sabermetrics.config import settings
        from sabermetrics.reasoning.client import AnthropicClient

        client = AnthropicClient.get_instance(self.db_path)

        deck_context = _build_deck_composition_context(partial_deck, None)
        variant = empirical_variant or "unknown"

        card_lines = []
        for i, card in enumerate(cards):
            rate = float(card.get("_empirical_inclusion", 0.0) or 0.0)
            card_lines.append(
                f"{i + 1}. {card.get('name', '?')} | {card.get('mana_cost', 'N/A')} | "
                f"{card.get('type_line', '?')} | ${card.get('price_usd', 0) or 0:.2f}\n"
                f"   Text: {(card.get('oracle_text') or 'No text')[:400]}\n"
                f"   Evidence: appears in {rate * 100:.0f}% of real "
                f"'{variant}' decks for this commander."
            )

        system = (
            "You are the final quality gate for a Commander deck generator. "
            "Score each listed card 1-10 for fit with the deck's strategy. "
            "A card with near-zero real-deck inclusion needs a strong "
            "text-based justification to score above 3 -- community absence "
            "is evidence, though genuinely synergistic sleepers do exist. "
            "Judge cards relative to each other and to the deck context. "
            "Mechanics checks: planeswalkers need board presence to "
            "survive (check the deck's creature count); vehicles need "
            "crew bodies and must survive blocks (crew cost vs toughness); "
            "score any card that mass-removes the deck's own engine type 1. "
            "For aura-engine decks: cheap (1-2 mana) auras that stop an "
            "attacker are legitimate engine fuel even if generically weak; "
            "commander-protection effects are top value (the deck does "
            "nothing without its commander, and debuff auras backfire if "
            "it leaves); cantrip auras stack with enchantress draw "
            "engines; board wipes must be one-sided (condition sparing "
            "a small, cheap board) -- uniform wipes score low. "
            "Output ONLY a JSON array, one object per card, in the same "
            'order: [{"name": str, "fit_score": int, "reasoning": str}]'
        )
        prompt = (
            f"DECK STRATEGY:\n{profile_summary}\n\n"
            f"ARCHETYPE: {archetype_definition or 'n/a'}\n\n"
            f"CURRENT DECK:\n{deck_context}\n\n"
            f"CARDS UNDER REVIEW ({len(cards)}):\n" + "\n".join(card_lines)
        )

        result = client.call_with_cache(
            model=settings.llm.fit_model,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            cache_breakpoints=[0],
            max_tokens=12000,
            call_type="card_fit_batch",
        )

        by_name: dict[str, CardFitResponse] = {}
        text = result.content.strip()
        try:
            start, end = text.find("["), text.rfind("]")
            items = json.loads(text[start:end + 1])
        except Exception:
            # Truncated output loses the closing bracket and the whole-array
            # parse fails -- build9 defaulted all 47 verdicts to 5 and the
            # vet fired blanks. Salvage every complete object individually.
            import re as _re
            items = []
            for m in _re.finditer(r"\{[^{}]*\}", text):
                try:
                    items.append(json.loads(m.group(0)))
                except Exception:
                    continue
            logger.warning(
                "Batch fit array parse failed; salvaged %d/%d verdicts "
                "(tail: %r)", len(items), len(cards), text[-120:],
            )
        for item in items:
            try:
                by_name[str(item.get("name", "")).lower()] = CardFitResponse(
                    fit_score=max(1, min(10, int(item.get("fit_score", 5)))),
                    reasoning=str(item.get("reasoning", ""))[:500],
                )
            except Exception:
                continue

        out: list[tuple[dict, CardFitResponse]] = []
        for card in cards:
            resp = by_name.get(
                (card.get("name") or "").lower(),
                CardFitResponse(fit_score=5, reasoning="No verdict returned."),
            )
            out.append((card, resp))
        logger.info(
            "Batch vet: %d cards in one call, %d verdicts parsed",
            len(cards), len(by_name),
        )
        return out


def _build_deck_composition_context(
    partial_deck: list[dict] | None,
    slot_intents: list[SlotIntent] | None,
) -> str:
    """Build deck composition context for the per-request prompt section.

    Args:
        partial_deck: Infrastructure cards already placed in the deck.
        slot_intents: What categories the deck still needs.

    Returns:
        Formatted string for the {deck_composition_context} placeholder.
    """
    if not partial_deck:
        return ""

    lines: list[str] = []

    # Role counts
    role_counts: dict[str, int] = {}
    for card in partial_deck:
        role_tags_raw = card.get("role_tags", "[]")
        if isinstance(role_tags_raw, str):
            try:
                role_tags = json.loads(role_tags_raw)
            except (json.JSONDecodeError, TypeError):
                role_tags = []
        else:
            role_tags = role_tags_raw or []
        for tag in role_tags:
            role_counts[tag] = role_counts.get(tag, 0) + 1

    lines.append(f"Cards already placed: {len(partial_deck)}")
    if role_counts:
        role_str = ", ".join(f"{k}: {v}" for k, v in sorted(role_counts.items()))
        lines.append(f"Role distribution: {role_str}")

    # Key cards (top 10 by CVAR score)
    scored = sorted(
        partial_deck,
        key=lambda c: c.get("_cvar_score", 0),
        reverse=True,
    )[:10]
    if scored:
        key_names = [c.get("name", "?") for c in scored]
        lines.append(f"Key infrastructure cards: {', '.join(key_names)}")

    # Slot intents
    if slot_intents:
        needs = []
        for intent in slot_intents[:5]:
            needs.append(
                f"{intent.category} (need {intent.slots_to_fill} more, "
                f"have {intent.current_count}/{intent.target_count})"
            )
        lines.append(f"Categories still needed: {'; '.join(needs)}")

    return "\n".join(lines)
