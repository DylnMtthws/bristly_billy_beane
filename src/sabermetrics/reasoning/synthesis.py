"""Deck synthesis narrative generator (D5.7).

Generates deck-level narrative and strategic analysis via Sonnet.
"""

import json
import logging
from pathlib import Path

from sabermetrics.models.llm_responses import DeckSynthesisResponse

logger = logging.getLogger(__name__)


class DeckSynthesizer:
    """Generate deck-level narrative and analysis."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def synthesize(
        self,
        profile_summary: str,
        deck_cards_with_reasoning: list[dict],
        bracket: int,
        bracket_reasoning: str,
    ) -> tuple[DeckSynthesisResponse, float]:
        """Generate a strategic narrative for a completed deck.

        Args:
            profile_summary: Compressed commander profile text.
            deck_cards_with_reasoning: List of dicts with card info and
                fit reasoning.
            bracket: Power bracket classification (1-5).
            bracket_reasoning: Explanation of bracket classification.

        Returns:
            Tuple of (DeckSynthesisResponse, cost_usd).
        """
        from sabermetrics.config import settings
        from sabermetrics.reasoning.client import AnthropicClient
        from sabermetrics.reasoning.prompts import load_prompt

        client = AnthropicClient.get_instance(self.db_path)
        template = load_prompt("deck_synthesis")

        # Format deck summary
        deck_summary_lines = []
        for card_info in deck_cards_with_reasoning:
            name = card_info.get("name", "Unknown")
            role = card_info.get("slot_role", "other")
            score = card_info.get("fit_score", "?")
            reasoning = card_info.get("reasoning", "")
            deck_summary_lines.append(
                f"- {name} [{role}] (fit: {score}/10): {reasoning}"
            )

        deck_summary = "\n".join(deck_summary_lines)

        prompt_text = template.format(
            profile_summary=profile_summary,
            deck_summary_with_fit_reasoning=deck_summary,
            bracket=bracket,
            bracket_reasoning=bracket_reasoning,
        )

        system = (
            "You are writing the strategic narrative for a generated "
            "Commander deck. Always output valid JSON matching the "
            "requested format."
        )

        result = client.call_with_cache(
            model=settings.llm.synthesis_model,
            system=system,
            messages=[{"role": "user", "content": prompt_text}],
            cache_breakpoints=[],
            max_tokens=2000,
            temperature=0.0,
            call_type="deck_synthesis",
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
        synthesis = DeckSynthesisResponse(**data)

        logger.info(
            "Deck synthesis complete: %d synergies, %d weaknesses, $%.4f",
            len(synthesis.key_synergies),
            len(synthesis.weaknesses),
            result.cost_usd,
        )

        return synthesis, result.cost_usd
