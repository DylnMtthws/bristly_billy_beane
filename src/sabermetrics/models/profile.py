"""Commander intent profile models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CardAnalysis(BaseModel):
    """Stream 1: Card-derived intent."""

    mana_cost: str
    color_identity: list[str]
    core_mechanic: str
    triggered_abilities: list[str]
    activated_abilities: list[str]
    static_abilities: list[str]
    evasion_or_protection: str | None = None


class TopCard(BaseModel):
    """A card with its EDHREC inclusion percentage."""

    card_name: str
    inclusion_pct: float


class BehavioralSignals(BaseModel):
    """Stream 2: Behavioral aggregate."""

    total_decks_tracked: int
    edhrec_themes: list[str]
    most_included_cards: list[TopCard]
    average_deck_price_usd: float
    average_cmc: float
    tournament_win_rate: float | None = None
    tournament_sample_size: int = 0


class CommunitySignals(BaseModel):
    """Stream 3: Cultural signal."""

    reddit_thread_count: int
    named_archetypes: list[str]
    primer_articles_referenced: list[str]
    emerging_strategies: list[str] = Field(default_factory=list)


class WinCondition(BaseModel):
    """A deck win condition."""

    description: str
    key_cards: list[str]
    reliability: Literal["primary", "secondary", "backup"]


class BuildPath(BaseModel):
    """A possible build direction for a commander."""

    name: str
    description: str
    consensus_status: Literal["mainstream", "emerging", "underexplored"]
    key_card_categories: list[str]


class AntiSynergy(BaseModel):
    """Cards or strategies that work against the commander."""

    description: str
    cards_to_avoid: list[str]
    reasoning: str


class ValueInversion(BaseModel):
    """A heuristic the commander inverts."""

    normal_heuristic: str
    inverted_value: str
    desired_characteristics: list[str]
    undesired_characteristics: list[str] = Field(default_factory=list)
    evaluation_guidance: str


class EngineDependency(BaseModel):
    """A causal dependency in the commander's strategy.

    Captures the distinction between the commander's core engine
    (what the deck must accumulate/activate) and the engine's outputs
    (effects produced as consequences of the engine running).
    """

    engine: str  # e.g. "Auras you control"
    engine_card_traits: list[str]  # e.g. ["aura", "enchant creature", "bestow"]
    dependent_outputs: list[str]  # e.g. ["life drain", "creature lockdown"]
    false_synergy_warning: (
        str  # e.g. "Lifegain cards that don't interact with Auras..."
    )


class MispricedCardExample(BaseModel):
    """A card undervalued by generic heuristics but excellent for this commander."""

    card_name: str
    why_undervalued: str


class StrategicConstraints(BaseModel):
    """Constraints on how the deck should be built."""

    mana_base_requirements: str
    interaction_density: Literal["high", "medium", "low"]
    speed_tier: Literal["fast", "midrange", "slow"]


class PowerIndicators(BaseModel):
    """Estimated power level range."""

    estimated_ceiling_bracket: int = Field(ge=1, le=5)
    estimated_floor_bracket: int = Field(ge=1, le=5)
    notes: str


class StrategicProfile(BaseModel):
    """Full strategic analysis of a commander."""

    primary_archetype: str
    game_plan_summary: str
    win_conditions: list[WinCondition]
    build_paths: list[BuildPath]
    synergy_priorities: dict[str, list[str]]
    anti_synergies: list[AntiSynergy]
    strategic_constraints: StrategicConstraints
    power_indicators: PowerIndicators
    value_inversions: list[ValueInversion] = Field(default_factory=list)
    engine_dependencies: list[EngineDependency] = Field(default_factory=list)
    mispriced_card_examples: list[MispricedCardExample] = Field(default_factory=list)


class UserIntent(BaseModel):
    """Optional user-provided build direction."""

    provided: bool
    description: str | None = None
    divergence_from_consensus: str | None = None


class EvidenceFreshness(BaseModel):
    """Timestamps of evidence source data."""

    edhrec_last_updated: datetime | None = None
    topdeck_last_updated: datetime | None = None
    reddit_last_searched: datetime | None = None


class ProfileSources(BaseModel):
    """Sources used to generate the profile."""

    rules_chunks_referenced: list[str] = Field(default_factory=list)
    articles_referenced: list[str] = Field(default_factory=list)
    evidence_freshness: EvidenceFreshness


class CommanderProfile(BaseModel):
    """Top-level commander profile structure."""

    commander_id: str
    commander_name: str
    generated_at: datetime
    set_version: str
    schema_version: str = "1.0"

    card_analysis: CardAnalysis
    behavioral_signals: BehavioralSignals
    community_signals: CommunitySignals
    strategic_profile: StrategicProfile
    user_intent: UserIntent
    sources: ProfileSources
