# Sabermetrics v2 Architecture Specification

**Document Type:** Major Version Update Specification
**Status:** Proposed; awaiting implementation review
**Supersedes:** Original 7-document spec (v1) where conflicts exist
**Audience:** Claude Code, after reading existing v1 specs

<instructions>
This document specifies a significant architectural evolution from the v1 design. It is NOT a clean-slate redesign — it builds on v1's foundation while restructuring the deck construction pipeline. Read the original v1 docs (CLAUDE.md, design.md, schema.md, api_contracts.md, prompts.md, SKILLS.md, build_plan.md) before reading this document. Then read this document to understand what's changing and why.

If anything in this document conflicts with v1 specs without explicitly saying "this supersedes v1," that's a bug. Surface it.
</instructions>

---

## 1. Why This Update Exists

<context name="motivation">

The v1 architecture established a sound foundation: layered architecture, lazy commander profile generation, three-call LLM pattern with caching, evidence triangulation, RAG-grounded reasoning, and structural analytics for filtering. Building this revealed several gaps:

**Gap 1: Value-inversion commanders produce bland recommendations.**
Commanders like Arcades, Eriette, Doran, and similar archetypes invert conventional Magic value heuristics — they make normally-weak cards strong (defenders, auras, creatures with downsides). The current profile generation doesn't reliably articulate these inversions, so downstream fit scoring evaluates cards against generic heuristics rather than commander-specific value frameworks.

**Gap 2: Card values are static, but Magic value is contextual.**
A card's value depends on what other cards are in the deck. The 9th aura in an Eriette deck is dramatically more valuable than the 1st because synergies compound. Current fit scoring evaluates each card in isolation against the profile, missing this contextual amplification.

**Gap 3: Deck construction pipeline is monolithic.**
The current pipeline applies expensive LLM reasoning to all 99 deck slots. This is wasteful — the 60-70 "infrastructure" slots (lands, ramp, draw, removal) are dominated by mechanical considerations and don't benefit from synergy reasoning. LLM reasoning should concentrate on the 25-30 "differentiator" slots where commander-specific value lives.

**Gap 4: Probability of drawing into specific cards is unweighted.**
The system can over-value expensive singleton staples that win when present but rarely appear. Esper Sentinel is "the best 1-mana draw enabler" but at 99 cards singleton, it's in your hand maybe 18-22% of the time by turn 6. Three $2 functional alternatives collectively cover that role more reliably at a fraction of the cost — but current scoring doesn't reflect this.

**Gap 5: Reasoning data lacks "why" content.**
Existing data sources answer "which cards" (Scryfall, EDHREC, TopDeck) but not "why this card." The LLM reasoning layer is asked to infer human strategic reasoning from card text, which produces inconsistent quality. Human-articulated reasoning exists in deck primers but isn't currently captured.

These gaps share a root cause: v1 treated deck construction as a single-stage problem with uniform reasoning. v2 restructures it as a staged pipeline where different stages apply different techniques optimized for what they're solving.

</context>

---

## 2. Conversation History Context

<context name="conversation_history">

This specification is the output of an extended design conversation that worked through these issues iteratively. Key decision points along the way:

**Established early:** The original 7-document spec architecture is sound and should be preserved. New work extends it rather than replacing it.

**Decided after exploring "deck tech" research:** Reasoning extraction from primer text is the right way to fill the "why" data gap. NotebookLM is useful for personal research and reference layer content but not for automated ingestion.

**Decided after exploring simulation:** Goldfish execution metrics are more useful than win-rate against benchmark decks. The actual question being asked is "does this deck reliably execute its commander's strategy" not "does it win." Per-commander execution profiles derived from commander profiles are the right abstraction.

**Decided after exploring statistical foundations:** Card value is contextual to deck composition, not a property of the card alone. The architecture must support deck-context-aware scoring rather than static card scoring.

**Decided after exploring search-space pruning:** Templated infrastructure filling is correct, but templates must be commander-aware (derived from profile) and infrastructure must be filled algorithmically (Frank Karsten's mathematics for mana bases) rather than via lookup tables. Tables grow combinatorially; generators stay tractable.

**Decided after exploring land quality tiers:** Premium dual lands (fetches, shocks) provide marginal gameplay improvement at extreme cost. Default to budget conditional lands (check, surveil, pain) and basics. Allow upgrades only for low-CMC decks where untapped speed measurably impacts gameplay.

**The user explicitly invited pushback throughout** and several times changed direction based on identified flaws in earlier proposals. The user values being told when their instinct is wrong, especially when the alternative is presented with reasoning.

</context>

---

## 3. New Architectural Decision Records

<context name="new_adrs">

These ADRs are added to design.md Section 11. Existing ADRs (1-14) are preserved.

### ADR-015: Staged Deck Construction Pipeline

**Decision:** Restructure deck construction from a monolithic LLM-driven process into a staged pipeline that applies different techniques to different slot categories.

**Stages:**
1. Hard filtering (deterministic, free)
2. Role tagging and Pareto filtering (deterministic, free)
3. Template derivation from commander profile (small LLM call)
4. Infrastructure slot filling via algorithmic generators (deterministic, free)
5. Category coverage analysis (deterministic, free)
6. Differentiator slot filling with deck-context-aware fit scoring (LLM, concentrated)
7. Two-pass budget redistribution (deterministic with optional LLM advisory)
8. Deck synthesis (LLM, single call)

**Rationale:** The 60-70 infrastructure slots in any Commander deck are dominated by mechanical considerations (curve, color requirements, format quality) that don't benefit from expensive LLM reasoning. Concentrating LLM reasoning on the 25-30 differentiator slots both reduces cost and improves quality, because the reasoning has fewer slots to focus on.

**Alternatives considered:**
- Maintain monolithic pipeline (rejected: cost and quality both suffer)
- Pure algorithmic deck building (rejected: loses commander-specific reasoning entirely)
- Multi-stage with LLM at every stage (rejected: marginal benefit over targeted LLM application)

### ADR-016: Algorithmic Infrastructure Generation Over Lookup Tables

**Decision:** Build parameterized algorithmic generators for lands, ramp, draw, and removal packages. Do not pre-enumerate combinations as lookup tables.

**Rationale:** The combinatorial space of (color identity × CMC bracket × archetype × budget tier) produces 3,000+ cells. Enumerating this is unmaintainable. Algorithmic generators produce correct outputs for any combination of inputs from a small candidate pool, are testable in isolation, update automatically when new sets release, and produce explainable recommendations.

Frank Karsten's published mathematics on Commander mana bases provides the foundation for the lands generator. The other generators (ramp, draw, removal) follow similar principles encoded as rules.

**Alternatives considered:**
- Lookup tables (rejected: combinatorial explosion, maintenance burden)
- LLM-driven infrastructure selection (rejected: cost not justified for mechanical decisions)
- Hybrid table+algorithm (rejected: complexity without benefit)

### ADR-017: Deck-Context-Aware Fit Scoring

**Decision:** Modify the per-card fit scoring prompt to receive the partial deck composition as context. Cards are evaluated against both the commander profile and the cards already chosen for the deck.

**Rationale:** Card value depends on what other cards are in the deck. The 9th aura in an Eriette deck is more valuable than the 1st because synergies compound. Static card-vs-profile scoring misses this. Deck-context-aware scoring captures combinatorial value emergence at the cost of slightly larger per-call context (and reduced caching efficiency, partially offset by smaller candidate pool from staged pipeline).

**Cost impact:** Approximately 2-3x cost per fit evaluation, but evaluating 25-30 cards instead of 50, net cost approximately equivalent. Quality improvement is significant.

**Alternatives considered:**
- Static card-vs-profile scoring (rejected: misses contextual value)
- Full synergy graph (deferred to v3 if needed; complexity not yet justified)
- Pairwise synergy lookups (rejected: doesn't capture combinatorial value)

### ADR-018: Probability-Weighted Card Valuation

**Decision:** Encode probability of having a card available during gameplay into the scoring system. Use category coverage analysis to favor redundancy in functional categories over premium singletons when budget constraints apply.

**Rationale:** A 99-card singleton format means specific cards have low presence probability. A $50 card present 20% of the time may have lower expected game contribution than three $5 cards collectively present 50%+ of the time. This is the mathematical foundation of budget building: leverage redundancy, avoid premium singletons.

**Alternatives considered:**
- Ignore presence probability (rejected: produces over-priced decks)
- Model presence probability for every card (over-engineered)
- Category-level coverage with redundancy bonuses (selected: captures the principle without per-card complexity)

### ADR-019: Profile-Driven Template Derivation

**Decision:** Deck composition templates (land count, ramp count, role distribution, curve target) are derived from the commander profile, not from global defaults.

**Rationale:** A 7-CMC commander needs more ramp than a 2-CMC commander. An aggro deck needs fewer lands than a control deck. A defender-tribal deck has different role distributions than a creature combat deck. Global defaults force compromise; profile-derived templates produce appropriate baselines.

**Alternatives considered:**
- Global defaults with manual override (rejected: requires user expertise)
- Per-commander hardcoded templates (rejected: doesn't scale)
- Profile-derived templates (selected: leverages existing profile work)

### ADR-020: Value-Inversion Articulation as Profile Requirement

**Decision:** Profile generation prompt (PROMPT-001) is modified to explicitly require articulation of value inversions for commanders that invert conventional Magic heuristics.

**Rationale:** Commanders like Arcades, Eriette, and Doran fundamentally invert value frameworks. Without explicit articulation in the profile, downstream scoring evaluates cards against generic heuristics. By requiring the profile to identify which conventional heuristics this commander inverts, downstream stages have the information needed to evaluate cards against the correct framework.

**Alternatives considered:**
- Per-commander hardcoded value rules (rejected: doesn't scale)
- Implicit profile inference (current state; rejected: unreliable)
- Explicit value-inversion section in profile (selected)

</context>

---

## 4. Schema Changes

<context name="schema_changes">

These additions extend schema.md without breaking v1 schemas.

### 4.1 New Columns on Existing Tables

```sql
-- Add to cards table
ALTER TABLE cards ADD COLUMN role_tags TEXT;          -- JSON array: ['ramp', 'fixing'], ['draw', 'repeatable'], etc.
ALTER TABLE cards ADD COLUMN functional_categories TEXT;  -- JSON array: ['treasure_generation', 'sacrifice_outlet']
ALTER TABLE cards ADD COLUMN tags_extracted_at TIMESTAMP;
ALTER TABLE cards ADD COLUMN tags_extraction_version TEXT;
```

### 4.2 New Tables

```sql
-- Deck primer text from decklist sources
CREATE TABLE deck_primers (
    deck_id TEXT PRIMARY KEY,
    primer_text TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    has_been_extracted BOOLEAN DEFAULT FALSE,
    extracted_at TIMESTAMP,
    extraction_version TEXT,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (deck_id) REFERENCES decks(id)
);
CREATE INDEX idx_primers_extraction_pending 
    ON deck_primers(has_been_extracted) 
    WHERE has_been_extracted = FALSE;

-- Card reasoning extracted from primers
CREATE TABLE card_reasoning_extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id TEXT NOT NULL,
    commander_oracle_id TEXT NOT NULL,
    card_oracle_id TEXT NOT NULL,
    reasoning_text TEXT NOT NULL,
    role_assignment TEXT,
    confidence_signal TEXT,
    extraction_model TEXT NOT NULL,
    extraction_prompt_version TEXT NOT NULL,
    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (deck_id) REFERENCES deck_primers(deck_id),
    FOREIGN KEY (commander_oracle_id) REFERENCES cards(oracle_id),
    FOREIGN KEY (card_oracle_id) REFERENCES cards(oracle_id)
);
CREATE INDEX idx_reasoning_lookup 
    ON card_reasoning_extractions(commander_oracle_id, card_oracle_id);

-- Operational tracking for failed extractions
CREATE TABLE primer_extraction_failures (
    deck_id TEXT PRIMARY KEY,
    failure_reason TEXT NOT NULL,
    attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    retry_count INTEGER DEFAULT 0,
    FOREIGN KEY (deck_id) REFERENCES deck_primers(deck_id)
);

-- Prospects: commanders user has flagged for exploration
CREATE TABLE prospects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commander_oracle_id TEXT NOT NULL,
    user_notes TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    state TEXT NOT NULL DEFAULT 'added',
    enrichment_started_at TIMESTAMP,
    enrichment_completed_at TIMESTAMP,
    last_state_change TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    archived BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (commander_oracle_id) REFERENCES cards(oracle_id)
);
CREATE INDEX idx_prospects_state ON prospects(state) WHERE archived = FALSE;

-- Execution profiles derived from commander profiles
CREATE TABLE execution_profiles (
    commander_oracle_id TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL,
    derived_from_profile_at TIMESTAMP,
    schema_version TEXT DEFAULT '1.0',
    FOREIGN KEY (commander_oracle_id) REFERENCES cards(oracle_id)
);

-- Generated deck quality scores from execution simulation
CREATE TABLE deck_execution_scores (
    generated_deck_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    measured_value REAL,
    target_value REAL,
    target_met BOOLEAN,
    sample_size INTEGER,
    PRIMARY KEY (generated_deck_id, metric_name),
    FOREIGN KEY (generated_deck_id) REFERENCES generated_decks(id)
);
```

### 4.3 Configuration File Additions

```yaml
# config/karsten_mana_base.yaml
# Frank Karsten's mathematical tables for mana base requirements
# Sources cited in inline comments

color_source_requirements:
  # Format: cmc_turn -> required_sources_for_each_color_pip
  # Example: 3-CMC double-pip spell on turn 3 needs 14 sources of that color
  3:
    single_pip: 12
    double_pip: 14
    triple_pip: 16
  4:
    single_pip: 11
    double_pip: 14
    triple_pip: 16
  # ... full table

land_count_targets:
  # Format: average_cmc -> target_land_count
  # Source: Karsten's analysis of optimal land counts
  2.5: 35
  3.0: 36
  3.5: 38
  4.0: 39
  4.5: 40
  5.0+: 41

# config/auto_include_cards.yaml
# Cards that should always appear when in color identity and budget allows

auto_includes:
  any_deck:
    - "Sol Ring"
  multicolor:
    - "Command Tower"
    - "Arcane Signet"
    - "Path of Ancestry"
  any_three_color_or_more:
    - "Exotic Orchard"
  black_color_identity:
    - "Bojuka Bog"
  high_cmc_decks:
    cmc_threshold: 4.0
    cards:
      - "Reliquary Tower"
```

</context>

---

## 5. Pipeline Architecture

<context name="pipeline_architecture">

The deck builder pipeline (src/pipeline/deck_builder.py) is restructured into 8 explicit stages.

### Stage 1: Hard Filters (Deterministic)

**Input:** Full card pool (~25,000 cards)
**Output:** ~3,000-5,000 candidates

Filters applied:
- Color identity match
- Format legality (Commander)
- Format banned list
- Budget cap per card (no card exceeding 15-20% of budget)
- Singleton legality

### Stage 2: Role Tagging and Pareto Filtering (Deterministic)

**Input:** ~3,000-5,000 candidates  
**Output:** ~2,000 candidates

Operations:
- Tag each card with its functional roles (ramp, draw, removal, wincon, synergy, utility, land)
- Within each role category, identify Pareto frontier: cards that are dominated by another card on both price and quality are removed

Note: Role tagging is a one-time pass over the entire card pool, not run per generation. The output is stored in the cards.role_tags column. This stage just filters by pre-tagged roles.

### Stage 3: Template Derivation from Profile (Small LLM Call)

**Input:** Commander profile  
**Output:** Deck composition template

Template specifies:
- Land count target
- Ramp count target  
- Draw count target
- Removal count target
- Board wipe count target
- Creature density target
- Synergy/differentiator slot count
- Average CMC target
- Curve shape (cards per CMC bucket)

Derivation:
- Some values come from formulas (land count from Karsten's tables given target CMC)
- Some values come from a small Haiku call that takes the profile and outputs structured template parameters

### Stage 4: Infrastructure Slot Filling (Algorithmic Generators)

**Input:** Template + remaining budget  
**Output:** ~60-70 cards filled (lands, ramp, draw, removal, board wipes)

Generators:
- `LandPackageGenerator`: Karsten-mathematics-based mana base
- `RampPackageGenerator`: Rule-based ramp selection
- `DrawPackageGenerator`: Rule-based card advantage selection
- `RemovalPackageGenerator`: Rule-based interaction selection

Each generator is parameterized and deterministic. No LLM calls.

### Stage 5: Category Coverage Analysis (Deterministic)

**Input:** Partial deck (~60-70 cards) + remaining slots + commander profile  
**Output:** Required and recommended functional categories for remaining slots

Analysis:
- What functional categories does the commander reward? (from profile)
- Which are already covered by infrastructure?
- Which need to be filled by differentiator slots?
- For categories needing redundancy (per ADR-018), how many slots should they consume?

Output is a list of "slot intents": "Need 4 cards in 'aura' category," "Need 3 cards in 'sacrifice outlet' category," etc.

### Stage 6: Differentiator Slot Filling with Deck-Context-Aware Fit Scoring (Concentrated LLM)

**Input:** Partial deck + slot intents + budget remaining  
**Output:** ~25-30 cards filled (synergy pieces, win conditions, commander-specific tech)

For each remaining slot:
1. Pull candidates matching the slot intent (functional category)
2. Filter by remaining budget
3. Score top N candidates with deck-context-aware fit prompt (PROMPT-002 modified)
4. Select top scorer; add to deck
5. Update partial deck and remaining budget

The fit prompt receives the partial deck composition as context. Cards are evaluated against both the commander profile and the cards already chosen.

### Stage 7: Two-Pass Budget Redistribution (Deterministic with Optional LLM Advisory)

**Input:** Complete 99-card deck + budget remaining  
**Output:** Optimized deck within budget

If budget remains after initial fill:
- Identify slots where upgrading the card meaningfully improves synergy
- Reallocate budget to those slots
- Replace lower-tier picks with higher-tier alternatives in upgrade-worthy slots

If budget has been exceeded:
- Identify slots where downgrading minimally impacts synergy
- Replace with cheaper alternatives until under budget

### Stage 8: Deck Synthesis (Single LLM Call)

**Input:** Final 99-card deck + profile  
**Output:** Narrative analysis (game plan, synergies, weaknesses, play patterns)

This stage is essentially unchanged from v1.

</context>

---

## 6. New Module Specifications

<context name="modules">

### 6.1 src/analytics/role_tagger.py

One-time pass that adds role tags to all cards.

```python
def tag_card_roles(card: Card) -> RoleTagResult:
    """
    Identify functional roles for a card.
    
    Roles include: ramp, fixing, draw, removal, board_wipe, tutor,
    recursion, protection, threat, wincon, utility, land, synergy
    
    A card may have multiple roles. Returns list of role tags plus
    functional categories (treasure_generation, sacrifice_outlet, etc.)
    
    Implementation: Pattern matching on oracle text + type line.
    Uses regex patterns and keyword detection.
    No LLM calls; deterministic.
    """
```

This runs once per card, output stored in cards.role_tags. Re-run only when new sets release.

### 6.2 src/analytics/karsten.py

Frank Karsten's mathematical tables encoded as a library.

```python
def required_sources(cmc_turn: int, pip_count: int) -> int:
    """
    Returns the number of land sources of a color needed to hit 90%
    probability of casting a spell with this CMC and pip requirement
    on its CMC turn.
    
    Source: Frank Karsten's hypergeometric distribution analysis.
    """

def target_land_count(average_cmc: float) -> int:
    """
    Returns the recommended land count for a deck with this average CMC.
    Source: Karsten's optimization analysis.
    """

def color_requirements_for_deck(deck_partial: list[Card]) -> dict[Color, int]:
    """
    Given a partial deck, computes the minimum required sources per color
    to satisfy all spells' casting requirements at confidence threshold.
    """
```

### 6.3 src/pipeline/generators/lands.py

```python
class LandPackageGenerator:
    def generate(
        self,
        color_identity: list[Color],
        target_land_count: int,
        average_cmc: float,
        budget: float,
        archetype: str,
        already_placed_lands: list[Card] = None
    ) -> list[Card]:
        """
        Generate a lands package satisfying:
        - Color requirements per Karsten's tables
        - Target land count
        - Budget constraint (default to budget tier; upgrade only if low CMC)
        - Auto-includes when in color identity (Command Tower, Bojuka Bog, etc.)
        
        Uses constraint optimization: minimize tapped-land probability
        weighted by turn cost, subject to color requirements being met.
        
        Candidate pool: basics + budget conditionals (check, surveil, fast,
        pain) + auto-include utility lands. Skips premium duals (fetches,
        shocks) by default per ADR (price/value not justified).
        
        Returns deterministic output for given inputs.
        """
```

### 6.4 src/pipeline/generators/ramp.py

```python
class RampPackageGenerator:
    def generate(
        self,
        color_identity: list[Color],
        commander_cmc: int,
        average_deck_cmc: float,
        target_ramp_count: int,
        budget: float,
        creature_density: float,  # 0.0-1.0
        archetype_speed: str,  # 'aggro', 'midrange', 'control'
        already_placed: list[Card] = None
    ) -> list[Card]:
        """
        Generate a ramp package using these principles:
        
        1. Sol Ring is auto-included always
        2. Arcane Signet auto-included for multicolor
        3. Ramp CMC should be at most (commander_cmc - 2)
        4. Diversify between rocks, land ramp (if green), and dorks
        5. Mana dorks scale with creature density
        6. Land ramp preferred over rocks for graveyard-vulnerable decks
        7. Budget ramp prioritizes efficiency over premium
        
        Returns deterministic output for given inputs.
        """
```

### 6.5 src/pipeline/generators/draw.py

```python
class DrawPackageGenerator:
    def generate(
        self,
        color_identity: list[Color],
        target_draw_count: int,
        budget: float,
        archetype_speed: str,
        commander_creates_advantage: bool,  # True for Arcades, False for Krenko
        already_placed: list[Card] = None
    ) -> list[Card]:
        """
        Generate card advantage package:
        
        1. Repeatable draw weighted higher than one-shot
        2. Conditional draw (when X happens) valued in decks reliably triggering X
        3. Instant-speed draw preferred for control archetypes
        4. Commander-driven draw (Arcades trigger, Eriette ETB, etc.) reduces
           required draw count from this package
        
        Returns deterministic output for given inputs.
        """
```

### 6.6 src/pipeline/generators/removal.py

```python
class RemovalPackageGenerator:
    def generate(
        self,
        color_identity: list[Color],
        target_removal_count: int,
        target_board_wipe_count: int,
        budget: float,
        archetype: str,
        already_placed: list[Card] = None
    ) -> list[Card]:
        """
        Generate interaction package:
        
        1. Mix single-target and board-impact removal
        2. Diversify targeting (creature removal, artifact/enchantment, planeswalker)
        3. Instant-speed valued higher for control; sorcery acceptable for midrange
        4. Avoid color-restricted removal in mixed decks (e.g., Path to Exile in
           white-heavy is fine; Doom Blade in black-light deck is risky)
        
        Returns deterministic output for given inputs.
        """
```

### 6.7 src/pipeline/category_coverage.py

```python
def analyze_category_coverage(
    profile: CommanderProfile,
    partial_deck: list[Card],
    remaining_slots: int,
    remaining_budget: float
) -> list[SlotIntent]:
    """
    Determine what functional categories should fill remaining slots.
    
    Logic:
    1. Profile identifies categories the commander rewards (e.g., "auras",
       "sacrifice_outlets", "ETB_effects")
    2. For each rewarded category, compute current coverage in partial deck
    3. For each undercovered category, determine how many slots it should
       consume (with redundancy bonus per ADR-018)
    4. For categories already covered, mark as "saturated"
    
    Returns SlotIntent objects: structured descriptions of what each
    remaining slot should aim for. Used by Stage 6 to generate candidate
    pools per slot.
    """
```

### 6.8 src/reasoning/template_deriver.py

```python
def derive_deck_template(profile: CommanderProfile, budget: float, power: int) -> DeckTemplate:
    """
    Translate commander profile into deck composition template.
    
    Most fields derive from formulas:
    - target_land_count: Karsten's table given target_avg_cmc
    - target_ramp_count: scaling formula based on commander_cmc and avg_cmc
    
    Some fields require small LLM call to interpret profile:
    - target_avg_cmc: depends on archetype speed (control wants higher,
      aggro wants lower)
    - role distribution: commander-specific (Arcades wants more creatures
      than typical because creatures double as defense)
    
    The LLM call is small (Haiku, single structured output) and runs once
    per generation. Cost: ~$0.005.
    """
```

</context>

---

## 7. Modified Prompts

<context name="prompts">

### 7.1 PROMPT-001 Modification: Value Inversion Articulation

Add to the Profile Synthesis prompt's task section:

```
For commanders whose abilities create non-obvious value patterns, explicitly state:

1. **Value Inversions:** What conventional Magic value heuristics does this 
   commander invert? Examples of inversions to look for:
   - Turning a normally-weak stat into the relevant stat (Arcades: toughness 
     becomes power)
   - Turning a normally-restrictive keyword into a benefit (defender becomes 
     valuable)
   - Making normally-overcosted spells appropriately costed (Eriette: auras 
     become value engines)
   - Rewarding repetition rather than optimization (Krenko: many small 
     creatures over few large ones)

2. **Card Characteristic Reframing:** Which card characteristics that are 
   normally weak become strong here? Which normally strong become irrelevant 
   or weak?

3. **Mispriced Card Examples:** Identify 3-5 specific cards that would be 
   poorly evaluated by generic heuristics but are excellent for this commander. 
   Briefly explain why each is undervalued by conventional analysis.

If the commander does NOT invert conventional heuristics (i.e., it's a 
"do-the-thing-but-better" commander like a Tribal Lord), explicitly state 
"This commander uses conventional Magic value heuristics" rather than 
forcing inversions that don't exist.
```

This addition prevents bland profiles for value-inversion commanders while not over-applying inversion logic to commanders that don't invert.

### 7.2 PROMPT-002 Modification: Deck-Context-Aware Fit Scoring

Modify the fit prompt to receive partial deck composition:

```
[CACHED SECTION - unchanged from v1]
[Role definition, scoring rubric, output format]
[Profile summary - cached]
[Reference excerpts - cached]
[END CACHED SECTION]

[PER-REQUEST SECTION]

<deck_composition_context>
This deck currently has {n} cards selected:

By role:
- Lands: {land_count} placed
- Ramp: {ramp_count} placed  
- Draw: {draw_count} placed
- Removal: {removal_count} placed
- Synergy/Win: {synergy_count} placed

Key cards already in deck (most relevant to this evaluation):
{key_cards_formatted}

Commander's strategic categories already covered:
{covered_categories}

Categories needing more support:
{undercovered_categories}
</deck_composition_context>

<card_to_evaluate>
{card_data}
</card_to_evaluate>

<task>
Evaluate this card's fit considering:
1. Does it serve the commander's strategy?
2. Does it add to undercovered categories or duplicate covered categories?
3. Does it synergize with the cards already chosen?
4. For value-inversion commanders, does it benefit from the commander's 
   value framework?
5. Does redundancy in this category provide meaningful reliability gains, 
   or is the category already saturated?
</task>
```

### 7.3 New PROMPT-005: Card Reasoning Extraction

For extracting reasoning from primer text. Specification:

```yaml
id: PROMPT-005
name: card_reasoning_extraction
file: src/reasoning/prompts/card_reasoning_extraction.txt
model: claude-haiku-4-5
estimated_cost: ~$0.017 per primer
purpose: "Extract {card, reasoning} pairs from primer text"
```

Full prompt content provided in implementation phase. Output schema is CardReasoningExtractionResponse with array of {card_name, reasoning_text, role_assignment, confidence_signal}.

</context>

---

## 8. Build Sequence

<context name="build_sequence">

This work is structured as Phase 6.5 (insertion between v1's Phases 6 and 7), Phase 9 (post-v1 reasoning extraction), and Phase 10 (post-v1 execution metrics).

### Phase 6.5: Pipeline Restructuring (Replaces parts of v1 Phase 6)

**Sub-phase 6.5.1: Foundation**
- Role tagging pass over all cards (one-time data generation)
- Add role_tags and functional_categories columns to cards table
- Implement role_tagger.py with pattern-matching rules
- Verify: 25,000+ cards tagged with reasonable accuracy

**Sub-phase 6.5.2: Karsten Library**  
- Encode Karsten's tables as karsten.py library
- Add config/karsten_mana_base.yaml with full data
- Unit test against Karsten's published examples
- Verify: required_sources() and target_land_count() match published tables

**Sub-phase 6.5.3: Template Derivation**
- Implement template_deriver.py
- Add small LLM prompt for profile-to-template translation
- Verify: templates for known commanders match expert intuition

**Sub-phase 6.5.4: Infrastructure Generators**
- Implement LandPackageGenerator with Karsten optimization
- Implement RampPackageGenerator with rule-based selection
- Implement DrawPackageGenerator
- Implement RemovalPackageGenerator
- Verify: each generator produces sensible output for 5+ test cases per archetype

**Sub-phase 6.5.5: Pipeline Integration**
- Restructure deck_builder.py to use staged pipeline
- Wire generators into Stage 4
- Verify: end-to-end deck generation produces valid 99-card decks

**Sub-phase 6.5.6: Category Coverage**
- Implement category_coverage.py
- Define functional categories taxonomy
- Verify: undercovered categories identified correctly for known archetypes

**Sub-phase 6.5.7: Deck-Context-Aware Fit Scoring**
- Modify PROMPT-002 to include deck composition context
- Modify fit.py to pass partial deck to prompt
- Update caching strategy for new prompt structure
- Verify: cost per fit call within 2-3x of v1; quality improvement on 
  value-inversion commanders

**Sub-phase 6.5.8: Budget Redistribution**
- Implement two-pass budget redistribution logic
- Verify: budget compliance achieved while maximizing synergy

**Sub-phase 6.5.9: Profile Prompt Update (PROMPT-001)**
- Add value-inversion articulation section
- Test against Arcades, Eriette, Doran, Krenko, conventional commanders
- Verify: profiles correctly identify inversions when present, don't force 
  them when absent

### Phase 9: Reasoning Extraction (Independent of Phase 6.5)

Original specification from prior conversation. Extends decklist ingestion to capture primers, adds extraction prompt, populates card_reasoning_extractions table. Integration with fit prompt as additional context.

### Phase 10: Execution Metrics (Optional, after Phase 6.5)

Per-commander execution profile generation, basic goldfish simulator, deck quality scoring. Can be deferred if Phase 6.5 alone produces satisfactory deck quality.

</context>

---

## 9. Open Decisions Requiring User Input

<context name="open_decisions">

The following decisions are not finalized in this spec and should be raised during implementation planning:

**Decision 1: Budget threshold for premium dual lands**
The spec says "skip premium duals by default, allow upgrades for low-CMC decks." What's the CMC threshold? Average CMC < 3.0? < 2.5? Should the upgrade also depend on budget tier (only allow shock-tier upgrades when budget exceeds $X)?

**Decision 2: Functional category taxonomy**
What are the official functional categories the system recognizes? Initial proposal:
- treasure_generation, sacrifice_outlet, etb_payoff, recursion, ramp, fixing, 
  draw, removal_creature, removal_artifact, removal_enchantment, board_wipe,
  tutor, protection, threat, wincon_combat, wincon_combo, wincon_drain,
  wincon_mill, aura, equipment, counter, evasion, flicker, token_generation,
  graveyard_payoff, anthem, mana_doubler

This list will need refinement during implementation. Should be encoded as config/functional_categories.yaml.

**Decision 3: Backward compatibility approach**
Should v1 deck generation continue working unchanged for users who haven't migrated, or is this a hard cutover? Recommendation: hard cutover, given single-user system. But flag for user confirmation.

**Decision 4: Role tagging accuracy threshold**
Pattern-matching for role tags will have errors. What's acceptable? Recommendation: aim for 95%+ accuracy on common cards (top 5,000 by EDHREC inclusion); accept lower accuracy on niche cards. Manual override capability via config/role_tag_overrides.yaml.

**Decision 5: When to apply value-inversion logic**
The profile prompt will sometimes incorrectly identify inversions for conventional commanders, or miss them for unusual commanders. How should the system handle these false positives/negatives? Recommendation: trust the profile output, but allow user notes during prospect addition to override.

</context>

---

## 10. Estimated Cost and Effort

<context name="estimates">

### Implementation Effort (Phase 6.5)

| Component | Effort | Complexity |
|---|---|---|
| Role tagging pass + library | 1 week | Moderate |
| Karsten library + config | 3 days | Low |
| Template derivation | 2 days | Low |
| Lands generator | 1 week | Moderate-High |
| Ramp generator | 4 days | Moderate |
| Draw generator | 3 days | Moderate |
| Removal generator | 4 days | Moderate |
| Pipeline integration | 1 week | High (touches existing code) |
| Category coverage | 4 days | Moderate |
| Deck-context fit prompt | 3 days | Low (prompt change) |
| Budget redistribution | 4 days | Moderate |
| Profile prompt update | 2 days | Low |
| Testing and refinement | 2 weeks | Moderate |

**Total: ~9-10 weeks of focused work**

### Cost Impact

| Stage | v1 Cost | v2 Cost | Delta |
|---|---|---|---|
| Profile generation | $0.40 | $0.45 | +$0.05 (longer prompt) |
| Template derivation | n/a | $0.005 | +$0.005 (new) |
| Infrastructure filling | $0.05 | $0 | -$0.05 |
| Differentiator scoring | $0.05 (50 cards) | $0.07 (30 cards × 2-3x context) | +$0.02 |
| Deck synthesis | $0.05 | $0.05 | $0 |
| **Per-deck total** | **$0.55** | **$0.58** | **+$0.03** |

Per-deck cost is roughly stable. Quality improvement is the primary value.

### Annual Budget Impact

Estimated personal usage: 30 commanders explored, 4 decks per commander.
- v1 annual: ~$30
- v2 annual: ~$32
- Within $50 ceiling

</context>
