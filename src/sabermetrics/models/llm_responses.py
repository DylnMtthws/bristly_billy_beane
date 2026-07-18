"""LLM response models for structured output parsing."""

from typing import List

from pydantic import BaseModel, Field


class CardFitResponse(BaseModel):
    """Output from per-card fit scoring (Haiku)."""

    fit_score: int = Field(ge=1, le=10)
    reasoning: str
    slot_role: str = "other"

    def __init__(self, **data):
        # Normalize slot_role: map unexpected LLM values to canonical roles
        canonical = {"ramp", "draw", "removal", "wincon", "utility", "land", "other"}
        role = data.get("slot_role", "other")
        if role not in canonical:
            data["slot_role"] = "other"
        super().__init__(**data)


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


class VariantCharacterization(BaseModel):
    """One cluster's qualitative sub-variant characterization (a hypothesis)."""

    cluster_id: int
    variant_name: str
    game_plan: str = ""
    key_cards: List[str] = Field(default_factory=list)
    differentiators: str = ""
    confidence: str = ""


class ClusterVariantsResponse(BaseModel):
    """Output from the Phase 4b LLM variant-characterization pass (Sonnet)."""

    variants: List[VariantCharacterization] = Field(default_factory=list)
    overall_note: str = ""
