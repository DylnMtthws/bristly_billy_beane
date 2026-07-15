"""Generated deck models."""

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .card import Card


class CVARWeights(BaseModel):
    """Weights for the CVAR composite score."""

    # Calibrated against real decklists (Option A criterion 6). price_efficiency
    # was reduced from 0.15 to 0.05 — as a *ranking* term it rewarded cheap
    # vanilla cards (budget is enforced as a constraint elsewhere, not by
    # preferring the cheapest card); that weight moved to synergy.
    synergy: float = 0.45
    replacement_value: float = 0.25
    mana_efficiency: float = 0.25
    price_efficiency: float = 0.05


class CardSubScores(BaseModel):
    """Individual scoring components for a card."""

    synergy: float
    mana_efficiency: float
    replacement_value: float
    price_efficiency: float
    card_win_equity: Optional[float] = None


class LLMFit(BaseModel):
    """LLM-assessed fit score for a card."""

    score: int = Field(ge=1, le=10)
    reasoning: str


class DeckCard(BaseModel):
    """A card selected for a generated deck with scoring metadata."""

    card: Card
    slot_role: Literal[
        "ramp", "draw", "removal", "protection", "wincon", "utility", "land", "other"
    ]
    cvar_score: float
    sub_scores: CardSubScores
    llm_fit: LLMFit
    alternatives: List[str]  # card_ids


class DeckParameters(BaseModel):
    """Parameters used to generate a deck."""

    budget_usd: float
    power_target: int
    strategy: Optional[str] = None
    weights: CVARWeights
    deck_name: Optional[str] = None


class ComponentCounts(BaseModel):
    """Counts of functional components in a deck."""

    ramp: int
    draw: int
    removal: int
    board_wipes: int
    tutors: int
    win_conditions: int


class DeckComposition(BaseModel):
    """Statistical breakdown of a generated deck."""

    total_price_usd: float
    average_cmc: float
    color_distribution: Dict[str, int]
    type_distribution: Dict[str, int]
    mana_curve: List[int]  # Index = CMC, value = count
    component_counts: ComponentCounts
    game_changers_present: List[str]  # card_ids
    detected_combos: List[str]  # combo_ids


class DeckClassification(BaseModel):
    """Power bracket classification."""

    estimated_bracket: int = Field(ge=1, le=5)
    bracket_reasoning: str


class DeckNarrative(BaseModel):
    """LLM-generated narrative about the deck."""

    game_plan: str
    key_synergies: List[str]
    weaknesses: List[str]
    suggested_play_pattern: str


class GenerationMeta(BaseModel):
    """Metadata about deck generation."""

    generation_time_seconds: float
    llm_cost_usd: float
    source_profile_id: str
    # Observable degradation: which scoring/data signals were live for this
    # build, and which were unavailable (e.g. embeddings failed to load, no
    # EDHREC data for the commander, narrative LLM unavailable).
    signals_used: list[str] = Field(default_factory=list)
    signals_unavailable: list[str] = Field(default_factory=list)


class GeneratedDeck(BaseModel):
    """A fully generated Commander deck."""

    id: str
    commander: Card
    generated_at: datetime
    parameters: DeckParameters
    cards: List[DeckCard]  # Should be exactly 99
    composition: DeckComposition
    classification: DeckClassification
    narrative: DeckNarrative
    meta: GenerationMeta
