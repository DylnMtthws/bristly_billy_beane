# api_contracts.md

<instructions>
This document defines:
1. Module-to-module interfaces (internal contracts)
2. External API contracts (third-party services we depend on)

Read this before:
- Calling any module from another module
- Adding new ingestion sources
- Modifying public function signatures
- Integrating with external APIs

When changing a contract, update this document FIRST, then implement. Breaking changes to internal contracts require updating all consumers.
</instructions>

---

## 1. Internal Module Contracts

<context name="internal_contracts">

### 1.1 Ingestion Layer Interface

All ingestion sources implement the same Protocol:

```python
# src/ingestion/base.py

from typing import Protocol
from datetime import datetime
from pydantic import BaseModel

class SyncResult(BaseModel):
    source_name: str
    started_at: datetime
    completed_at: datetime
    items_ingested: int
    items_updated: int
    items_failed: int
    errors: list[str]
    success: bool

class IngestionSource(Protocol):
    """Contract for all data ingestion adapters"""

    name: str  # Unique source identifier

    def is_available(self) -> bool:
        """Check if source is reachable. Used for health monitoring."""
        ...

    def last_updated(self) -> datetime | None:
        """When did this source last successfully sync? None if never."""
        ...

    def sync(self, full: bool = False) -> SyncResult:
        """
        Pull data from source into local DB.

        Args:
            full: If True, full refresh; if False, incremental (delta only).

        Returns:
            SyncResult with metrics and any errors.

        Raises:
            FatalError: Only for unrecoverable issues (DB corruption, etc.)

        Notes:
            - Handles its own rate limiting
            - Handles its own retries for recoverable errors
            - Marks degraded state in source_health table on partial failure
            - Never raises on transient errors; logs and returns success=False
        """
        ...
```

### 1.2 Anthropic Client Contract

```python
# src/reasoning/client.py

from typing import Optional
from pydantic import BaseModel

class CallResult(BaseModel):
    content: str                              # Raw response text
    model: str
    input_tokens: int
    cached_input_tokens: int                  # Hit count for caching
    output_tokens: int
    cost_usd: float
    request_id: str

class AnthropicClient:
    """Singleton wrapper for Anthropic API. All LLM calls MUST go through this."""

    def call_with_cache(
        self,
        model: str,                           # 'claude-haiku-4-5' or 'claude-sonnet-4-6'
        system: str,
        messages: list[dict],
        cache_breakpoints: list[int],         # Indices of messages to mark cacheable
        max_tokens: int = 4000,
        temperature: float = 0.0
    ) -> CallResult:
        """
        Make an Anthropic API call with prompt caching.

        Behavior:
            - Automatically logs to cost_log table
            - Enforces monthly cost ceiling (raises FatalError if exceeded)
            - Retries transient failures with exponential backoff (max 3)
            - Validates model name against ADR-011 allowed models

        Args:
            model: Must be one of the configured models in CLAUDE.md
            system: System prompt (typically cached in entirety)
            messages: List of messages; cache_breakpoints index into this
            cache_breakpoints: Where to insert cache_control markers
            max_tokens: Output token limit
            temperature: 0.0 for deterministic, 0.0-1.0 for creative

        Returns:
            CallResult with full metadata

        Raises:
            RecoverableError: Transient API failure (caller may retry)
            FatalError: Cost ceiling exceeded, invalid config
        """
        ...

    def estimate_cost(
        self,
        model: str,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int
    ) -> float:
        """Compute cost in USD given token counts. Source of truth for pricing."""
        ...

    def get_monthly_spend(self) -> float:
        """Query cost_log for spend in last 30 days."""
        ...
```

### 1.3 Reference Layer Contract

```python
# src/reference_layer/retriever.py

from pydantic import BaseModel

class ReferenceQuery(BaseModel):
    query_text: str
    tier_filter: list[int] | None = None      # Filter to specific tiers, None = all
    document_filter: list[str] | None = None  # Filter to specific documents
    top_k: int = 10

class RetrievedChunk(BaseModel):
    id: str
    document: str
    section: str | None
    tier: int
    content: str
    similarity_score: float                   # Cosine similarity 0.0-1.0

class ReferenceRetriever:
    """Query reference chunks via cosine similarity."""

    def retrieve(self, query: ReferenceQuery) -> list[RetrievedChunk]:
        """
        Retrieve top-K relevant reference chunks.

        Args:
            query: Query specification with filters

        Returns:
            Up to top_k chunks, sorted by similarity descending.
            Empty list if no chunks meet relevance threshold.

        Notes:
            - Embeddings cached in memory for 1-hour TTL
            - Falls back to text search if embedding model unavailable
        """
        ...
```

### 1.4 Profile Manager Contract

```python
# src/reasoning/profiler.py

from pydantic import BaseModel

class ProfileRequest(BaseModel):
    commander_id: str
    user_intent: str | None = None
    force_refresh: bool = False

class ProfileResult(BaseModel):
    profile: CommanderProfile
    cache_hit: bool
    generation_cost_usd: float
    generation_time_seconds: float

def generate_profile(request: ProfileRequest) -> ProfileResult:
    """
    Get or generate a commander profile.

    Workflow:
        1. Check cache (commander_id + user_intent_hash + set_version)
        2. If cache hit and not force_refresh: return cached
        3. Aggregate evidence (calls EvidenceAggregator)
        4. Retrieve reference chunks (calls ReferenceRetriever)
        5. Call Sonnet via AnthropicClient
        6. Validate response against CommanderProfile schema
        7. Persist to commander_profiles table
        8. Return result with cache_hit flag

    Returns:
        ProfileResult always; profile is always valid

    Raises:
        DegradableError: LLM unavailable, returns last cached profile if any
        FatalError: Commander not found, schema validation persistently fails
    """
    ...
```

### 1.5 Deck Builder Contract

```python
# src/pipeline/deck_builder.py

from pydantic import BaseModel

class DeckBuildRequest(BaseModel):
    commander_id: str
    budget_usd: float
    power_target: int = Field(ge=1, le=5)
    strategy: str | None = None
    weights: CVARWeights | None = None
    user_intent: str | None = None

class DeckBuildResult(BaseModel):
    deck: GeneratedDeck
    profile_was_generated: bool
    total_cost_usd: float
    total_time_seconds: float
    pipeline_metrics: dict                    # Per-stage timing

def build_deck(request: DeckBuildRequest) -> DeckBuildResult:
    """
    End-to-end deck generation pipeline.

    Workflow: See SKILLS.md SKILL-005 for full 10-step workflow.

    Returns:
        DeckBuildResult with full deck, generation metrics

    Raises:
        DegradableError: LLM unavailable; returns structural-only deck
        FatalError: Commander not found, budget too low for legal deck
    """
    ...
```

### 1.6 Analytics Layer Contracts

All scoring functions follow a common shape:

```python
# Pattern for all src/analytics/*.py modules

from pydantic import BaseModel

class ScoringContext(BaseModel):
    """Shared context passed to all scoring functions"""
    commander: Card
    profile: CommanderProfile | None = None
    weights: CVARWeights | None = None
    other_cards_in_deck: list[Card] = Field(default_factory=list)

# CVAR module
def compute_cvar(card: Card, context: ScoringContext) -> CVARResult:
    """
    Compute CVAR composite score for a card given commander context.

    Formula: w1·synergy + w2·mana_eff + w3·replacement_val − w4·price_penalty

    Returns:
        CVARResult with composite score and sub-scores

    Notes:
        - Pure function: same inputs → same outputs
        - <100ms execution time
        - No external dependencies
    """
    ...

# Card Win Equity
def compute_cwe(card_id: str, commander_id: str) -> CWEResult:
    """
    Compute Card Win Equity from cached tournament_results.

    Formula:
        wr_with = avg(win_rate of decks with card)
        wr_without = avg(win_rate of decks without card, same commander)
        cwe = wr_with - wr_without

    Returns:
        CWEResult with score and Wilson confidence interval

    Notes:
        - Returns None if sample_size < threshold (default 5)
        - Cached in card_win_equity table; recomputed weekly
    """
    ...

# Brackets
def classify_deck(deck: list[Card], game_changers: list[str]) -> BracketResult:
    """
    Classify deck per WotC's 5-tier bracket framework.

    Returns:
        BracketResult with bracket (1-5) and reasoning

    Inputs from config/game_changers.yaml
    """
    ...
```

</context>

---

## 2. External API Contracts

<context name="external_apis">

### 2.1 Scryfall API (PRIMARY card source)

```yaml
service: Scryfall
base_url: https://api.scryfall.com
auth: none
rate_limit:
  notes: "Polite usage; recommended 50-100ms between requests"
  enforcement: "Self-imposed; 1 req/sec for safety"

endpoints:
  bulk_data_list:
    method: GET
    path: /bulk-data
    purpose: "List available bulk download files"
    response_shape:
      data:
        - object: bulk_data
          type: string                       # 'default_cards', 'all_cards', 'rulings', etc.
          updated_at: datetime
          download_uri: string
          size: int

  bulk_data_download:
    method: GET
    path: "[from download_uri]"
    purpose: "Download full bulk dataset"
    response: "JSON array of card objects (~150MB for default_cards)"
    use_for: "Daily card refresh"

  card_search:
    method: GET
    path: /cards/search
    params:
      q: "Scryfall query syntax (e.g., 'o:flying t:creature')"
    purpose: "Targeted searches; rarely used (bulk preferred)"

  card_by_id:
    method: GET
    path: /cards/{id}
    purpose: "Fetch single card by Scryfall UUID"
    use_when: "Need fresh data for specific card"

card_object_shape:
  id: string                                 # Scryfall UUID
  oracle_id: string
  name: string
  mana_cost: string
  cmc: number
  type_line: string
  oracle_text: string
  color_identity: array[string]
  keywords: array[string]
  set: string                                # Set code
  rarity: string
  prices:
    usd: string                              # Note: string in API, parse to float
    usd_foil: string
    eur: string
  legalities:
    commander: string                        # 'legal', 'banned', 'not_legal', 'restricted'
    standard: string
    # ... other formats
  image_uris:
    normal: string
    small: string
```

### 2.2 magicthegathering.io API (SUPPLEMENTARY: rulings only)

```yaml
service: magicthegathering.io
base_url: https://api.magicthegathering.io/v1
sdk: mtgsdk (Python)
auth: none
rate_limit:
  cap: "5000 requests/hour"
  enforcement: "Self-imposed 1 req/sec"
  headers:
    - Ratelimit-Limit
    - Ratelimit-Remaining

endpoints:
  cards_list:
    method: GET
    path: /cards
    pagination:
      page_size: 100  # Max
      param: page
    use_for: "Ingest rulings (NOT primary card data per ADR-013)"

  card_by_id:
    method: GET
    path: /cards/{id}
    purpose: "Single card with full rulings array"

card_object_shape_relevant_fields:
  id: string                                 # SHA1 hash
  name: string
  multiverseid: int                          # Gatherer link
  rulings:
    - date: string                           # YYYY-MM-DD
      text: string

ingestion_pattern:
  iterate_by: oracle_id (deduplicates reprints)
  store_in: card_rulings table
  join_via: cards.oracle_id (NOT cards.id; rulings persist across reprints)
```

### 2.3 TopDeck.gg API (Tournament data)

```yaml
service: TopDeck.gg
base_url: https://topdeck.gg/api/v2
auth:
  type: Bearer token
  header: "Authorization: Bearer {API_KEY}"
  obtain_via: "Free registration at topdeck.gg"
rate_limit:
  cap: "100 requests/minute"

endpoints:
  tournaments_list:
    method: GET
    path: /tournaments
    params:
      format: "EDH"                          # Filter to Commander
      since: datetime                        # ISO 8601
      limit: int
    response:
      tournaments:
        - id: string
          name: string
          date: datetime
          format: string
          standings: array

  tournament_detail:
    method: GET
    path: /tournaments/{id}
    response:
      id: string
      standings:
        - player: string
          deck:
            commander: string
            cards: array[string]
          standing: int
          win_rate: number
          games_played: int
          games_won: int
```

### 2.4 EDHREC (Scrape)

```yaml
service: EDHREC
base_url: https://edhrec.com
data_url: https://json.edhrec.com    # Underlying JSON endpoints
auth: none
rate_limit:
  cap: "Polite scraping; 1 req/sec self-imposed"
note: "No official API; scrape JSON behind page rendering"

endpoints:
  commander_data:
    pattern: "/pages/commanders/{commander-slug}.json"
    response_shape:
      container:
        json_dict:
          card_lists:
            - tag: string                    # 'topcards', 'creatures', etc.
              cardviews:
                - name: string
                  inclusion: int             # Number of decks
                  num_decks: int             # Total decks for commander
                  synergy: number            # EDHREC's own synergy score

  themes:
    pattern: "/pages/commanders/{commander-slug}/themes.json"

  salt_scores:
    pattern: "/pages/top/salt.json"
```

### 2.5 Moxfield, Archidekt, deckstats (via mtg-parser)

```yaml
service: mtg-parser library
purpose: "Unified interface to multiple decklist sites"
auth:
  moxfield: "Custom User-Agent required (contact moxfield support)"
  archidekt: none
  deckstats: none

usage:
  pattern: |
    import mtg_parser
    cards = mtg_parser.parse_deck(url, http_client)

returns:
  cards:
    - name: string
      quantity: int
      is_commander: bool
      tags: array[string]
```

### 2.6 Commander Spellbook (Combo data)

```yaml
service: Commander Spellbook
base_url: https://commanderspellbook.com
api_url: https://backend.commanderspellbook.com  # JSON API
auth: none

endpoints:
  combos_list:
    method: GET
    path: /api/combos
    pagination: cursor-based
    response:
      results:
        - id: string
          uses:                              # Cards required for combo
            - card:
                name: string
            quantity: int
          produces:                          # Effects produced
            - feature:
                name: string                 # 'infinite mana', etc.
          identity: string                   # 'WUBR' color identity
          description: string
          prerequisites: string
```

### 2.7 Reddit (Cultural signal)

```yaml
service: Reddit
base_url: https://www.reddit.com
auth: none (using public JSON endpoints)
rate_limit:
  enforcement: "Self-imposed 1 req/sec"
  required_header: "User-Agent: Sabermetrics/1.0 (personal research)"

endpoints:
  subreddit_search:
    method: GET
    path: /r/EDH/search.json
    params:
      q: string                              # Search query
      restrict_sr: 1                         # Limit to subreddit
      sort: "top"
      t: "year"                              # Time range
      limit: 25
    response:
      data:
        children:
          - data:
              title: string
              url: string
              ups: int
              created_utc: int
              selftext: string

usage_for_profile_generation:
  query_pattern: "{commander_name} strategy"
  top_k: 20
  filter: "ups > 50 (signal of community engagement)"
```

### 2.8 WotC Comprehensive Rules

```yaml
service: Wizards of the Coast
url: https://magic.wizards.com/en/rules
auth: none
refresh: quarterly (with set releases)

format: Plain text download (.txt)
size: ~1MB
processing:
  - "Chunk by section number (CR 100, CR 101, etc.)"
  - "Embed each chunk via sentence-transformers"
  - "Store in reference_chunks table with tier=1"
```

</context>

---

## 3. Error Contracts

<context name="error_contracts">

### 3.1 Exception Hierarchy

```python
# src/errors.py

class SabermetricsError(Exception):
    """Base for all custom exceptions"""

class RecoverableError(SabermetricsError):
    """Transient failure; caller may retry with backoff"""

class DegradableError(SabermetricsError):
    """Source unavailable but system can continue with degraded functionality"""

class FatalError(SabermetricsError):
    """Unrecoverable; requires user intervention"""

# Specific subclasses
class APIRateLimitError(RecoverableError): ...
class NetworkError(RecoverableError): ...
class SourceUnavailableError(DegradableError): ...
class LLMCostCeilingExceeded(FatalError): ...
class SchemaValidationError(FatalError): ...
class CommanderNotFoundError(FatalError): ...
```

### 3.2 Error Handling Conventions

```yaml
conventions:
  - "Always raise typed exceptions, never bare Exception"
  - "Log error context at the layer where it's caught"
  - "Recoverable: caller retries with exponential backoff"
  - "Degradable: log, mark in source_health, continue"
  - "Fatal: log, alert, halt operation, require explicit unlock"
  - "Never silently swallow errors"
  - "Errors crossing module boundaries must include context (which source, which commander, etc.)"
```

</context>

---

## 4. Public CLI Contract

<context name="cli_contract">

```yaml
cli_commands:
  - command: profile
    args:
      - commander_name: string
    options:
      --user-intent: string
      --force-refresh: flag
    output: "Pretty-printed profile JSON to stdout"
    example: 'python -m sabermetrics profile "Korvold, Fae-Cursed King"'

  - command: build
    args:
      - commander_name: string
    options:
      --budget: float (default from settings.yaml)
      --power: int (1-5)
      --strategy: string
      --user-intent: string
      --output-format: enum [json, text, moxfield, archidekt]
    output: "Generated deck in specified format"
    example: 'python -m sabermetrics build "Korvold" --budget 300 --power 4'

  - command: refresh-set
    args:
      - set_code: string
    output: "Refresh summary"
    example: 'python -m sabermetrics refresh-set FIN'

  - command: search-rules
    args:
      - query: string
    options:
      --top-k: int (default 5)
    output: "Relevant reference chunks"

  - command: serve
    options:
      --port: int (default 5000)
      --host: string (default 127.0.0.1; localhost-only enforced)
    behavior: "Start Flask UI server"

  - command: report
    options:
      --period: enum [day, week, month, year]
    output: "Cost and usage report"

  - command: health
    output: "Status of all data sources, last sync times, recent errors"

  - command: sync
    options:
      --source: string (specific source name; default all)
      --full: flag (full refresh vs incremental)
    output: "Sync results"
```

</context>
