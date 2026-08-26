# design.md

<instructions>
This is the high-level design document for Sabermetrics. It captures vision, goals, constraints, success criteria, and architectural decisions. Read this when:
- Making architectural changes
- Resolving conflicting requirements
- Proposing scope changes
- Understanding the "why" behind a code pattern

If this document conflicts with implementation specifications (api_contracts.md, schema.md), this document defines intent; the implementation docs define current shape.
</instructions>

---

## 1. Vision

<context name="vision">

A personal Commander/EDH deck optimization tool that combines structural data analytics with LLM-driven strategic reasoning to identify undervalued cards for budget-conscious deckbuilding.

The core insight: existing EDH tools answer *"What do players who like this commander typically include?"* (collaborative filtering). This tool answers *"What does this commander want to do, and which cards best enable that?"* (causal reasoning over game mechanics).

The differentiation is not better algorithms but **strategic comprehension at affordable cost** — possible only with current-generation LLMs and only economical for personal use.

</context>

---

## 2. Goals

<context name="goals">

### 2.1 Primary Goals

```yaml
primary_goals:
  - id: G1
    goal: "Generate decks that surface undervalued cards specific to a commander's strategy"
    measurement: "Each generated deck contains ≥1 card not in EDHREC top-50 inclusion for that commander"

  - id: G2
    goal: "Explain card inclusions in language that teaches, not just lists"
    measurement: "Each card has a one-sentence reasoning tied to commander strategy"

  - id: G3
    goal: "Identify anti-recommendations (popular cards that don't fit specific build)"
    measurement: "Profile output includes anti_synergies section with reasoning"

  - id: G4
    goal: "Run fully autonomously after one-time setup"
    measurement: "Zero manual data entry required for ongoing operation"

  - id: G5
    goal: "Stay within personal budget"
    measurement: "Annual LLM costs <$50/year"
```

### 2.2 Secondary Goals

```yaml
secondary_goals:
  - id: G6
    goal: "Support unconventional/niche strategies via user intent override"

  - id: G7
    goal: "Provide power-level estimation grounded in WotC bracket framework"

  - id: G8
    goal: "Maintain currency with new Magic set releases via quarterly refresh"

  - id: G9
    goal: "Enable inspection of every recommendation (sources, confidence, rules cited)"
```

</context>

---

## 3. Hard Constraints

<context name="constraints">

These constraints define the boundary of acceptable solutions. Violating any of them invalidates a design.

```yaml
hard_constraints:
  - id: C1
    constraint: "Single user only"
    rationale: "Personal tool; multi-tenant complexity unjustified"

  - id: C2
    constraint: "No manual data entry of any kind, on any cadence"
    rationale: "Automated systems must remain automated; manual workflows fail in week 3"

  - id: C3
    constraint: "Self-hosted on existing Mac mini"
    rationale: "No paid cloud infrastructure; hardware already owned"

  - id: C4
    constraint: "Tabular/API-first data sources only"
    rationale: "YouTube CV scraping has unfixable validation gap given C2"

  - id: C5
    constraint: "Annual LLM cost ≤$50"
    rationale: "Personal use; cost discipline is hard requirement"

  - id: C6
    constraint: "Lazy/on-demand LLM-driven profile generation"
    rationale: "Eager pre-computation is 50x more expensive and produces stale data"

  - id: C7
    constraint: "Reference layer required for LLM reasoning"
    rationale: "Pure LLM reasoning hallucinates mechanically invalid synergies"

  - id: C8
    constraint: "Commander format only"
    rationale: "Different formats have fundamentally different metrics; scope discipline"
```

</context>

---

## 4. Non-Goals

<context name="non_goals">

Explicit non-goals to prevent scope creep:

```yaml
non_goals:
  - YouTube event data acquisition
  - Personal pod game logging
  - Multi-user / public hosting
  - Real-time deck building during games
  - Arena/Standard/Limited formats
  - Mobile app
  - Card image rendering
  - Goldfish simulation in V1
  - Multi-player simulation in V1
  - Automated playtest learning from YouTube
  - Community features
  - Profile sharing between users
```

</context>

---

## 5. Success Criteria

<context name="success_criteria">

### 5.1 Technical Success

```yaml
technical_success:
  - "Pipeline runs end-to-end without manual intervention"
  - "Profile generation completes in <60 seconds per commander"
  - "Deck generation completes in <30 seconds per request (with cached profile)"
  - "Quarterly refresh completes in <2 hours, automated"
  - "Annual cost stays under $50"
  - "30 days of regular use costs <$5 in LLM API spend"
```

### 5.2 Product Success (Personal)

```yaml
product_success:
  - "Generates decks I would actually consider building"
  - "Surfaces ≥1 undervalued card per deck I hadn't considered"
  - "Explains card inclusions in language that teaches rather than lists"
  - "Identifies anti-recommendations specific to my build"
  - "Catches non-obvious synergies the structural tools miss"
```

### 5.3 Research Success

```yaml
research_success:
  - "Generated reasoning holds up to scrutiny"
  - "Different commanders produce meaningfully different strategic profiles"
  - "Profiles correctly identify niche vs mainstream archetypes"
  - "Recommendations remain stable across re-runs (where deterministic)"
```

</context>

---

## 6. Architectural Style

<context name="architectural_style">

**Chosen style:** Layered architecture with on-demand reasoning pipeline

The system has 7 horizontal layers:

```
1. Presentation Layer    (CLI / Flask UI)
2. Pipeline Layer        (Orchestration)
3. Reasoning Layer       (LLM-powered)
4. Analytics Layer       (Deterministic scoring)
5. Reference & Evidence  (RAG + per-commander aggregation)
6. Data Layer            (SQLite + filesystem)
7. Ingestion Layer       (External source adapters)
```

Each layer can only call layers below it. This enforces unidirectional dependencies.

**Why not microservices:** Single-user system has no coordination problems to solve.
**Why not event-driven:** Deterministic request-response flow; no producer/consumer fanout.
**Why not pure pipeline:** System needs both batch (ingestion) and interactive (deck generation) flows.

</context>

---

## 7. The Reasoning Architecture

<context name="reasoning_architecture">

The differentiation layer warrants explicit explanation.

### 7.1 Three-Call LLM Pattern

A single deck generation invokes the LLM three times in distinct roles:

```yaml
llm_calls:
  - name: profile_synthesis
    model: claude-sonnet-4-6
    frequency: "Once per commander, cached forever (until set release invalidates)"
    cost: ~$0.40
    cache_hit_rate: ~95%
    purpose: "Generate strategic profile by triangulating evidence streams"

  - name: per_card_fit_scoring
    model: claude-haiku-4-5
    frequency: "50 calls per deck generation"
    cost_per_call: ~$0.001
    cost_per_deck: ~$0.05
    cache_strategy: "Profile + reference chunks cached across all 50 calls"
    purpose: "Score each candidate's fit with one-sentence reasoning"

  - name: deck_synthesis
    model: claude-sonnet-4-6
    frequency: "Once per deck generation"
    cost: ~$0.05
    purpose: "Generate deck-level narrative, key synergies, weaknesses"
```

### 7.2 Why Three Calls Instead of One

A single mega-prompt would be:
- Too long for high-quality reasoning (attention dilution)
- Impossible to cache effectively (per-deck variance)
- More expensive than three-call pattern with caching

### 7.3 Evidence Triangulation

Profile generation fuses three evidence streams:

```yaml
evidence_streams:
  stream_1_card_text:
    source: Scryfall + mtgapi rulings
    role: "Authoritative for what the commander mechanically does"
    weight: "Primary"

  stream_2_behavioral:
    source: EDHREC + TopDeck.gg + Moxfield/Archidekt/deckstats
    role: "Corroboration of what players actually do"
    weight: "Secondary"

  stream_3_cultural:
    source: Reddit + community primers/articles
    role: "Surface emerging strategies and named archetypes"
    weight: "Tertiary"
```

Reference layer chunks (Comprehensive Rules, strategic frameworks) are retrieved via RAG and injected into the synthesis prompt as grounding context.

### 7.4 Three-Tier Filter Pipeline

LLM reasoning is reserved for the top candidates after deterministic filtering:

```yaml
candidate_pipeline:
  - tier: 1_hard_rules
    cost: free
    input: ~25,000 cards
    output: ~3,000 candidates
    operations: ["color identity", "format legality", "singleton", "budget cap"]

  - tier: 2_embedding_similarity
    cost: free
    input: ~3,000 candidates
    output: ~200 candidates
    operations: ["cosine similarity vs profile narrative"]

  - tier: 3_structural_scoring
    cost: free
    input: ~200 candidates
    output: ~50 candidates
    operations: ["CVAR", "CWE", "card-demand index", "co-occurrence"]

  - tier: 4_llm_fit_reasoning
    cost: ~$0.05
    input: ~50 candidates
    output: ~50 candidates with fit annotations
    operations: ["Haiku per-card scoring with cached profile context"]
```

</context>

---

## 8. Data Architecture

<context name="data_architecture">

### 8.1 Storage Choice

```yaml
storage_decision:
  primary: SQLite
  rationale:
    - "Single-user system has no concurrency requirements"
    - "Embedded DB simplifies deployment"
    - "Performance more than adequate for expected volumes (<10GB total)"
  rejected_alternatives:
    - postgres: "Operational complexity unjustified for solo use"
    - duckdb: "Would work, but SQLite has better library ecosystem"

vector_storage:
  approach: "numpy arrays as SQLite BLOBs"
  rationale: "Reference layer is <10K chunks; numpy cosine is sub-ms"
  rejected_alternatives:
    - pgvector: "Requires Postgres"
    - chroma: "Separate process"
    - pinecone: "Paid service"
```

### 8.2 Data Volumes (Steady State)

| Table | Rows | Storage |
|---|---|---|
| cards | ~30,000 | ~50 MB |
| card_prices (time series) | ~10M | ~500 MB |
| decks | ~50,000 | ~200 MB |
| deck_cards | ~5M | ~300 MB |
| tournament_results | ~10,000 | ~5 MB |
| edhrec_commander_data | ~3,000 | ~30 MB |
| card_cooccurrence | ~10M sparse | ~500 MB |
| card_win_equity | ~1M | ~50 MB |
| commander_profiles | ~20 active | ~5 MB |
| reference_chunks | ~10,000 | ~100 MB w/ embeddings |
| card_rulings | ~50,000 | ~30 MB |
| combos | ~30,000 | ~30 MB |

Total expected: ~2-3 GB SQLite + ~500 MB filesystem.

</context>

---

## 9. Refresh Cadence Strategy

<context name="refresh_cadence">

Different data has different volatility. Cache TTLs align with source freshness:

```yaml
refresh_schedules:
  daily:
    sources: [scryfall]
    purpose: "Card data and prices"
    runtime: "~10 min"
    cost: free

  weekly:
    sources: [topdeck, moxfield, archidekt, deckstats, edhrec, spellbook]
    purpose: "Decklists, tournament results, inclusion data"
    derived: ["co-occurrence matrix rebuild", "CWE recomputation"]
    runtime: "~60 min"
    cost: free

  monthly:
    sources: [mtgapi]
    purpose: "Card rulings (judge clarifications)"
    runtime: "~120 min (rate-limited to 1 req/sec)"
    cost: free

  quarterly:
    sources: [comprehensive_rules, commander_rules, banned_list]
    purpose: "Set release; reference layer updates"
    derived: ["per-commander profile relevance screening"]
    runtime: "~120 min"
    cost: ~$2-5 (relevance screens + selective regeneration)

  on_demand:
    sources: [reddit]
    purpose: "Cultural signal during profile generation"
    runtime: "~5-15 sec per profile"
```

</context>

---

## 10. Failure Mode Strategy

<context name="failure_modes">

### 10.1 Three Error Classes

```yaml
error_classes:
  recoverable:
    examples: [transient_network, rate_limit, temporary_api_outage]
    handling: "Retry with exponential backoff, max 3 attempts"

  degradable:
    examples: [single_source_unavailable, llm_call_failed]
    handling: "Continue with reduced functionality, mark output as degraded"

  fatal:
    examples: [schema_mismatch, corrupted_db, cost_ceiling_exceeded]
    handling: "Halt and alert user, require explicit unlock"
```

### 10.2 Per-Source Fallback Matrix

| Source Down | Fallback |
|---|---|
| Scryfall | Use cached card data; alert if >24h stale |
| mtgapi | Profile generation continues without rulings |
| TopDeck.gg | CWE scoring degrades to Moxfield-only |
| Moxfield | Falls back to Archidekt + deckstats |
| EDHREC | Profile generation drops behavioral stream; relies on text + community |
| Reddit | Profile generation drops cultural stream |
| Anthropic API | Return structural-only deck with explicit note |

</context>

---

## 11. Architectural Decision Records (ADRs)

<context name="adrs">

Each ADR records a major decision with rationale. Implementation should not relitigate these unless the underlying constraint has changed.

### ADR-001: Lazy Profile Generation
- **Decision:** Generate commander profiles on-demand, not in advance for all 25,000+ cards.
- **Rationale:** Eager generation costs $50-250 upfront and produces stale data. Most users have 5-15 active commanders. Lazy generation is 50x cheaper and stays current.
- **Alternatives:** Pre-compute all (rejected: cost), pre-compute top 100 (rejected: still wasteful).

### ADR-002: SQLite over Postgres
- **Decision:** SQLite as the primary database.
- **Rationale:** Single-user system has no concurrency requirements. Embedded simplifies deployment.
- **Alternatives:** Postgres (operational complexity), DuckDB (weaker ecosystem for this use).

### ADR-003: numpy Cosine Similarity over Vector Database
- **Decision:** Embeddings as numpy arrays in SQLite BLOBs; query via cosine similarity in Python.
- **Rationale:** Reference layer is small (<10K chunks). Vector DBs add overhead with no payoff.
- **Alternatives:** pgvector (requires Postgres), Chroma (separate process), Pinecone (paid).

### ADR-004: Three-Call LLM Pattern with Aggressive Caching
- **Decision:** Split LLM reasoning into profile, fit, and synthesis calls. Cache profile and reference chunks.
- **Rationale:** Single mega-prompt is too expensive and dilutes attention. Three-call with caching achieves ~94% cost reduction.
- **Alternatives:** Single call (cost), no caching (cost), 4+ calls (latency).

### ADR-005: Triangulated Evidence over Card-Text-Only
- **Decision:** Profile generation fuses card text, inclusion data, and community discussion.
- **Rationale:** Each source has blind spots. Triangulation is professional analytics methodology.
- **Alternatives:** Card text only (misses discovered synergies), inclusion data only (lags innovation).

### ADR-006: Reference Layer RAG over Pure LLM Reasoning
- **Decision:** Ground LLM reasoning in retrieved chunks from official rules and frameworks.
- **Rationale:** Without grounding, LLMs hallucinate mechanically invalid synergies.
- **Alternatives:** Pure reasoning (hallucination risk), fine-tuning (cost and complexity).

### ADR-007: No YouTube Data Acquisition
- **Decision:** Exclude YouTube event scraping from architecture.
- **Rationale:** Reliable extraction requires manual validation, violating "no manual input." TopDeck.gg + EDHREC + Moxfield provide sufficient signal.
- **Alternatives:** Manual labeling (rejected), CV-only pipeline (validation gap).

### ADR-008: Mac mini Self-Hosted over Cloud
- **Decision:** Run entirely on user's existing Mac mini.
- **Rationale:** Single-user. Cloud adds cost with no benefit. Mac mini already paid for.
- **Alternatives:** AWS/GCP (cost), Raspberry Pi (insufficient for embeddings).

### ADR-009: Layered Architecture over Microservices
- **Decision:** Single Python application with module boundaries.
- **Rationale:** Monolith is simpler, faster, cheaper for single-user systems.
- **Alternatives:** Microservices (complexity), event-driven (no fanout).

### ADR-010: Quarterly Refresh Cadence
- **Decision:** Align reference and profile refresh with Magic set releases.
- **Rationale:** Magic releases sets quarterly. This cadence captures relevant change with minimal redundancy.
- **Alternatives:** Continuous (cost), annual (stale during set windows).

### ADR-011: Haiku for Per-Card Fit, Sonnet for Synthesis
- **Decision:** Cheapest model for high-volume well-bounded tasks; Sonnet for nuanced synthesis.
- **Rationale:** ~12x cost difference. Haiku handles per-card fit adequately; Sonnet's marginal capability is worth cost only for synthesis.
- **Alternatives:** Sonnet for everything (cost), Haiku for everything (profile quality).

### ADR-012: Profile Caching with Set-Version Invalidation
- **Decision:** Cache profiles indefinitely; invalidate when new sets affect them via Haiku relevance screen.
- **Rationale:** Profiles are expensive to generate but stable across long periods. Cheap relevance screening is cost-optimal.
- **Alternatives:** TTL expiration (arbitrary), no caching (cost).

### ADR-013: magicthegathering.io for Rulings Only, Scryfall for Cards
- **Decision:** Scryfall as canonical card source. mtgapi only for `rulings` data, monthly cadence.
- **Rationale:** Scryfall provides bulk download, pricing, faster set updates, richer representation. mtgapi has rate limits and pagination making it impractical as primary. mtgapi's value is inline rulings, which Scryfall doesn't include in bulk.
- **Alternatives:** mtgapi as primary (rate limits), skip mtgapi (lose ruling-grounded reasoning), Scryfall rulings endpoint (per-card calls; equivalent effort).

### ADR-014: Rulings Joined by oracle_id
- **Decision:** Foreign key from `card_rulings` to `cards.oracle_id`, not `cards.id`.
- **Rationale:** Rulings apply to mechanical identity (oracle text), not specific printing. Reprints share rulings.
- **Alternatives:** Join by card_id with duplication (storage waste, update complexity).

</context>

---

## 12. Future Evolution

<context name="future_evolution">

Pre-considered V2 increments. Build only after V1 is validated through use.

```yaml
v2_candidates:
  - name: goldfish_simulation
    description: "Fork auto-goldfish for consistency simulation"
    effort: "~2 weeks"
    cost_impact: "minimal (CPU only)"
    triggers: "If sim calibration needed for confidence in scoring"

  - name: multi_player_simulation
    description: "Extend goldfish to 4-player with simple interaction heuristics"
    effort: "~4 weeks"
    cost_impact: "minimal"
    dependencies: ["goldfish_simulation complete"]

  - name: user_pod_simulation
    description: "Auto-generate optimized opponent decks for user-specified pod"
    effort: "~2 weeks beyond multi-player"
    cost_impact: "minimal"
    value: "Killer feature - estimate bracket vs your specific pod"

  - name: forge_integration
    description: "Replace custom goldfish with Forge's full rules engine"
    effort: "~6 weeks"
    complexity: "high"
    triggers: "Only if custom goldfish proves insufficient"
```

</context>

---

## 13. Architectural Stability Statement

<context name="stability">

The 7-layer architecture is intended to be stable. Future increments add modules within existing layers or extend the pipeline with new stages — they do not require architectural rewrites.

The most likely architectural change: if SQLite performance becomes a bottleneck (unlikely at expected volumes), migrate to Postgres + pgvector. This is localized to the Data Layer and does not affect higher layers.

</context>
