"""Commander intent profile models."""

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class CardAnalysis(BaseModel):
    """Stream 1: Card-derived intent."""

    mana_cost: str
    color_identity: List[str]
    core_mechanic: str
    triggered_abilities: List[str]
    activated_abilities: List[str]
    static_abilities: List[str]
    evasion_or_protection: Optional[str] = None


class TopCard(BaseModel):
    """A card with its EDHREC inclusion percentage."""

    card_name: str
    inclusion_pct: float


class BehavioralSignals(BaseModel):
    """Stream 2: Behavioral aggregate."""

    total_decks_tracked: int
    edhrec_themes: List[str]
    most_included_cards: List[TopCard]
    average_deck_price_usd: float
    average_cmc: float
    tournament_win_rate: Optional[float] = None
    tournament_sample_size: int = 0


class CommunitySignals(BaseModel):
    """Stream 3: Cultural signal."""

    reddit_thread_count: int
    named_archetypes: List[str]
    primer_articles_referenced: List[str]
    emerging_strategies: List[str] = Field(default_factory=list)


class WinCondition(BaseModel):
    """A deck win condition."""

    description: str
    key_cards: List[str]
    reliability: Literal["primary", "secondary", "backup"]


class BuildPath(BaseModel):
    """A possible build direction for a commander."""

    name: str
    description: str
    consensus_status: Literal["mainstream", "emerging", "underexplored"]
    key_card_categories: List[str]


class AntiSynergy(BaseModel):
    """Cards or strategies that work against the commander."""

    description: str
    cards_to_avoid: List[str]
    reasoning: str


class ValueInversion(BaseModel):
    """A heuristic the commander inverts."""

    normal_heuristic: str
    inverted_value: str
    desired_characteristics: List[str]
    evaluation_guidance: str


class EngineDependency(BaseModel):
    """A causal dependency in the commander's strategy.

    Captures the distinction between the commander's core engine
    (what the deck must accumulate/activate) and the engine's outputs
    (effects produced as consequences of the engine running).
    """

    engine: str  # e.g. "Auras you control"
    engine_card_traits: List[str]  # e.g. ["aura", "enchant creature", "bestow"]
    dependent_outputs: List[str]  # e.g. ["life drain", "creature lockdown"]
    false_synergy_warning: str  # e.g. "Lifegain cards that don't interact with Auras..."


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
    win_conditions: List[WinCondition]
    build_paths: List[BuildPath]
    synergy_priorities: Dict[str, List[str]]
    anti_synergies: List[AntiSynergy]
    strategic_constraints: StrategicConstraints
    power_indicators: PowerIndicators
    value_inversions: List[ValueInversion] = Field(default_factory=list)
    engine_dependencies: List[EngineDependency] = Field(default_factory=list)


class UserIntent(BaseModel):
    """Optional user-provided build direction."""

    provided: bool
    description: Optional[str] = None
    divergence_from_consensus: Optional[str] = None


class EvidenceFreshness(BaseModel):
    """Timestamps of evidence source data."""

    edhrec_last_updated: Optional[datetime] = None
    topdeck_last_updated: Optional[datetime] = None
    reddit_last_searched: Optional[datetime] = None


class ProfileSources(BaseModel):
    """Sources used to generate the profile."""

    rules_chunks_referenced: List[str] = Field(default_factory=list)
    articles_referenced: List[str] = Field(default_factory=list)
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
