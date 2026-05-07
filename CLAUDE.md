# CLAUDE.md

<instructions>
This file is auto-loaded by Claude Code at the start of every session in this repository. It provides project context, conventions, and decisions that should inform every code generation. Read this file in full before responding to any request. When making implementation choices not specified here, defer to the principles in this file.
</instructions>

---

<context name="project_identity">

## Project Identity

- **Name:** Sabermetrics for Magic
- **Type:** Personal research tool, single-user, self-hosted
- **Owner:** Dylan Matthews
- **Purpose:** Generate Commander/EDH decklists optimized for budget, synergy, and strategic coherence using LLM-driven reasoning over multiple data sources.
- **Inspiration:** Sabermetric "moneyball" methodology in professional sports analytics — find cards with the best cost-to-impact ratio.

</context>

---

<context name="critical_constraints">

## Critical Constraints (Hard Requirements)

These constraints are non-negotiable. Every code generation must respect them.

```yaml
constraints:
  user_count: 1
  hosting: "self-hosted on Mac mini, no cloud infrastructure"
  manual_data_entry: forbidden
  human_in_the_loop: forbidden
  data_acquisition: "fully automated via APIs and structured scrapes only"
  excluded_data_sources:
    - youtube_event_scraping  # Validation gap unacceptable
    - personal_pod_logging    # Breaks "no manual input" principle
    - manual_winner_labeling  # Breaks "no manual input" principle
  annual_cost_target_usd: 30
  annual_cost_ceiling_usd: 100
  per_deck_cost_target_usd: 0.15
  per_deck_cost_ceiling_usd: 0.50
  format_scope: "Commander (EDH) only"
  ui_scope: "Desktop web UI on localhost only; no mobile"
```

</context>

---

<context name="core_value_proposition">

## Core Value Proposition

Existing EDH tools (EDHREC, edhpowerlevel, Moxfield analyzer) are **frequency counters with weighted heuristics**. They recommend cards based on what other players include.

This tool **reasons about why a commander wants specific cards**, grounded in:
1. The card's actual mechanical text
2. Aggregated player behavior (corroboration, not authority)
3. Community discussion (cultural signal)
4. Official rules and strategic frameworks (RAG grounding)

The differentiation is strategic comprehension, not better algorithms. LLM reasoning makes this possible at affordable cost via aggressive prompt caching and a three-tier filter pipeline.

</context>

---

<context name="architectural_principles">

## Architectural Principles

When implementation choices arise that aren't explicitly specified, resolve them using these principles in priority order:

1. **Locality over distribution** — single-process Python application, no microservices, no cloud
2. **Lazy computation over eager** — generate profiles on-demand for active commanders, never pre-compute all 25,000 cards
3. **Reasoning layered over reasoning required** — cheap deterministic filters narrow candidates before expensive LLM calls
4. **Evidence triangulation over single-source truth** — fuse card text + behavioral data + community discussion
5. **Reference grounding over pure generation** — LLM reasoning is RAG-augmented with rules and frameworks
6. **Cache hierarchically** — different TTLs aligned to data volatility (permanent / quarterly / weekly / daily / per-session)
7. **Observable over opaque** — every output cites sources and exposes confidence
8. **Bounded cost over unbounded capability** — every operation has a budget; never trade indefinite cost growth for marginal capability

</context>

---

<context name="technology_stack">

## Technology Stack

```yaml
runtime:
  language: Python
  version: ">=3.11"
  package_manager: pip
  virtual_env: ".venv"

storage:
  primary_db:
    engine: SQLite
    file: "data/sabermetrics.db"
    rationale: "Single-user system; embedded DB simplifies deployment"
  vector_storage:
    approach: "numpy arrays as SQLite BLOBs"
    rationale: "Reference layer is small (<10K chunks); cosine similarity in numpy is sub-millisecond"
  blob_storage: "filesystem under data/"

llm:
  provider: Anthropic
  models:
    profile_synthesis: "claude-sonnet-4-6"
    card_fit_scoring: "claude-haiku-4-5"
    deck_synthesis: "claude-sonnet-4-6"
    relevance_screening: "claude-haiku-4-5"
  caching: "prompt caching enabled for all calls"
  sdk: "anthropic Python SDK"

embeddings:
  library: "sentence-transformers"
  model: "all-MiniLM-L6-v2"
  device: "cpu"

web_framework:
  ui: Flask
  binding: "localhost only"
  port: 5000

scheduling:
  system: "macOS launchd"
  jobs:
    - nightly: "Scryfall card + price refresh"
    - weekly: "Decklists, EDHREC, tournaments, derived metrics"
    - monthly: "magicthegathering.io rulings"
    - quarterly: "Set release refresh, profile invalidation"

key_libraries:
  - mtg-parser           # Decklist parsing across multiple sources
  - mtgsdk               # magicthegathering.io official SDK
  - anthropic            # Claude API
  - sentence-transformers
  - pydantic             # Data models
  - flask                # Local UI
  - click                # CLI
  - pyyaml               # Configuration
  - httpx                # HTTP client
  - numpy                # Embeddings, vector ops
  - pandas               # Data manipulation
```

</context>

---

<context name="external_data_sources">

## External Data Sources

```yaml
sources:
  - name: Scryfall
    role: "PRIMARY card data source"
    url: "https://api.scryfall.com/bulk-data"
    auth: none
    cost: free
    refresh: daily
    provides: ["cards", "prices", "oracle_text", "images", "legality"]

  - name: magicthegathering.io
    role: "SUPPLEMENTARY: rulings only"
    url: "https://api.magicthegathering.io/v1/cards"
    sdk: mtgsdk
    auth: none
    rate_limit: "5000 req/hour"
    refresh: monthly
    provides: ["rulings"]
    explicitly_NOT_used_for: ["card data", "prices"]

  - name: TopDeck.gg
    role: "Tournament outcome data"
    url: "https://topdeck.gg/api/v2"
    auth: "API key (free)"
    refresh: weekly
    provides: ["tournament_results", "win_rates", "decklists"]

  - name: Moxfield
    role: "Decklist source"
    library: mtg-parser
    refresh: weekly
    provides: ["popular_decklists"]

  - name: Archidekt
    role: "Decklist source (redundancy)"
    library: mtg-parser
    refresh: weekly
    provides: ["popular_decklists"]

  - name: deckstats.net
    role: "Decklist source (redundancy)"
    library: mtg-parser
    refresh: weekly
    provides: ["popular_decklists"]

  - name: EDHREC
    role: "Inclusion rates, themes, salt scores"
    url: "https://json.edhrec.com (page-derived JSON)"
    auth: none
    refresh: weekly
    rate_limit: "1 req/sec (politeness)"
    provides: ["inclusion_data", "themes", "tags", "salt_scores"]

  - name: Commander Spellbook
    role: "Combo database"
    url: "https://commanderspellbook.com/api"
    auth: none
    refresh: weekly
    provides: ["combos"]

  - name: Reddit r/EDH
    role: "Cultural signal for profile generation"
    url: "https://www.reddit.com/r/EDH/search.json"
    auth: none
    refresh: on-demand (during profile generation)
    rate_limit: "1 req/sec"

  - name: WotC Comprehensive Rules
    role: "Reference layer foundation"
    url: "https://magic.wizards.com/en/rules"
    refresh: quarterly
```

</context>

---

<context name="excluded_capabilities">

## Explicitly Excluded Capabilities

Do not implement these. They have been considered and rejected:

- YouTube event scraping (validation gap)
- Personal pod game logging (manual input)
- Multi-user support (out of scope)
- Public hosting (out of scope)
- Real-time gameplay assistance (architectural mismatch)
- Mobile UI (out of scope)
- Card image rendering (cosmetic, deferred)
- Full game-theory-optimal play modeling (research-grade)
- Goldfish simulation in V1 (deferred to V2)
- Multi-player simulation in V1 (deferred to V2)
- Arena/Standard/Limited format support (Commander only)
- Untapped.gg or 17lands data ingestion (wrong format)

</context>

---

<context name="cost_discipline">

## Cost Discipline

Every LLM call must:
1. Pass through `src/reasoning/client.py` wrapper
2. Use prompt caching where reusable context exists
3. Log token usage and computed cost to `cost_log` table
4. Respect monthly cost ceiling (default $5/mo, configurable)
5. Use the cheapest model that meets quality requirements (Haiku by default; Sonnet only when warranted)

Cost call distribution per deck generation (target):

```yaml
deck_generation_cost_breakdown:
  profile_synthesis:
    when: "cache miss only (~1 in 50 generations)"
    model: claude-sonnet-4-6
    cost: ~$0.40
  per_card_fit_scoring:
    count: 50
    model: claude-haiku-4-5
    cost_each: ~$0.001
    total: ~$0.05
    caching: "profile + reference chunks cached across all 50 calls"
  deck_synthesis:
    count: 1
    model: claude-sonnet-4-6
    cost: ~$0.05
  typical_total_per_deck: "~$0.10 (cache hit) or ~$0.50 (cache miss)"
```

</context>

---

<context name="conventions">

## Code Conventions

```yaml
style:
  formatter: black
  linter: ruff
  type_checker: mypy
  type_hints: required
  docstring_style: "Google style, required for all public functions"

structure:
  models: "Pydantic v2 for all data structures crossing module boundaries"
  errors:
    pattern: "Custom exception classes per layer; no bare `except:`"
    classes:
      - "RecoverableError (retry with backoff)"
      - "DegradableError (continue with reduced functionality)"
      - "FatalError (halt and alert)"

  configuration:
    pattern: "All non-secret config in YAML under config/; secrets in .env"
    no_magic_constants: true

  testing:
    framework: pytest
    minimum_coverage: "Core scoring functions, filters, and parsers must have unit tests"
    skip: "Extensive UI testing, generation determinism tests"

logging:
  module: "Python stdlib logging"
  format: "JSON structured logs to data/logs/"
  rotation: "RotatingFileHandler, 10MB per file, 5 backups"
  cost_tracking: "Separate cost_log SQLite table, written by Anthropic client wrapper"
```

</context>

---

<context name="architectural_decisions">

## Key Architectural Decisions (ADR Summary)

These decisions are settled. Do not relitigate in code; refer here for the "why."

| ID | Decision | Rationale |
|---|---|---|
| ADR-001 | Lazy profile generation | Eager is 50x more expensive and stale |
| ADR-002 | SQLite over Postgres | Single-user; embedded DB simpler |
| ADR-003 | numpy cosine over vector DB | <10K chunks; numpy is sub-ms |
| ADR-004 | 3-call LLM pattern with caching | 94% cost reduction vs single mega-prompt |
| ADR-005 | Triangulated evidence | Each source has blind spots |
| ADR-006 | RAG grounding required | Prevents hallucinated synergies |
| ADR-007 | No YouTube data | Validation gap unacceptable |
| ADR-008 | Mac mini self-hosted | Free, already owned |
| ADR-009 | Layered architecture | Microservices solve problems we don't have |
| ADR-010 | Quarterly refresh cadence | Aligns with Magic set releases |
| ADR-011 | Haiku for fit, Sonnet for synthesis | 12x cost difference; quality tier-matched |
| ADR-012 | Profile cache w/ set-version invalidation | Cheap relevance screening |
| ADR-013 | mtgapi for rulings only, Scryfall for cards | Scryfall has bulk + prices; mtgapi has inline rulings |
| ADR-014 | Rulings join by oracle_id | Rulings persist across reprints |

Full ADR text in `design.md` Section 11.

</context>

---

<context name="document_map">

## Document Map

This project's design is split across multiple documents. Read them in this order when starting:

```yaml
documents:
  - file: CLAUDE.md
    purpose: "This file. Project context, auto-loaded."
    read_when: "Always, at session start"

  - file: design.md
    purpose: "High-level vision, goals, constraints, ADRs"
    read_when: "Before architectural changes"

  - file: SKILLS.md
    purpose: "Recurring task workflows and patterns"
    read_when: "When implementing a workflow that has a defined skill"

  - file: schema.md
    purpose: "All data schemas (SQL, Pydantic, YAML)"
    read_when: "Before any data model changes or DB queries"

  - file: api_contracts.md
    purpose: "Module interfaces and external API contracts"
    read_when: "Before module-to-module integration work"

  - file: prompts.md
    purpose: "LLM prompt templates with input/output schemas"
    read_when: "Before any reasoning layer changes"

  - file: build_plan.md
    purpose: "Phased build sequence with acceptance criteria"
    read_when: "When deciding what to build next"
```

</context>

---

<context name="active_phase">

## Current Build Status

Track progress here. Update as phases complete.

```yaml
status:
  current_phase: "Complete"
  completed_phases: ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"]
  in_progress: []
  blocked: []
  notes: "All 8 phases complete. Refresh automation: nightly/weekly/monthly/quarterly scripts, launchd plists with install script, JSON structured logging with rotation. CLI refresh-set command wired. KB enriched with Karsten hypergeometric mana base analysis: source requirements table, per-archetype color targets, observed quality scores from Game Knights decks. 140 tests passing."
```

</context>
