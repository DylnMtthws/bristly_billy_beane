# build_plan.md

<instructions>
This document defines the ordered build sequence for the Sabermetrics application. Each phase has explicit deliverables, acceptance criteria, and dependencies.

When you start a new session:
1. Check the "current_phase" status in CLAUDE.md
2. Read the corresponding phase here
3. Verify previous phase acceptance criteria pass
4. Begin work on the current phase

Update CLAUDE.md `active_phase.status` when phases complete.

Phases must be completed in order. Acceptance criteria are gates — do not advance until they pass.
</instructions>

---

## Phase Index

```yaml
phases:
  - id: P1
    name: Foundation
    status: pending
    estimated_effort: "1 weekend"
    blocks: [P2, P3, P4, P5, P6, P7, P8]

  - id: P2
    name: Data Sources
    status: pending
    estimated_effort: "1 weekend"
    blocks: [P3, P4, P5, P6]

  - id: P3
    name: Reference Layer
    status: pending
    estimated_effort: "1 weekend"
    blocks: [P5]

  - id: P4
    name: Structural Analytics
    status: pending
    estimated_effort: "1 weekend"
    blocks: [P5, P6]

  - id: P5
    name: LLM Reasoning
    status: pending
    estimated_effort: "2 weekends"
    blocks: [P6]

  - id: P6
    name: Pipeline Integration
    status: pending
    estimated_effort: "1 weekend"
    blocks: [P7]

  - id: P7
    name: Local UI
    status: pending
    estimated_effort: "1 weekend"
    blocks: []

  - id: P8
    name: Refresh Automation
    status: pending
    estimated_effort: "1 weekend"
    blocks: []
```

---

## Phase 1: Foundation

<context name="phase_1">

### Goal
Validate the core data pipeline. Get cards into a database we can query.

### Deliverables

```yaml
deliverables:
  - id: D1.1
    name: "Project scaffolding"
    items:
      - "pyproject.toml with all dependencies from CLAUDE.md technology_stack"
      - "Directory structure per CLAUDE.md (src/, config/, data/, scripts/, tests/)"
      - ".env.example with required keys (ANTHROPIC_API_KEY, TOPDECK_API_KEY)"
      - ".gitignore (excludes data/, .venv/, .env, *.pyc)"
      - "README.md (basic; full docs are separate)"

  - id: D1.2
    name: "SQLite schema setup"
    items:
      - "scripts/setup_db.py creates all tables from schema.md Section 1"
      - "_schema_version table tracks current version"
      - "Run via: python scripts/setup_db.py"
      - "Idempotent (safe to re-run)"

  - id: D1.3
    name: "Pydantic models"
    items:
      - "src/models/card.py - Card, CardRuling"
      - "src/models/deck.py - Deck, DeckCard, GeneratedDeck"
      - "src/models/profile.py - CommanderProfile and sub-models"
      - "src/models/evidence.py - EvidencePackage and sub-models"
      - "src/models/llm_responses.py - LLM response models"
      - "All match schema.md Section 2"

  - id: D1.4
    name: "Configuration system"
    items:
      - "src/config.py loads YAML from config/"
      - "config/settings.yaml with defaults from schema.md Section 3.1"
      - "Validation: settings load successfully on import"

  - id: D1.5
    name: "Scryfall ingestion"
    items:
      - "src/ingestion/base.py - IngestionSource Protocol"
      - "src/ingestion/scryfall.py - implements Scryfall bulk download"
      - "Per api_contracts.md Section 2.1"
      - "Rate limiting via src/utils/rate_limit.py"
      - "Populates cards + card_prices tables"

  - id: D1.6
    name: "CLI scaffold"
    items:
      - "src/main.py with Click commands per api_contracts.md Section 4"
      - "All commands stubbed (return 'not implemented')"
      - "Working: python -m sabermetrics --help"

  - id: D1.7
    name: "Initial ingestion script"
    items:
      - "scripts/initial_ingestion.py orchestrates first-time data load"
      - "Supports flags: --scryfall-only, --skip-prices"
      - "Logs progress, handles errors gracefully"
```

### Acceptance Criteria

```yaml
acceptance:
  - id: A1.1
    test: "python scripts/setup_db.py succeeds against fresh sabermetrics.db"
    verify: "All tables from schema.md exist via sqlite3 .schema"

  - id: A1.2
    test: "python -m sabermetrics --help shows all commands"
    verify: "All CLI commands from api_contracts.md Section 4 listed"

  - id: A1.3
    test: "python scripts/initial_ingestion.py --scryfall-only completes"
    verify:
      - "cards table has >25,000 rows"
      - "card_prices table has >25,000 rows"
      - "All cards have non-null oracle_id, name, color_identity"
      - "Run completes in <30 minutes"

  - id: A1.4
    test: "Pydantic model validation"
    verify: "Sample card from cards table parses into Card model without errors"

  - id: A1.5
    test: "Config loading"
    verify: "src/config.py loads settings.yaml without error and validates against schema"
```

### Common Issues

```yaml
known_issues:
  - issue: "Scryfall bulk download is large (~150MB)"
    mitigation: "Stream download with progress bar; allow resume"

  - issue: "JSON array fields in SQLite need serialization"
    pattern: "Use json.dumps() / json.loads() consistently in adapter layer"

  - issue: "Pricing data has nulls (some cards untracked)"
    handling: "Allow null prices in card_prices; filter out at query time"
```

### Status Update

When complete, update CLAUDE.md:
```yaml
status:
  current_phase: "Phase 2 - Data Sources"
  completed_phases: ["P1: Foundation"]
```

</context>

---

## Phase 2: Data Sources

<context name="phase_2">

### Goal
Populate all secondary data sources. Build the data foundation before reasoning layer.

### Deliverables

```yaml
deliverables:
  - id: D2.1
    name: "TopDeck.gg ingestion"
    file: src/ingestion/topdeck.py
    follows: api_contracts.md Section 2.3
    populates: tournament_results

  - id: D2.2
    name: "Decklist sources via mtg-parser"
    files:
      - src/ingestion/moxfield.py
      - src/ingestion/archidekt.py
      - src/ingestion/deckstats.py
    follows: api_contracts.md Section 2.5
    populates: decks, deck_cards
    note: "Pull popular decklists for top 100 commanders initially"

  - id: D2.3
    name: "EDHREC scraper"
    file: src/ingestion/edhrec.py
    follows: api_contracts.md Section 2.4
    populates: edhrec_commander_data
    note: "Scrape JSON endpoints behind page rendering; rate-limit to 1 req/sec"

  - id: D2.4
    name: "Commander Spellbook combo ingestion"
    file: src/ingestion/spellbook.py
    follows: api_contracts.md Section 2.6
    populates: combos

  - id: D2.5
    name: "magicthegathering.io rulings ingestion"
    file: src/ingestion/mtgapi.py
    follows: api_contracts.md Section 2.2
    populates: card_rulings
    library: mtgsdk (pip install mtgsdk)
    cadence: monthly (NOT daily; rulings change rarely)

  - id: D2.6
    name: "Reddit search wrapper"
    file: src/ingestion/reddit.py
    follows: api_contracts.md Section 2.7
    note: "On-demand only, not scheduled; called during profile generation"
    no_persistence: "Doesn't write to DB; returns RedditThread list"

  - id: D2.7
    name: "Source health monitoring"
    file: src/ingestion/health.py
    populates: source_health table
    function: "Periodic check of is_available() for each source"
```

### Acceptance Criteria

```yaml
acceptance:
  - id: A2.1
    test: "Each source's sync() method completes successfully"
    verify: "SyncResult returned with success=True for each source"

  - id: A2.2
    test: "After full ingestion run"
    verify:
      - "decks table: >10,000 rows"
      - "deck_cards table: >5,000,000 rows (avg ~99 per deck × 10K+ decks)"
      - "tournament_results: >100 rows"
      - "edhrec_commander_data: >100 entries (top commanders)"
      - "combos: >5,000 rows"
      - "card_rulings: >5,000 rows for high-frequency cards"

  - id: A2.3
    test: "Source failure isolation"
    setup: "Disable network temporarily"
    verify: "Other sources continue ingesting; degraded source marked in source_health"

  - id: A2.4
    test: "Rate limiting"
    monitor: "Outbound request rate during ingestion"
    verify: "No source exceeds its declared rate limit"

  - id: A2.5
    test: "Idempotency"
    action: "Run sync twice in succession"
    verify: "No duplicate rows; updated_at fields advance for changed rows only"
```

### Status Update

```yaml
status:
  current_phase: "Phase 3 - Reference Layer"
  completed_phases: ["P1", "P2"]
```

</context>

---

## Phase 3: Reference Layer

<context name="phase_3">

### Goal
Build the RAG-grounding knowledge base for LLM reasoning.

### Deliverables

```yaml
deliverables:
  - id: D3.1
    name: "Comprehensive Rules ingestion"
    file: src/ingestion/reference.py
    source: "https://magic.wizards.com/en/rules"
    process:
      - "Download .txt rules file"
      - "Parse by section number (CR 100, CR 101, etc.)"
      - "Store raw to data/reference/comprehensive_rules.txt"

  - id: D3.2
    name: "Commander rules ingestion"
    source: "https://mtgcommander.net/index.php/rules/"
    store_to: data/reference/commander_rules.txt

  - id: D3.3
    name: "Strategic article curation"
    file: config/strategic_articles.yaml
    contents: "Hardcoded URLs of curated strategy content"
    initial_set:
      - "Mark Rosewater design philosophy articles"
      - "Reid Duke fundamentals series"
      - "Frank Karsten mana base mathematics"
      - "EDHREC archetype deep-dives"
    fetch: src/ingestion/reference.py scrapes and stores

  - id: D3.4
    name: "Document chunker"
    file: src/reference_layer/chunker.py
    function: "Split documents into ~500-token chunks with semantic boundary respect"
    requirements:
      - "Comprehensive Rules: chunk by section number"
      - "Articles: chunk by paragraph clusters with overlap"
      - "Each chunk gets metadata: document, section, tier"

  - id: D3.5
    name: "Embedding indexer"
    file: src/reference_layer/indexer.py
    library: sentence-transformers
    model: "all-MiniLM-L6-v2"
    process:
      - "For each chunk, compute embedding"
      - "Store as numpy array bytes in reference_chunks.embedding column"
      - "Build in-memory cache for query-time loading"

  - id: D3.6
    name: "Retrieval interface"
    file: src/reference_layer/retriever.py
    follows: api_contracts.md Section 1.3
    function: "Cosine similarity search over reference_chunks"

  - id: D3.7
    name: "WotC bracket framework"
    file: config/game_changers.yaml
    contents: "Current official game-changer list"
    populates_reference: "Game-changer list also indexed as reference chunk"

  - id: D3.8
    name: "Hand-curated synergy rules"
    file: config/synergy_rules.yaml
    initial_count: "~50 rules covering common patterns"
    examples:
      - "untap_lands_with_high_cost_activated"
      - "tokens_with_sacrifice_payoff"
      - "spellslinger_count_triggers"
      - "graveyard_recursion_with_self_mill"
```

### Acceptance Criteria

```yaml
acceptance:
  - id: A3.1
    test: "Reference chunks populated"
    verify:
      - "reference_chunks table: >5,000 rows"
      - "All chunks have non-null embedding"
      - "All chunks have valid tier (1-4)"

  - id: A3.2
    test: "Retrieval quality on test queries"
    queries:
      - query: "what is color identity in commander"
        expect_top_match: "Section CR 903 (Commander format)"
      - query: "how does ward 2 work"
        expect_top_match: "CR 702.21 (Ward keyword)"
      - query: "spellslinger archetype strategy"
        expect_top_match: "Strategic article on spellslinger or instant-speed control"
    verify: "Top match makes sense semantically"

  - id: A3.3
    test: "Retrieval performance"
    measure: "Time to retrieve top-10 chunks for a query"
    target: "<200ms for cosine similarity over 5000+ chunks"

  - id: A3.4
    test: "Synergy rules load and parse"
    verify:
      - "config/synergy_rules.yaml validates against schema"
      - "All rules have id, trigger, payoff, strength, description"
```

</context>

---

## Phase 4: Structural Analytics

<context name="phase_4">

### Goal
Implement deterministic scoring functions. The scaffolding the LLM layer builds on.

### Deliverables

```yaml
deliverables:
  - id: D4.1
    name: "Hard-rule filters"
    file: src/analytics/filters.py
    functions:
      - filter_by_color_identity(cards, commander)
      - filter_by_legality(cards, format='commander')
      - filter_by_budget(cards, max_price)
      - filter_singleton_legal(cards)
      - filter_by_banned_list(cards)
    follows: api_contracts.md Section 1.6 patterns

  - id: D4.2
    name: "Co-occurrence matrix builder"
    file: src/analytics/cooccurrence.py
    function: "Build sparse matrix from decks + deck_cards"
    schedule: "Run weekly after decklist sync"
    populates: card_cooccurrence table

  - id: D4.3
    name: "Card Win Equity"
    file: src/analytics/card_win_equity.py
    function: "Compute lift in win rate when card present"
    formula: "wr_with - wr_without, with Wilson confidence interval"
    schedule: "Run weekly after tournament sync"
    populates: card_win_equity table

  - id: D4.4
    name: "CVAR composite scoring"
    file: src/analytics/cvar.py
    function: "Weighted sum: synergy + mana_eff + replacement_val - price_penalty"
    requires: ScoringContext with weights from settings.yaml or override

  - id: D4.5
    name: "Card-demand index"
    file: src/analytics/card_demand.py
    function: "Compute price × inclusion_rate as demand proxy (per edhpowerlevel methodology)"

  - id: D4.6
    name: "Component scorer"
    file: src/analytics/components.py
    functions:
      - count_ramp_spells(deck) -> int
      - count_card_draw(deck) -> int
      - count_removal(deck) -> int
      - count_board_wipes(deck) -> int
      - count_tutors(deck) -> int
      - analyze_mana_base(deck) -> ManaBaseScore

  - id: D4.7
    name: "Bracket classifier"
    file: src/analytics/brackets.py
    function: "Classify deck per WotC 5-tier framework"
    inputs:
      - deck cards
      - game_changers from config
    output: "Bracket 1-5 with reasoning"

  - id: D4.8
    name: "Embedding wrapper"
    file: src/analytics/embeddings.py
    function: "Wrap sentence-transformers for application-wide use"
    cache: "In-memory cache of recently embedded texts"
```

### Acceptance Criteria

```yaml
acceptance:
  - id: A4.1
    test: "Filters reduce candidates correctly"
    setup: "Korvold (BRG) commander"
    verify:
      - "color_identity filter: ~25,000 → ~5,000"
      - "After all filters with $200 budget: ~2,000 candidates"

  - id: A4.2
    test: "CVAR scoring runs in <100ms per card"
    measure: "Time 100 calls; compute average"

  - id: A4.3
    test: "Co-occurrence query"
    verify: "card_cooccurrence has entries for all top-100 commanders"

  - id: A4.4
    test: "CWE computation"
    verify:
      - "card_win_equity entries exist for cards with sample_size >= 5"
      - "Confidence intervals computed correctly (Wilson score)"

  - id: A4.5
    test: "Bracket classifier sanity"
    inputs:
      - "Unmodified precon → Bracket 1 or 2"
      - "Heavy fast mana + tutors + combos → Bracket 4 or 5"
    verify: "Classifications match intuition"

  - id: A4.6
    test: "End-to-end analyze command"
    cli: "python -m sabermetrics analyze-deck <decklist-url>"
    verify: "Outputs all metrics for the deck without LLM calls"
```

</context>

---

## Phase 5: LLM Reasoning

<context name="phase_5">

### Goal
The differentiation layer. Make the tool smart, not just analytical.

### Deliverables

```yaml
deliverables:
  - id: D5.1
    name: "Anthropic client wrapper"
    file: src/reasoning/client.py
    follows: api_contracts.md Section 1.2
    requirements:
      - "Singleton pattern"
      - "Prompt caching support with cache_breakpoints"
      - "Cost tracking to cost_log table on every call"
      - "Monthly cost ceiling enforcement"
      - "Exponential backoff retry"
      - "Model name validation against ADR-011"

  - id: D5.2
    name: "Prompt template loader"
    file: src/reasoning/prompts/__init__.py
    function: "Load .txt files from src/reasoning/prompts/"
    template_format: "Python str.format with named placeholders"

  - id: D5.3
    name: "Prompt files"
    location: src/reasoning/prompts/
    files:
      - profile_synthesis.txt (per prompts.md PROMPT-001)
      - card_fit.txt (per prompts.md PROMPT-002)
      - deck_synthesis.txt (per prompts.md PROMPT-003)
      - relevance_screen.txt (per prompts.md PROMPT-004)

  - id: D5.4
    name: "Evidence aggregator"
    file: src/reference_layer/evidence.py
    function: "Compose EvidencePackage from data layer + on-demand Reddit"
    follows: SKILLS.md SKILL-004 step 3

  - id: D5.5
    name: "Profiler"
    file: src/reasoning/profiler.py
    follows:
      - api_contracts.md Section 1.4
      - SKILLS.md SKILL-004
    function: "Generate or retrieve commander profile"

  - id: D5.6
    name: "Per-card fit scorer"
    file: src/reasoning/fit.py
    function: "Score 50 candidates with parallelized Haiku calls"
    parallelism: "Up to 5 concurrent (rate limit safety)"

  - id: D5.7
    name: "Deck synthesizer"
    file: src/reasoning/synthesis.py
    function: "Generate deck-level narrative via Sonnet"
```

### Acceptance Criteria

```yaml
acceptance:
  - id: A5.1
    test: "Anthropic client cost tracking"
    setup: "Make 10 test calls"
    verify:
      - "cost_log has 10 rows"
      - "Sum of cost_usd matches expected pricing for token counts"

  - id: A5.2
    test: "Profile generation for known commanders"
    commanders: ["Atraxa, Praetors' Voice", "Korvold, Fae-Cursed King", "Edgar Markov", "Yuriko, the Tiger's Shadow", "Kinnan, Bonder Prodigy"]
    verify_each:
      - "Profile generated in <60 seconds"
      - "Cost <$0.50"
      - "Output validates against CommanderProfile schema"
      - "primary_archetype matches the actual archetype (judgment call - manual review)"
      - "anti_synergies contains plausible items"
      - "sources block populated"

  - id: A5.3
    test: "Card fit scoring with caching"
    setup: "Score 50 cards for one commander"
    verify:
      - "First call uses full input tokens"
      - "Subsequent 49 calls show high cached_input_tokens"
      - "Total cost <$0.10"
      - "All 50 results validate against CardFitResponse schema"

  - id: A5.4
    test: "Deck synthesis"
    setup: "Run on a deck assembled by Phase 4 tools"
    verify:
      - "DeckSynthesisResponse validates"
      - "key_synergies references specific cards from deck"
      - "weaknesses are mechanically specific"
      - "Cost <$0.10"

  - id: A5.5
    test: "Profile cache hit"
    setup: "Generate profile, then immediately request again"
    verify:
      - "Second call returns cached profile"
      - "Second call cost = $0"
      - "Second call time <100ms"
```

</context>

---

## Phase 6: Pipeline Integration

<context name="phase_6">

### Goal
End-to-end deck generation. The product becomes usable.

### Deliverables

```yaml
deliverables:
  - id: D6.1
    name: "Slot-aware deck assembler"
    file: src/pipeline/slot_assigner.py
    function: "Fill 99 cards per target composition"
    inputs:
      - "Top 50 candidates with fit reasoning"
      - "Target composition (X ramp, Y draw, Z removal, ...)"
    outputs:
      - "Final 99-card list"
      - "Alternatives list per slot"

  - id: D6.2
    name: "Deck builder orchestrator"
    file: src/pipeline/deck_builder.py
    follows:
      - api_contracts.md Section 1.5
      - SKILLS.md SKILL-005
    function: "Run all 10 pipeline stages in sequence"

  - id: D6.3
    name: "Output formatters"
    file: src/pipeline/formatters.py
    formats:
      - JSON (full structured output)
      - Text (human-readable stat sheet)
      - Moxfield-importable (cards list with quantities)
      - Archidekt-importable

  - id: D6.4
    name: "CLI integration"
    update: src/main.py
    commands_completed:
      - "profile" (was stub in P1)
      - "build" (was stub in P1)
      - "analyze-deck" (uses Phase 4 tools)
      - "search-rules" (uses Phase 3 retriever)
```

### Acceptance Criteria

```yaml
acceptance:
  - id: A6.1
    test: "Generate decks for 5 reference commanders"
    commanders: ["Atraxa", "Korvold", "Edgar Markov", "Yuriko", "Kinnan"]
    parameters: "Each at $200 budget, power 3"
    verify_each:
      - "Exactly 99 cards + 1 commander"
      - "All cards in commander's color identity"
      - "Total price ≤ $200"
      - "Each card has llm_fit reasoning"
      - "Deck has narrative (game_plan, synergies, weaknesses)"
      - "Bracket classification with reasoning"
      - "Generation time <90s (cache miss profile) or <30s (cache hit)"
      - "Cost ≤ $0.50 (cache miss) or ≤ $0.15 (cache hit)"

  - id: A6.2
    test: "Output format validity"
    verify:
      - "Moxfield-format output can be imported into Moxfield"
      - "JSON format validates against GeneratedDeck schema"

  - id: A6.3
    test: "Re-generation determinism"
    setup: "Run build for same commander twice with same parameters"
    verify: "~80% card overlap (some randomness expected from LLM, but core stable)"

  - id: A6.4
    test: "Budget compliance"
    parameters: "$50 budget"
    verify: "Total deck price ≤ $50; no single card >15% of budget"
```

</context>

---

## Phase 7: Local UI

<context name="phase_7">

### Goal
Make it usable beyond CLI for browsing and exploration.

### Deliverables

```yaml
deliverables:
  - id: D7.1
    name: "Flask app skeleton"
    file: src/ui/app.py
    binding: "127.0.0.1 only (per security model)"
    port: 5000

  - id: D7.2
    name: "Templates"
    location: src/ui/templates/
    pages:
      - index.html (home / commander selector)
      - deck_view.html (generated deck display)
      - profile_view.html (commander profile)
      - reference_search.html (browse rules / articles)
      - cost_report.html (spending dashboard)

  - id: D7.3
    name: "Routes"
    file: src/ui/routes.py
    endpoints:
      - GET /
      - GET /commander/{name}/profile
      - POST /generate-deck
      - GET /deck/{id}
      - GET /reference/search?q=
      - GET /report

  - id: D7.4
    name: "Static assets"
    location: src/ui/static/
    items:
      - "Minimal CSS (per arxiv 2512.09802 guidance: clean, comprehensible)"
      - "Use heatmaps and line charts; avoid complex visualizations"

  - id: D7.5
    name: "CLI integration"
    command: "python -m sabermetrics serve"
    behavior: "Launch Flask app; print localhost:5000 URL"
```

### Acceptance Criteria

```yaml
acceptance:
  - id: A7.1
    test: "Server launches"
    cmd: "python -m sabermetrics serve"
    verify:
      - "Bound to 127.0.0.1 only (not 0.0.0.0)"
      - "Browser can load http://localhost:5000"

  - id: A7.2
    test: "Commander profile flow"
    actions:
      - "Navigate to home"
      - "Search for 'Korvold'"
      - "Click to view profile"
    verify: "Profile renders all sections from CommanderProfile model"

  - id: A7.3
    test: "Deck generation flow"
    actions:
      - "Configure budget=300, power=4"
      - "Submit"
      - "Wait for completion"
    verify:
      - "Result page shows full deck"
      - "Per-card stat sheets visible"
      - "Narrative renders"
      - "Alternatives accessible per slot"

  - id: A7.4
    test: "Reference search"
    query: "color identity"
    verify: "Returns relevant CR sections"

  - id: A7.5
    test: "Cost report"
    verify:
      - "Shows spend by call_type for last 30 days"
      - "Projects annual rate"
      - "Compares to ceiling"
```

</context>

---

## Phase 8: Refresh Automation

<context name="phase_8">

### Goal
The system runs itself.

### Deliverables

```yaml
deliverables:
  - id: D8.1
    name: "Nightly refresh script"
    file: scripts/nightly_refresh.py
    actions:
      - "Scryfall sync"
      - "Update health monitoring"

  - id: D8.2
    name: "Weekly refresh script"
    file: scripts/weekly_refresh.py
    actions:
      - "TopDeck.gg sync"
      - "Decklist sources sync (Moxfield, Archidekt, deckstats)"
      - "EDHREC sync for tracked commanders"
      - "Spellbook sync"
      - "Co-occurrence rebuild"
      - "CWE recomputation"

  - id: D8.3
    name: "Monthly rulings refresh"
    file: scripts/monthly_rulings_refresh.py
    actions:
      - "magicthegathering.io rulings sync"
      - "Iterates by oracle_id; rate-limited 1 req/sec"

  - id: D8.4
    name: "Quarterly set refresh"
    file: scripts/quarterly_set_refresh.py
    follows: SKILLS.md SKILL-006
    actions:
      - "Detect new sets via Scryfall"
      - "Update Comprehensive Rules if changed"
      - "Update keyword glossary"
      - "Refresh ban list"
      - "Per-cached-profile relevance screening (Haiku)"
      - "Mark affected profiles is_stale=true"

  - id: D8.5
    name: "launchd plists"
    location: launchd/
    files:
      - nightly.plist (daily 2am)
      - weekly.plist (Sunday 3am)
      - monthly.plist (1st Sunday 4am)
      - quarterly.plist (manual trigger initially)

  - id: D8.6
    name: "Installation script"
    file: scripts/install_launchd.sh
    function: "Copy plists to ~/Library/LaunchAgents and load"

  - id: D8.7
    name: "Logging infrastructure"
    file: src/utils/logging.py
    config:
      - "JSON structured logs"
      - "Per-job log file under data/logs/"
      - "Rotation: 10MB per file, 5 backups"
```

### Acceptance Criteria

```yaml
acceptance:
  - id: A8.1
    test: "Nightly refresh manual run"
    cmd: "python scripts/nightly_refresh.py"
    verify:
      - "Completes in <15 min"
      - "Cards table updated_at advances"
      - "Logs written to data/logs/nightly.log"

  - id: A8.2
    test: "Weekly refresh manual run"
    cmd: "python scripts/weekly_refresh.py"
    verify:
      - "Completes in <90 min"
      - "All weekly tables updated"
      - "Co-occurrence and CWE recomputed"

  - id: A8.3
    test: "Quarterly set refresh"
    cmd: "python scripts/quarterly_set_refresh.py"
    setup: "Recent set with new cards in active commander colors"
    verify:
      - "Relevance screen runs for affected profiles"
      - "Cost <$5 total"
      - "is_stale flags set correctly"

  - id: A8.4
    test: "launchd installation"
    cmd: "bash scripts/install_launchd.sh"
    verify:
      - "Plists copied to ~/Library/LaunchAgents/"
      - "launchctl list shows all jobs"

  - id: A8.5
    test: "Health command"
    cmd: "python -m sabermetrics health"
    verify:
      - "Shows last_successful_sync for each source"
      - "Identifies any sources in degraded state"
```

</context>

---

## Final Acceptance Test Suite

<context name="final_acceptance">

The system is "shippable" (for personal use) when ALL of these pass:

```yaml
foundation:
  - "Cards table populated with >25,000 entries"
  - "Decks table populated with >10,000 entries from at least 2 sources"
  - "Tournament results: >100 entries"
  - "Card rulings: >5,000 entries"

reference_layer:
  - "Comprehensive Rules chunked and embedded"
  - "Reference search returns sensible results for 5 test queries"

structural_analytics:
  - "CVAR scoring runs in <100ms per card"
  - "Co-occurrence matrix queryable by commander"
  - "CWE computed for cards with sample_size >= 5"

llm_reasoning:
  - "Profile generation completes in <60 seconds for 5 test commanders"
  - "Generated profiles capture correct primary archetype"
  - "Per-card fit scoring runs at <$0.005 per card with caching"

pipeline:
  - "End-to-end deck generation completes in <30 seconds (cache hit)"
  - "Generated decks include 99 unique cards in correct color identity"
  - "All decks include rationale + per-card reasoning"
  - "Total deck price respects budget"

ui:
  - "Flask UI runs on localhost:5000 only"
  - "All CLI flows accessible via UI"

automation:
  - "Nightly refresh runs without errors via launchd"
  - "Weekly refresh runs without errors via launchd"
  - "Monthly rulings refresh runs without errors via launchd"
  - "Quarterly refresh correctly flags new sets"

cost:
  - "30 days of regular use costs <$5 in LLM API spend"
  - "Annual projection <$50"
  - "Per-deck cost (cache hit) <$0.15"
  - "Per-profile cost <$0.50"

quality_gate:
  - "Generate a deck for each of your 5 most-built commanders"
  - "Compare to your actual built decks"
  - "Identify ≥1 surprise inclusion per deck"
  - "Validate that anti-synergies make sense"
  - "Confirm bracket estimates feel correct"
```

When all pass, mark the project complete. Begin daily use. Iterate based on lived experience.

</context>

---

## Known Issues / Future Work

<context name="known_issues">

This section captures issues discovered during build that are deferred. Update as build progresses.

```yaml
deferred:
  - issue: "Goldfish simulation not in V1"
    when_revisit: "If sim calibration needed for confidence in scoring"

  - issue: "Multi-player simulation not in V1"
    when_revisit: "After goldfish integration is complete"

  - issue: "User-pod simulation not in V1"
    when_revisit: "After multi-player simulation"

  - issue: "Mobile UI not built"
    when_revisit: "Never; out of scope per design.md"
```

</context>
