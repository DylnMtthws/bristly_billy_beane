# schema.md

<instructions>
This document defines all data schemas used in the Sabermetrics application:
- SQLite database schema (DDL)
- Pydantic data models (Python)
- Configuration file schemas (YAML)
- LLM input/output schemas (JSON)

Read this before:
- Querying any database table
- Defining new data structures
- Modifying existing tables (use SKILL-007: Schema Migration)

All schemas use additive evolution — new fields with defaults, new tables independently. Destructive changes (DROP COLUMN, RENAME) are forbidden.
</instructions>

---

## 1. SQLite Database Schema

<context name="sqlite_schema">

### 1.1 Cards and Pricing

```sql
-- Core card data, primary source: Scryfall
CREATE TABLE cards (
    id TEXT PRIMARY KEY,                  -- Scryfall UUID
    oracle_id TEXT NOT NULL,              -- Identity across reprints
    name TEXT NOT NULL,
    mana_cost TEXT,
    cmc REAL,
    type_line TEXT,
    oracle_text TEXT,
    color_identity TEXT,                  -- JSON array: ["W","U"]
    keywords TEXT,                        -- JSON array
    is_legal_commander BOOLEAN,
    is_legal_in_99 BOOLEAN,
    set_code TEXT,
    rarity TEXT,
    image_uri TEXT,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_cards_name ON cards(name);
CREATE INDEX idx_cards_oracle_id ON cards(oracle_id);
CREATE INDEX idx_cards_legal_commander ON cards(is_legal_commander);

-- Time-series pricing data
CREATE TABLE card_prices (
    card_id TEXT,
    price_usd REAL,
    price_usd_foil REAL,
    snapshot_date DATE,
    source TEXT DEFAULT 'scryfall',
    PRIMARY KEY (card_id, snapshot_date),
    FOREIGN KEY (card_id) REFERENCES cards(id)
);
CREATE INDEX idx_prices_card_date ON card_prices(card_id, snapshot_date DESC);
```

### 1.2 Decks

```sql
-- Decklists from various sources
CREATE TABLE decks (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,                 -- 'moxfield', 'archidekt', 'deckstats', 'topdeck'
    source_id TEXT NOT NULL,              -- ID on the source platform
    commander_id TEXT NOT NULL,
    deck_name TEXT,
    creator TEXT,
    estimated_price_usd REAL,
    power_tier INTEGER,                   -- Inferred 1-5 bracket
    raw_data TEXT,                        -- JSON dump from source
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id),
    FOREIGN KEY (commander_id) REFERENCES cards(id)
);
CREATE INDEX idx_decks_commander ON decks(commander_id);
CREATE INDEX idx_decks_source ON decks(source);

-- Cards in decks (many-to-many)
CREATE TABLE deck_cards (
    deck_id TEXT,
    card_id TEXT,
    quantity INTEGER DEFAULT 1,
    is_commander BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (deck_id, card_id),
    FOREIGN KEY (deck_id) REFERENCES decks(id),
    FOREIGN KEY (card_id) REFERENCES cards(id)
);
CREATE INDEX idx_deck_cards_card ON deck_cards(card_id);
```

### 1.3 Tournament Results

```sql
-- Tournament results from TopDeck.gg
CREATE TABLE tournament_results (
    id TEXT PRIMARY KEY,
    tournament_id TEXT,
    player_name TEXT,
    deck_id TEXT,                         -- FK to decks if matched
    commander_id TEXT,
    standing INTEGER,
    win_rate REAL,
    games_played INTEGER,
    games_won INTEGER,
    tournament_date DATE,
    FOREIGN KEY (deck_id) REFERENCES decks(id),
    FOREIGN KEY (commander_id) REFERENCES cards(id)
);
CREATE INDEX idx_tourney_commander ON tournament_results(commander_id);
CREATE INDEX idx_tourney_date ON tournament_results(tournament_date DESC);
```

### 1.4 EDHREC Data

```sql
-- EDHREC data per commander
CREATE TABLE edhrec_commander_data (
    commander_id TEXT PRIMARY KEY,
    themes TEXT,                          -- JSON array of theme tags
    salt_score REAL,
    deck_count INTEGER,
    top_cards TEXT,                       -- JSON: [{card_id, inclusion_pct}]
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (commander_id) REFERENCES cards(id)
);
```

### 1.5 Derived Analytics

```sql
-- Card co-occurrence (sparse matrix)
CREATE TABLE card_cooccurrence (
    card_a_id TEXT,
    card_b_id TEXT,
    commander_id TEXT,                    -- Co-occurrence is per-commander
    cooccurrence_count INTEGER,
    cooccurrence_rate REAL,
    PRIMARY KEY (card_a_id, card_b_id, commander_id),
    FOREIGN KEY (card_a_id) REFERENCES cards(id),
    FOREIGN KEY (card_b_id) REFERENCES cards(id),
    FOREIGN KEY (commander_id) REFERENCES cards(id)
);
CREATE INDEX idx_cooccurrence_lookup ON card_cooccurrence(commander_id, card_a_id);

-- Card Win Equity (computed from tournament data)
CREATE TABLE card_win_equity (
    card_id TEXT,
    commander_id TEXT,
    win_rate_when_present REAL,
    win_rate_when_absent REAL,
    cwe_score REAL,                       -- Difference (the lift)
    sample_size INTEGER,
    confidence REAL,                      -- Wilson score interval
    last_computed TIMESTAMP,
    PRIMARY KEY (card_id, commander_id),
    FOREIGN KEY (card_id) REFERENCES cards(id),
    FOREIGN KEY (commander_id) REFERENCES cards(id)
);
```

### 1.6 Profiles and Generated Decks

```sql
-- Cached commander profiles (LLM-generated)
CREATE TABLE commander_profiles (
    commander_id TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL,           -- Full structure (see Pydantic model)
    user_intent TEXT,                     -- Optional override
    user_intent_hash TEXT,                -- For cache key generation
    set_version TEXT NOT NULL,            -- Set code at generation time
    evidence_sources TEXT,                -- JSON: which sources contributed
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_validated_at TIMESTAMP,
    is_stale BOOLEAN DEFAULT FALSE,
    schema_version TEXT DEFAULT '1.0',
    FOREIGN KEY (commander_id) REFERENCES cards(id)
);
CREATE INDEX idx_profiles_stale ON commander_profiles(is_stale);

-- Generated decks (history)
CREATE TABLE generated_decks (
    id TEXT PRIMARY KEY,
    commander_id TEXT NOT NULL,
    profile_id TEXT,                      -- FK to commander_profiles.commander_id
    budget_usd REAL,
    power_target INTEGER,
    strategy TEXT,
    cards_json TEXT,                      -- JSON array with metadata
    rationale TEXT,
    cvar_score REAL,
    estimated_bracket INTEGER,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (commander_id) REFERENCES cards(id)
);
```

### 1.7 Reference Layer (RAG)

```sql
-- Reference chunks for grounding LLM reasoning
CREATE TABLE reference_chunks (
    id TEXT PRIMARY KEY,
    document TEXT NOT NULL,               -- 'comprehensive_rules', 'commander_rules', etc.
    section TEXT,                         -- e.g., 'CR 702.2'
    tier INTEGER NOT NULL,                -- 1-4 per design.md
    content TEXT NOT NULL,
    embedding BLOB,                       -- numpy array as bytes
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_chunks_document ON reference_chunks(document);
CREATE INDEX idx_chunks_tier ON reference_chunks(tier);

-- Card rulings from magicthegathering.io
CREATE TABLE card_rulings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_oracle_id TEXT NOT NULL,         -- Joined to cards.oracle_id (persists across reprints)
    ruling_date DATE,
    ruling_text TEXT NOT NULL,
    source TEXT DEFAULT 'mtgapi',
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (card_oracle_id) REFERENCES cards(oracle_id)
);
CREATE INDEX idx_rulings_oracle ON card_rulings(card_oracle_id);
CREATE INDEX idx_rulings_date ON card_rulings(ruling_date DESC);
```

### 1.8 Combos

```sql
-- Combo data from Commander Spellbook
CREATE TABLE combos (
    id TEXT PRIMARY KEY,
    cards TEXT NOT NULL,                  -- JSON array of card IDs
    color_identity TEXT,                  -- JSON array
    description TEXT,
    result TEXT,                          -- "infinite mana", "win the game", etc.
    prerequisites TEXT,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_combos_color ON combos(color_identity);
```

### 1.9 Operational Tables

```sql
-- Schema migration tracking
CREATE TABLE _schema_version (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

-- LLM cost tracking (written by src/reasoning/client.py)
CREATE TABLE cost_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    call_type TEXT NOT NULL,              -- 'profile_synthesis', 'card_fit', etc.
    model TEXT NOT NULL,
    input_tokens INTEGER,
    cached_input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    request_id TEXT,                      -- For correlation
    metadata TEXT                         -- JSON for additional context
);
CREATE INDEX idx_cost_timestamp ON cost_log(timestamp DESC);
CREATE INDEX idx_cost_call_type ON cost_log(call_type);

-- Source health monitoring
CREATE TABLE source_health (
    source TEXT PRIMARY KEY,
    last_successful_sync TIMESTAMP,
    last_failed_sync TIMESTAMP,
    last_error TEXT,
    consecutive_failures INTEGER DEFAULT 0
);
```

</context>

---

## 2. Pydantic Data Models

<context name="pydantic_models">

### 2.1 Core Card Model

```python
# src/models/card.py

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class Card(BaseModel):
    id: str
    oracle_id: str
    name: str
    mana_cost: Optional[str] = None
    cmc: float
    type_line: str
    oracle_text: Optional[str] = None
    color_identity: List[str]
    keywords: List[str] = Field(default_factory=list)
    is_legal_commander: bool
    is_legal_in_99: bool
    set_code: str
    rarity: str
    image_uri: Optional[str] = None
    last_updated: datetime

    # Derived/joined fields (populated when needed)
    current_price_usd: Optional[float] = None
    rulings: List["CardRuling"] = Field(default_factory=list)
    edhrec_inclusion_pct: Optional[float] = None

class CardRuling(BaseModel):
    ruling_date: Optional[datetime]
    ruling_text: str
    source: str = "mtgapi"
```

### 2.2 Commander Intent Profile

```python
# src/models/profile.py

from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime

class CardAnalysis(BaseModel):
    """Stream 1: Card-derived intent"""
    mana_cost: str
    color_identity: List[str]
    core_mechanic: str
    triggered_abilities: List[str]
    activated_abilities: List[str]
    static_abilities: List[str]
    evasion_or_protection: Optional[str] = None

class TopCard(BaseModel):
    card_name: str
    inclusion_pct: float

class BehavioralSignals(BaseModel):
    """Stream 2: Behavioral aggregate"""
    total_decks_tracked: int
    edhrec_themes: List[str]
    most_included_cards: List[TopCard]
    average_deck_price_usd: float
    average_cmc: float
    tournament_win_rate: Optional[float] = None
    tournament_sample_size: int = 0

class CommunitySignals(BaseModel):
    """Stream 3: Cultural signal"""
    reddit_thread_count: int
    named_archetypes: List[str]
    primer_articles_referenced: List[str]
    emerging_strategies: List[str] = Field(default_factory=list)

class WinCondition(BaseModel):
    description: str
    key_cards: List[str]
    reliability: Literal["primary", "secondary", "backup"]

class BuildPath(BaseModel):
    name: str
    description: str
    consensus_status: Literal["mainstream", "emerging", "underexplored"]
    key_card_categories: List[str]

class AntiSynergy(BaseModel):
    description: str
    cards_to_avoid: List[str]
    reasoning: str

class StrategicConstraints(BaseModel):
    mana_base_requirements: str
    interaction_density: Literal["high", "medium", "low"]
    speed_tier: Literal["fast", "midrange", "slow"]

class PowerIndicators(BaseModel):
    estimated_ceiling_bracket: int = Field(ge=1, le=5)
    estimated_floor_bracket: int = Field(ge=1, le=5)
    notes: str

class StrategicProfile(BaseModel):
    primary_archetype: str
    game_plan_summary: str
    win_conditions: List[WinCondition]
    build_paths: List[BuildPath]
    synergy_priorities: dict  # {high: [...], medium: [...], low: [...]}
    anti_synergies: List[AntiSynergy]
    strategic_constraints: StrategicConstraints
    power_indicators: PowerIndicators

class UserIntent(BaseModel):
    provided: bool
    description: Optional[str] = None
    divergence_from_consensus: Optional[str] = None

class EvidenceFreshness(BaseModel):
    edhrec_last_updated: Optional[datetime] = None
    topdeck_last_updated: Optional[datetime] = None
    reddit_last_searched: Optional[datetime] = None

class ProfileSources(BaseModel):
    rules_chunks_referenced: List[str] = Field(default_factory=list)
    articles_referenced: List[str] = Field(default_factory=list)
    evidence_freshness: EvidenceFreshness

class CommanderProfile(BaseModel):
    """Top-level commander profile structure"""
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
```

### 2.3 Generated Deck Model

```python
# src/models/deck.py

from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime

class CVARWeights(BaseModel):
    synergy: float = 0.35
    replacement_value: float = 0.25
    mana_efficiency: float = 0.25
    price_efficiency: float = 0.15

class CardSubScores(BaseModel):
    synergy: float
    mana_efficiency: float
    replacement_value: float
    price_efficiency: float
    card_win_equity: Optional[float] = None

class LLMFit(BaseModel):
    score: int = Field(ge=1, le=10)
    reasoning: str

class DeckCard(BaseModel):
    card: Card
    slot_role: Literal["ramp", "draw", "removal", "wincon", "utility", "land", "other"]
    cvar_score: float
    sub_scores: CardSubScores
    llm_fit: LLMFit
    alternatives: List[str]  # card_ids

class DeckParameters(BaseModel):
    budget_usd: float
    power_target: int
    strategy: Optional[str] = None
    weights: CVARWeights

class ComponentCounts(BaseModel):
    ramp: int
    draw: int
    removal: int
    board_wipes: int
    tutors: int
    win_conditions: int

class DeckComposition(BaseModel):
    total_price_usd: float
    average_cmc: float
    color_distribution: Dict[str, int]
    type_distribution: Dict[str, int]
    mana_curve: List[int]                  # Index = CMC, value = count
    component_counts: ComponentCounts
    game_changers_present: List[str]       # card_ids
    detected_combos: List[str]             # combo_ids

class DeckClassification(BaseModel):
    estimated_bracket: int = Field(ge=1, le=5)
    bracket_reasoning: str

class DeckNarrative(BaseModel):
    game_plan: str
    key_synergies: List[str]
    weaknesses: List[str]
    suggested_play_pattern: str

class GenerationMeta(BaseModel):
    generation_time_seconds: float
    llm_cost_usd: float
    source_profile_id: str

class GeneratedDeck(BaseModel):
    id: str
    commander: Card
    generated_at: datetime
    parameters: DeckParameters
    cards: List[DeckCard]                  # Should be exactly 99
    composition: DeckComposition
    classification: DeckClassification
    narrative: DeckNarrative
    meta: GenerationMeta
```

### 2.4 Evidence Package Model

```python
# src/models/evidence.py

from pydantic import BaseModel
from typing import List, Optional
from .card import Card, CardRuling

class RedditThread(BaseModel):
    title: str
    url: str
    upvotes: int
    created_utc: int
    summary: Optional[str] = None

class PrimerArticle(BaseModel):
    title: str
    url: str
    source: str
    summary: Optional[str] = None

class ReferenceChunk(BaseModel):
    id: str
    document: str
    section: Optional[str]
    tier: int
    content: str

class EvidencePackage(BaseModel):
    """Composed by EvidenceAggregator for profile generation"""
    commander: Card
    rulings: List[CardRuling]
    edhrec_data: Optional[dict] = None
    tournament_data: Optional[dict] = None
    reddit_threads: List[RedditThread]
    primer_articles: List[PrimerArticle]
    reference_chunks: List[ReferenceChunk]
    user_intent: Optional[str] = None
```

### 2.5 LLM Response Models

```python
# src/models/llm_responses.py

from pydantic import BaseModel
from typing import Literal

class CardFitResponse(BaseModel):
    """Output from per-card fit scoring (Haiku)"""
    fit_score: int = Field(ge=1, le=10)
    reasoning: str
    slot_role: Literal["ramp", "draw", "removal", "wincon", "utility", "land", "other"]

class RelevanceScreenResponse(BaseModel):
    """Output from set-release relevance screening (Haiku)"""
    card_name: str
    affects_strategy: bool
    reason: str

class DeckSynthesisResponse(BaseModel):
    """Output from deck-level synthesis (Sonnet)"""
    game_plan: str
    key_synergies: List[str]
    weaknesses: List[str]
    suggested_play_pattern: str
```

</context>

---

## 3. Configuration Schemas

<context name="config_schemas">

### 3.1 settings.yaml Schema

```yaml
# config/settings.yaml structure

user:
  default_budget_usd: float                # Default: 200
  default_power_target: int                # 1-5; Default: 3
  default_weights:
    synergy: float                         # Sum to 1.0
    replacement_value: float
    mana_efficiency: float
    price_efficiency: float

llm:
  profile_model: string                    # 'claude-sonnet-4-6'
  fit_model: string                        # 'claude-haiku-4-5'
  synthesis_model: string                  # 'claude-sonnet-4-6'
  refresh_model: string                    # 'claude-haiku-4-5'
  max_candidates_for_llm_fit: int          # Default: 50
  prompt_caching: bool                     # Default: true
  monthly_cost_ceiling_usd: float          # Default: 5.0

embeddings:
  model: string                            # 'sentence-transformers/all-MiniLM-L6-v2'
  device: string                           # 'cpu' or 'cuda'
  cache_dir: string                        # './data/embedding_cache'

pipeline:
  hard_filter_target: int                  # Default: 3000
  embedding_filter_target: int             # Default: 200
  structural_filter_target: int            # Default: 50
  candidates_per_slot: int                 # Default: 5

refresh:
  scryfall_daily: bool
  topdeck_weekly: bool
  decklist_sources_weekly: bool
  edhrec_weekly: bool
  mtgapi_rulings_monthly: bool
  set_refresh_quarterly: bool

output:
  deck_format: string                      # 'json', 'moxfield', 'archidekt', 'text'
  include_alternatives: bool
  alternatives_per_slot: int
```

### 3.2 synergy_rules.yaml Schema

```yaml
# config/synergy_rules.yaml structure

rules:
  - id: string                             # snake_case identifier
    trigger:
      text_contains: [string]              # Optional: oracle text patterns
      type_includes: [string]              # Optional: type line includes
      keywords: [string]                   # Optional: keyword abilities
    payoff:
      text_contains: [string]
      type_includes: [string]
      cmc_range: [int, int]                # Optional: [min, max]
    strength: float                        # 0.0-1.0
    description: string                    # Human-readable
```

### 3.3 game_changers.yaml Schema

```yaml
# config/game_changers.yaml structure

# WotC official game changer list (per bracket framework)
# Source: https://magic.wizards.com/en/news/announcements/commander-format-update
last_updated: date                         # YYYY-MM-DD

game_changers:
  - card_name: string
    bracket_threshold: int                 # Bracket level where this card becomes a "game changer"
    rationale: string                      # Optional: why on the list
```

### 3.4 known_combos.yaml Schema

```yaml
# config/known_combos.yaml structure
# Augmented at runtime by Commander Spellbook API ingestion

combos:
  - id: string
    cards: [string]                        # Card names
    color_identity: [string]               # ["W","U","B"]
    description: string
    result: string                         # 'infinite mana', 'win the game', etc.
    prerequisites: string
```

</context>

---

## 4. LLM Input/Output JSON Schemas

<context name="llm_schemas">

### 4.1 Profile Synthesis Output

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "CommanderProfile",
  "type": "object",
  "required": [
    "commander_id",
    "commander_name",
    "card_analysis",
    "strategic_profile",
    "sources"
  ],
  "properties": {
    "commander_id": {"type": "string"},
    "commander_name": {"type": "string"},
    "card_analysis": {
      "type": "object",
      "properties": {
        "core_mechanic": {"type": "string"},
        "triggered_abilities": {"type": "array", "items": {"type": "string"}},
        "activated_abilities": {"type": "array", "items": {"type": "string"}},
        "static_abilities": {"type": "array", "items": {"type": "string"}}
      }
    },
    "strategic_profile": {
      "type": "object",
      "required": [
        "primary_archetype",
        "game_plan_summary",
        "win_conditions",
        "build_paths",
        "anti_synergies",
        "power_indicators"
      ],
      "properties": {
        "primary_archetype": {"type": "string"},
        "game_plan_summary": {"type": "string"},
        "win_conditions": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "description": {"type": "string"},
              "key_cards": {"type": "array", "items": {"type": "string"}},
              "reliability": {"enum": ["primary", "secondary", "backup"]}
            }
          }
        },
        "build_paths": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "name": {"type": "string"},
              "description": {"type": "string"},
              "consensus_status": {"enum": ["mainstream", "emerging", "underexplored"]}
            }
          }
        },
        "anti_synergies": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "description": {"type": "string"},
              "cards_to_avoid": {"type": "array", "items": {"type": "string"}},
              "reasoning": {"type": "string"}
            }
          }
        },
        "power_indicators": {
          "type": "object",
          "properties": {
            "estimated_ceiling_bracket": {"type": "integer", "minimum": 1, "maximum": 5},
            "estimated_floor_bracket": {"type": "integer", "minimum": 1, "maximum": 5}
          }
        }
      }
    },
    "sources": {
      "type": "object",
      "properties": {
        "rules_chunks_referenced": {"type": "array", "items": {"type": "string"}},
        "articles_referenced": {"type": "array", "items": {"type": "string"}}
      }
    }
  }
}
```

### 4.2 Card Fit Scoring Output

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "CardFitResponse",
  "type": "object",
  "required": ["fit_score", "reasoning", "slot_role"],
  "properties": {
    "fit_score": {"type": "integer", "minimum": 1, "maximum": 10},
    "reasoning": {"type": "string", "maxLength": 200},
    "slot_role": {"enum": ["ramp", "draw", "removal", "wincon", "utility", "land", "other"]}
  }
}
```

### 4.3 Deck Synthesis Output

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "DeckSynthesisResponse",
  "type": "object",
  "required": ["game_plan", "key_synergies", "weaknesses", "suggested_play_pattern"],
  "properties": {
    "game_plan": {"type": "string", "minLength": 100, "maxLength": 800},
    "key_synergies": {
      "type": "array",
      "minItems": 3,
      "maxItems": 7,
      "items": {"type": "string"}
    },
    "weaknesses": {
      "type": "array",
      "minItems": 1,
      "maxItems": 5,
      "items": {"type": "string"}
    },
    "suggested_play_pattern": {"type": "string", "maxLength": 400}
  }
}
```

### 4.4 Relevance Screen Output

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "RelevanceScreenResponse",
  "type": "array",
  "items": {
    "type": "object",
    "required": ["card_name", "affects_strategy", "reason"],
    "properties": {
      "card_name": {"type": "string"},
      "affects_strategy": {"type": "boolean"},
      "reason": {"type": "string", "maxLength": 200}
    }
  }
}
```

</context>

---

## 5. Schema Change Log

<context name="change_log">

### Version 1.0 (Initial)
- Established core tables: cards, decks, deck_cards, tournament_results
- Established analytics tables: cooccurrence, card_win_equity
- Established profile tables: commander_profiles, generated_decks
- Established reference tables: reference_chunks, card_rulings
- Established operational tables: cost_log, source_health, _schema_version

### Future Versions
- Document each migration here with: version, date, description, migration script reference

</context>
