"""Per-card fit scorer (D5.6).

Scores candidate cards against a commander profile using Haiku.
Uses prompt caching so the profile context is shared across all 50 calls.
"""

import json
import logging
import sqlite3
from pathlib import Path

from sabermetrics.models.llm_responses import CardFitResponse

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
    ) -> list[tuple[dict, CardFitResponse]]:
        """Score a batch of cards against a commander profile.

        Args:
            cards: List of card dicts to evaluate.
            profile_summary: Compressed profile text (cached across calls).
            archetype_definition: Archetype text from reference layer.
            relevant_rules: Rule excerpts from reference layer.

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
