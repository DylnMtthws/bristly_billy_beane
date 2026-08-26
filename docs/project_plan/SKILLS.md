# SKILLS.md

<instructions>
This file defines recurring tasks and workflows for the Sabermetrics project. When a user request matches one of these skills, follow its workflow exactly. Each skill is self-contained and includes inputs, steps, validation criteria, and output expectations.

If a request doesn't match a defined skill, fall back to general implementation guided by CLAUDE.md and design.md.
</instructions>

---

## Skill Index

```yaml
skills:
  - id: SKILL-001
    name: add_data_source
    when: "User requests integration with a new external data source"

  - id: SKILL-002
    name: add_scoring_metric
    when: "User requests a new card-level or deck-level scoring metric"

  - id: SKILL-003
    name: add_llm_prompt
    when: "User requests new LLM-driven reasoning capability"

  - id: SKILL-004
    name: profile_a_commander
    when: "User requests a commander profile generation"

  - id: SKILL-005
    name: build_a_deck
    when: "User requests deck generation"

  - id: SKILL-006
    name: refresh_for_set_release
    when: "New Magic set has released and system needs updating"

  - id: SKILL-007
    name: schema_migration
    when: "Database schema needs additive change"

  - id: SKILL-008
    name: cost_audit
    when: "User asks about spend or wants to optimize cost"

  - id: SKILL-009
    name: debug_recommendation_quality
    when: "Generated deck/profile quality is suspect"

  - id: SKILL-010
    name: add_synergy_rule
    when: "User identifies a pattern that should be hand-curated"
```

---

## SKILL-001: Add a New Data Source

<context name="skill_001">

### Inputs Required
- Source URL or API documentation
- Auth requirements (API key, OAuth, none)
- Rate limit constraints
- What data the source provides
- Refresh cadence (daily/weekly/monthly/quarterly/on-demand)
- Whether it's primary, supplementary, or fallback

### Workflow

```yaml
steps:
  1_classify_source:
    actions:
      - "Identify which existing source(s) it overlaps with"
      - "Determine if it's PRIMARY (replaces another), SUPPLEMENTARY (adds new field), or REDUNDANCY (fallback for existing)"
      - "If overlap with existing, document the role distinction in CLAUDE.md external_data_sources block"

  2_create_ingestion_module:
    location: "src/ingestion/<source_name>.py"
    template: "Inherit from src/ingestion/base.IngestionSource"
    required_methods:
      - "is_available() -> bool"
      - "last_updated() -> datetime"
      - "sync(full: bool = False) -> SyncResult"
    rate_limiting: "Use src/utils/rate_limit.py decorators; default 1 req/sec for unfamiliar sources"
    error_handling: "Wrap in DegradableError for source failures"

  3_define_schema:
    actions:
      - "Add table(s) to schema.md"
      - "Generate migration in scripts/migrations/"
      - "Add Pydantic models in src/models/"
      - "Update foreign keys to existing tables"

  4_register_in_config:
    file: "config/settings.yaml"
    location: "refresh: section"
    add: "<source>_<cadence>: true"

  5_add_scheduled_job:
    if_cadence: "daily, weekly, monthly, or quarterly"
    actions:
      - "Create or update scripts/<cadence>_refresh.py to call new ingestion"
      - "If new cadence not yet supported, create launchd/<cadence>.plist"

  6_add_failure_mode:
    file: "design.md"
    section: "10.2 Per-Source Fallback Matrix"
    add: "Row for new source describing degraded behavior"

  7_test:
    create: "tests/ingestion/test_<source_name>.py"
    minimum_tests:
      - "Smoke test: is_available() returns True"
      - "Parse test: sample response parses into models"
      - "Error test: 4xx/5xx responses raise DegradableError"
```

### Validation Criteria
- New ingestion module follows IngestionSource interface
- Schema additions are additive only (no destructive changes)
- Rate limiting respects source constraints
- Failure does not block other ingestion
- CLAUDE.md `external_data_sources` block updated

### Cost Impact
Should be $0 unless source is paid. If paid, requires ADR documenting why cost is justified.

</context>

---

## SKILL-002: Add a New Scoring Metric

<context name="skill_002">

### Inputs Required
- Metric name and what it measures
- Inputs the metric requires (which data tables)
- Output type and range (e.g., 0.0-1.0 normalized score)
- Whether it's per-card or per-deck

### Workflow

```yaml
steps:
  1_specify_metric:
    actions:
      - "Define formula or algorithm in plain language"
      - "Identify all inputs from existing tables"
      - "Define normalization range"
      - "Document expected behavior at boundary cases"

  2_implement_in_analytics:
    location: "src/analytics/<metric_name>.py"
    type: "Pure function or class with stateless methods"
    signature_pattern: |
      def compute_<metric>(
          card: Card | None,
          deck: Deck | None,
          context: ScoringContext
      ) -> MetricResult:
          ...

  3_add_to_pipeline:
    if_per_card: "Integrate into src/pipeline/deck_builder.py Stage 5"
    if_per_deck: "Integrate into src/pipeline/deck_builder.py Stage 8"
    update: "src/models/deck.py to include new score field"

  4_expose_in_output:
    actions:
      - "Add to deck output schema in schema.md"
      - "Update Flask UI deck view to display"
      - "Update CLI text output to include"

  5_calibrate:
    actions:
      - "Run on 5 known reference decks"
      - "Verify output matches intuition"
      - "Adjust normalization if needed"

  6_test:
    create: "tests/analytics/test_<metric_name>.py"
    minimum_tests:
      - "Unit test: known input -> known output"
      - "Boundary test: empty/extreme inputs handled gracefully"
      - "Integration test: integrates with deck_builder pipeline"
```

### Validation Criteria
- Metric is deterministic (same input → same output)
- Runs in <100ms per card or <1s per deck
- No external dependencies (operates on local data only)
- Output is observable (cited in deck output)
- Documented in schema.md scoring_metrics section

### Cost Impact
Zero. All scoring is local computation.

</context>

---

## SKILL-003: Add a New LLM Prompt

<context name="skill_003">

### Inputs Required
- Purpose of the LLM call (what reasoning task)
- Inputs to the prompt (which data structures)
- Expected output schema (Pydantic model)
- Model choice (Haiku for bounded tasks, Sonnet for synthesis)
- Caching strategy

### Workflow

```yaml
steps:
  1_write_prompt:
    location: "src/reasoning/prompts/<task_name>.txt"
    structure:
      system_section: "Role, output format, evaluation criteria"
      cached_section: "Reusable context (mark with {cache_breakpoint})"
      variable_section: "Per-call inputs"
    requirements:
      - "Output JSON specification at end of prompt"
      - "Schema reference inline"
      - "Examples if behavior is non-obvious"
      - "Explicit instruction to cite sources"

  2_define_output_schema:
    location: "src/models/<output_type>.py"
    type: "Pydantic v2 BaseModel"
    requirements:
      - "All fields typed"
      - "Optional fields explicitly Optional"
      - "Validation rules in field_validator decorators"

  3_implement_caller:
    location: "src/reasoning/<task_name>.py"
    pattern: |
      def call_<task>(inputs: <InputModel>) -> <OutputModel>:
          prompt = load_prompt("<task_name>.txt")
          composed = compose_prompt(prompt, inputs, cache_breakpoints=[...])
          response = anthropic_client.call_with_cache(
              model="claude-haiku-4-5",  # or sonnet per ADR-011
              system=composed.system,
              messages=composed.messages,
              cache_breakpoints=composed.cache_indices
          )
          parsed = <OutputModel>.model_validate_json(response.content)
          return parsed

  4_register_cost_tracking:
    actions:
      - "Anthropic client wrapper auto-tracks cost; no manual logging needed"
      - "If new model added, update CLAUDE.md llm.models block"

  5_test:
    create: "tests/reasoning/test_<task_name>.py"
    minimum_tests:
      - "Mock Anthropic response, verify parsing"
      - "Validate prompt template renders correctly"
      - "Verify schema validation catches malformed responses"
    skip: "Live LLM calls (costs money; reserve for manual validation)"

  6_validate_quality:
    method: "Manual sanity check on 5 representative inputs"
    document: "Save outputs to tests/fixtures/llm_outputs/ for regression"
```

### Validation Criteria
- Prompt is in `src/reasoning/prompts/` as a `.txt` file (not embedded in Python)
- Output is validated against a Pydantic schema
- All calls go through `src/reasoning/client.py` wrapper
- Cache breakpoints are explicit in the call
- Cost per call is documented

### Cost Impact
Document expected cost per call in the docstring. If new prompt's cost exceeds $0.05, requires explicit justification.

</context>

---

## SKILL-004: Profile a Commander

<context name="skill_004">

### Inputs Required
- Commander name (string, will be resolved to commander_id)
- Optional: user_intent (string describing unconventional strategy)
- Optional: force_refresh (bool, bypass cache)

### Workflow

```yaml
steps:
  1_resolve_commander:
    action: "Look up commander_id from cards table by name"
    error_if: "Not found, ambiguous match without exact name"

  2_check_cache:
    action: "Query commander_profiles by commander_id + user_intent_hash"
    decision_tree:
      - if: "Hit AND not force_refresh AND set_version current"
        then: "Return cached profile"
      - if: "Hit BUT set_version stale"
        then: "Run relevance screen (Haiku); regenerate only if affected"
      - if: "Miss"
        then: "Generate from scratch"

  3_aggregate_evidence:
    parallel_fetches:
      - source: cards
        get: "card_data, oracle_text, color_identity, keywords"
      - source: card_rulings
        get: "All rulings for commander's oracle_id"
      - source: edhrec_commander_data
        get: "themes, top_cards, salt_score, deck_count"
      - source: tournament_results
        get: "win_rate, sample_size for commander"
      - source: reddit
        get: "Top 20 threads in last 6 months"
        rate_limit: "1 req/sec"

  4_retrieve_reference_chunks:
    action: "Query reference_chunks via cosine similarity"
    query_construction: "card_oracle_text + 'archetype strategies'"
    top_k: 8

  5_compose_prompt:
    template: "src/reasoning/prompts/profile_synthesis.txt"
    cache_breakpoints:
      - "After reference_chunks (cacheable across many profile calls)"
      - "After card_data (cached per commander)"

  6_call_llm:
    model: claude-sonnet-4-6
    estimated_cost: "$0.30-0.40"
    estimated_time: "15-25s"

  7_validate_and_persist:
    actions:
      - "Validate response against CommanderProfile Pydantic model"
      - "Reject and retry once with stricter format if validation fails"
      - "Write to commander_profiles table with set_version, generated_at"

  8_return:
    type: CommanderProfile
```

### Validation Criteria
- Output passes CommanderProfile schema validation
- Profile includes ALL required fields (game_plan, win_conditions, build_paths, anti_synergies, power_indicators)
- Citations are present (rules_chunks_referenced, articles_referenced)
- Generation completes in <60 seconds
- Cost stays under $0.50

### Failure Modes
- Reddit fails: continue without cultural stream, mark in evidence_sources
- EDHREC fails: continue with text + community only, mark profile as text_dominant
- Anthropic fails: return DegradableError with clear message, do not write to cache

</context>

---

## SKILL-005: Build a Deck

<context name="skill_005">

### Inputs Required
- Commander name (resolved to commander_id)
- Budget USD (float)
- Power target (1-5 bracket)
- Optional: strategy override (string)
- Optional: weights override (CVAR weight dict)
- Optional: user_intent (passed to profiler if profile cache miss)

### Workflow

```yaml
steps:
  1_validate_request:
    checks:
      - "Commander exists and is legal"
      - "Budget > 0"
      - "Power target in [1, 5]"
      - "Weights sum to 1.0 if provided"

  2_acquire_profile:
    action: "Call SKILL-004 (profile_a_commander)"
    expected_time: "<100ms (cache hit) or 15-60s (cache miss)"

  3_filter_candidates:
    operations:
      - "src/analytics/filters.py: by_color_identity"
      - "by_legality (Commander format)"
      - "by_singleton_legal"
      - "by_budget (per-card cap = budget * 0.15)"
    expected_output: "~3,000 candidates from ~25,000 cards"

  4_embedding_score:
    action: "Cosine similarity vs profile.strategic_profile narrative"
    keep_top_n: 200

  5_structural_score:
    parallel_metrics:
      - "src/analytics/cvar.compute_cvar(card, profile, weights)"
      - "src/analytics/card_win_equity.compute_cwe(card, commander_id)"
      - "src/analytics/card_demand.compute(card)"
      - "src/analytics/cooccurrence.score_against_profile_top_cards(card)"
    composite_score: "Weighted sum"
    keep_top_n: 50

  6_llm_fit_reasoning:
    action: "Call SKILL-003 (Haiku card_fit prompt) for each of 50"
    parallelism: "Up to 5 concurrent calls (rate limit safety)"
    expected_cost: "~$0.05 total"
    expected_time: "10-15s"

  7_assemble_99_cards:
    action: "src/pipeline/slot_assigner.fill_slots(candidates, target_composition)"
    target_composition_lookup: "Based on profile + power_target"
    constraints:
      - "Exactly 99 non-commander cards"
      - "Mana base requirements met (lands, fixers)"
      - "Singleton enforced"
      - "Budget total respected"

  8_synthesize_narrative:
    action: "Call Sonnet deck_synthesis prompt"
    expected_cost: "~$0.05"

  9_classify:
    action: "src/analytics/brackets.classify_deck(deck, game_changers)"
    output: "Estimated bracket 1-5"

  10_persist_and_return:
    actions:
      - "Write to generated_decks table"
      - "Format output (JSON, text, Moxfield-importable per config)"
      - "Compute alternatives per slot"
      - "Return GeneratedDeck"
```

### Validation Criteria
- Exactly 99 unique cards + 1 commander
- All cards in commander's color identity
- Total price ≤ budget USD
- Each card has cvar_score, fit reasoning, slot_role
- Deck has narrative (game_plan, key_synergies, weaknesses, play_pattern)
- Bracket classification with reasoning
- Generation completes in <30s with cached profile
- Cost ≤ $0.15 with cached profile

</context>

---

## SKILL-006: Refresh for Set Release

<context name="skill_006">

### Trigger
- New Magic set detected via Scryfall (set_code never seen before)
- Manual: `python -m sabermetrics refresh-set <SET_CODE>`

### Workflow

```yaml
steps:
  1_full_card_refresh:
    action: "Run scryfall.sync(full=True)"
    expected_runtime: "~10 min"

  2_check_rules_updates:
    action: "Fetch latest Comprehensive Rules; compare version to cache"
    if_changed:
      - "Re-chunk rules document"
      - "Re-embed via sentence-transformers"
      - "Replace reference_chunks rows where document='comprehensive_rules'"

  3_check_keyword_glossary:
    action: "Identify new keywords in new set"
    if_new_keywords: "Add to keyword glossary; re-embed affected chunks"

  4_refresh_banned_list:
    action: "Fetch from mtgcommander.net; update if changed"

  5_per_profile_relevance_screen:
    for_each: "commander_profiles where is_stale = false"
    actions:
      - "Filter new_set_cards by commander.color_identity"
      - "If no relevant cards: skip"
      - "If any relevant: call relevance_screen Haiku prompt"
      - "Cost: ~$0.001 per screen"
      - "If any card flagged as affecting strategy: mark profile is_stale = true"

  6_lazy_regeneration:
    action: "Do not regenerate stale profiles immediately"
    rationale: "User will trigger regeneration next time they request profile"
    cost_savings: "Avoid regenerating profiles for inactive commanders"

  7_log_summary:
    output:
      - cards_added: int
      - rules_updated: bool
      - profiles_screened: int
      - profiles_invalidated: int
      - total_cost_usd: float
```

### Validation Criteria
- All new set cards present in cards table
- Reference layer reflects latest rules
- Profile invalidations are accurate (spot-check 3 invalidated profiles)
- Total cost <$5 per set release
- Total runtime <2 hours

</context>

---

## SKILL-007: Schema Migration

<context name="skill_007">

### Inputs Required
- Description of schema change
- Whether it's additive (allowed) or destructive (forbidden)

### Workflow

```yaml
steps:
  1_classify_change:
    additive_examples: ["new column with default", "new table", "new index"]
    forbidden_examples: ["DROP COLUMN", "RENAME without backward compat", "data type narrowing"]
    if_destructive: "REJECT. Propose additive alternative (e.g., new column instead of renamed column)"

  2_generate_migration:
    location: "scripts/migrations/<NNN>_<description>.py"
    template:
      - "Numbered sequentially (001_, 002_, ...)"
      - "Idempotent (safe to re-run)"
      - "Wrapped in transaction"
    record: "INSERT into _schema_version table"

  3_update_schema_md:
    action: "Update schema.md to reflect new shape"
    keep_history: "Note version in change log"

  4_update_pydantic_models:
    location: "src/models/"
    action: "Add new fields with appropriate Optional/default values"
    backward_compat: "Old data without new field must still parse"

  5_update_consuming_code:
    action: "Find usages of affected tables, update to handle new fields"

  6_test:
    action: "Run migration against fresh DB and against existing DB"
    verify: "Both succeed; data integrity preserved"
```

### Validation Criteria
- No destructive changes
- Migration is idempotent
- Old code paths still work (backward compatibility)
- schema.md updated
- Tested against both fresh and existing databases

</context>

---

## SKILL-008: Cost Audit

<context name="skill_008">

### Workflow

```yaml
steps:
  1_pull_cost_log:
    query: |
      SELECT
        call_type,
        model,
        SUM(cost_usd) as total,
        COUNT(*) as count,
        AVG(cost_usd) as avg
      FROM cost_log
      WHERE timestamp > date('now', '-30 days')
      GROUP BY call_type, model
      ORDER BY total DESC

  2_identify_top_spenders:
    threshold: "Calls or call types representing >20% of monthly spend"

  3_check_cache_hit_rates:
    query: "Compare cached_input_tokens vs input_tokens by call_type"
    target_cache_rates:
      profile_synthesis: ">90%"
      card_fit_scoring: ">95%"
      deck_synthesis: "<10% (single calls, not cacheable)"

  4_propose_optimizations:
    common_optimizations:
      - "Move uncached prompt content into cached section"
      - "Reduce candidate pool size for fit scoring (currently 50)"
      - "Switch model from Sonnet to Haiku where quality permits"
      - "Increase cache TTL where data is stable"

  5_project_annual:
    formula: "(monthly_spend * 12) vs annual_target"
    if_over_target: "Implement optimizations before continued use"
```

### Output Format

```yaml
audit_report:
  period: "Last 30 days"
  total_spend_usd: float
  by_call_type: dict
  cache_hit_rates: dict
  top_spenders: list
  projected_annual: float
  vs_target: "under | at | over"
  recommendations: list
```

</context>

---

## SKILL-009: Debug Recommendation Quality

<context name="skill_009">

### Trigger
- User reports a generated deck or profile feels wrong
- Recommendations include obviously inappropriate cards
- Anti-synergies are missed
- Power level estimate is wildly off

### Workflow

```yaml
steps:
  1_capture_specifics:
    questions:
      - "What command was run?"
      - "What output was unexpected?"
      - "What was the expected output?"
      - "Is this commander cached? When was profile generated?"

  2_inspect_profile:
    action: "Load profile JSON; verify it captures the actual archetype"
    common_issues:
      - "Profile primary_archetype wrong → regenerate with user_intent override"
      - "Anti-synergies missing → check evidence_sources; may need stronger reference grounding"
      - "Stale set_version → invalidate and regenerate"

  3_inspect_pipeline_stage_outputs:
    actions:
      - "Re-run with --debug flag"
      - "Inspect candidates after each pipeline stage"
      - "Identify where wrong cards entered or right cards were filtered out"

  4_check_data_freshness:
    queries:
      - "When was last Scryfall sync?"
      - "When was last EDHREC sync for this commander?"
      - "Are there commander-specific decks in decks table?"

  5_check_reference_layer:
    action: "Query reference_chunks for relevant rules"
    if_missing: "Reference layer may need archetype article scraped"

  6_remediate:
    common_remediations:
      - "Force profile regeneration"
      - "Add hand-curated synergy_rule for missing pattern"
      - "Update game_changers list if power-level wrong"
      - "Refresh data sources"
      - "Adjust CVAR weights if structural scoring is off"

  7_document:
    if_systematic_issue:
      - "Add test case to tests/quality/"
      - "If prompt issue, iterate on prompt template"
      - "If algorithm issue, file in build_plan.md known_issues"
```

</context>

---

## SKILL-010: Add a Synergy Rule

<context name="skill_010">

### Trigger
- User identifies a mechanical synergy not captured by current rules
- Embedding similarity misses an important pattern
- Co-occurrence data doesn't surface the relationship

### Workflow

```yaml
steps:
  1_specify_pattern:
    required_fields:
      - id: "Snake_case identifier"
      - trigger: "What card characteristic activates this rule"
      - payoff: "What card characteristic the trigger pairs with"
      - strength: "Float 0.0-1.0"
      - description: "Human-readable explanation"

  2_express_in_yaml:
    location: "config/synergy_rules.yaml"
    template: |
      - id: <id>
        trigger:
          text_contains: ["pattern1", "pattern2"]   # Optional
          type_includes: ["Creature"]               # Optional
          keywords: ["flash"]                       # Optional
        payoff:
          text_contains: [...]
          cmc_range: [1, 3]                          # Optional
        strength: 0.85
        description: "Human-readable"

  3_test_pattern:
    actions:
      - "Run rule against 5 known cards that should match trigger"
      - "Run against 5 known cards that should match payoff"
      - "Verify expected pairings are detected"

  4_integrate:
    action: "Rules are loaded at runtime by src/analytics/synergy.py"
    no_code_change_required: true

  5_validate_in_pipeline:
    action: "Generate a deck for a commander where this synergy should fire"
    verify: "Recommendation includes the expected pairing"
```

### Quality Bar
- Rule should be mechanically meaningful, not just thematic
- Strength reflects how reliably the synergy works in practice
- Description explains the strategic logic, not just the pattern

</context>

---

## Skill Registry Maintenance

<instructions>
When you add a new recurring task pattern that doesn't fit an existing skill, add it as SKILL-NNN to this file. Update the Skill Index at the top.

When refining an existing skill, do not delete the old version. Append a "## Revision History" section if the workflow has materially changed.
</instructions>
