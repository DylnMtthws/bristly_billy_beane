"""LLM response models for structured output parsing."""

from typing import List, Literal

from pydantic import BaseModel, Field


class CardFitResponse(BaseModel):
    """Output from per-card fit scoring (Haiku)."""

    fit_score: int = Field(ge=1, le=10)
    reasoning: str
    slot_role: Literal[
        "ramp", "draw", "removal", "wincon", "utility", "land", "other"
    ]


class RelevanceScreenResponse(BaseModel):
    """Output from set-release relevance screening (Haiku)."""

    card_name: str
    affects_strategy: bool
    reason: str


class DeckSynthesisResponse(BaseModel):
    """Output from deck-level synthesis (Sonnet)."""

    game_plan: str
    key_synergies: List[str]
    weaknesses: List[str]
    suggested_play_pattern: str
