# prompts.md

<instructions>
This document specifies all LLM prompt templates used in the Sabermetrics application. Each prompt is documented with:
- Purpose and call site
- Model used and rationale
- Caching strategy
- Input variables (with types)
- Output schema
- Full prompt text
- Examples of expected output

When modifying a prompt:
1. Update this document FIRST
2. Update the corresponding `.txt` file in `src/reasoning/prompts/`
3. Update the Pydantic output schema if the contract changes
4. Save sample outputs to `tests/fixtures/llm_outputs/` for regression checking

When adding a new prompt: follow SKILLS.md SKILL-003.
</instructions>

---

## Prompt Index

```yaml
prompts:
  - id: PROMPT-001
    name: profile_synthesis
    file: src/reasoning/prompts/profile_synthesis.txt
    model: claude-sonnet-4-6
    purpose: "Generate Commander Intent Profile from triangulated evidence"

  - id: PROMPT-002
    name: card_fit
    file: src/reasoning/prompts/card_fit.txt
    model: claude-haiku-4-5
    purpose: "Score per-card fit against commander profile"

  - id: PROMPT-003
    name: deck_synthesis
    file: src/reasoning/prompts/deck_synthesis.txt
    model: claude-sonnet-4-6
    purpose: "Generate deck-level narrative and analysis"

  - id: PROMPT-004
    name: relevance_screen
    file: src/reasoning/prompts/relevance_screen.txt
    model: claude-haiku-4-5
    purpose: "Screen new set cards for impact on existing profile"
```

---

## PROMPT-001: Profile Synthesis

<context name="prompt_001">

### Metadata

```yaml
id: PROMPT-001
name: profile_synthesis
file: src/reasoning/prompts/profile_synthesis.txt
model: claude-sonnet-4-6
estimated_cost: $0.30-0.40
estimated_time: 15-25 seconds
called_by: src/reasoning/profiler.py
output_schema: src/models/profile.py CommanderProfile
```

### Caching Strategy

```yaml
cache_breakpoints:
  - position: "After system prompt"
    content: "Role description, output schema"
    reuse: "Across all profile generations"
    expected_hit_rate: "100% after first call"

  - position: "After reference_chunks"
    content: "Retrieved rules and frameworks"
    reuse: "Often shared across commanders with similar archetypes"
    expected_hit_rate: "30-50%"

uncached:
  - "Commander-specific evidence (card text, EDHREC data, Reddit, rulings)"
  - "User intent if provided"
```

### Input Variables

```yaml
inputs:
  reference_chunks: list[ReferenceChunk]    # Retrieved rules and frameworks
  commander_name: str
  mana_cost: str
  type_line: str
  oracle_text: str
  color_identity: list[str]
  keywords: list[str]
  commander_rulings: list[CardRuling]       # From mtgapi
  deck_count: int                            # From EDHREC
  edhrec_themes: list[str]
  top_cards_list: list[TopCard]              # Top 30 with inclusion %
  avg_price: float
  avg_cmc: float
  tourney_winrate: float | None
  tourney_sample: int
  named_archetypes: list[str]
  reddit_topics: list[str]
  primer_summaries: list[str]
  user_intent_section: str                   # Empty string if no override
  profile_schema: str                        # Inline JSON schema
```

### Output Schema

See schema.md Section 4.1 (CommanderProfile JSON schema).

### Full Prompt

```text
<role>
You are an expert Magic: The Gathering Commander format strategist. Your task is to generate a structured strategic profile for a commander based on multiple evidence streams.
</role>

<reference_grounding>
{reference_chunks}
</reference_grounding>

<commander_card_data>
Name: {commander_name}
Mana Cost: {mana_cost}
Type Line: {type_line}
Oracle Text: {oracle_text}
Color Identity: {color_identity}
Keywords: {keywords}
</commander_card_data>

<official_rulings>
The following are official judge rulings on this commander, which clarify mechanical interactions:
{commander_rulings}
</official_rulings>

<aggregated_player_behavior>
Based on {deck_count} tracked decks:

Most Common Themes: {edhrec_themes}

Top 30 Most-Included Cards (with inclusion %):
{top_cards_list}

Average Deck Price: ${avg_price}
Average Mana Value: {avg_cmc}
Tournament Win Rate: {tourney_winrate} (sample size: {tourney_sample})
</aggregated_player_behavior>

<community_discussion>
Named Archetypes Identified: {named_archetypes}
Recent Discussion Topics: {reddit_topics}
Primer Articles: {primer_summaries}
</community_discussion>

{user_intent_section}

<task>
Synthesize these evidence streams into a strategic profile. Your reasoning must:

1. **Prioritize card text** as the primary source of truth for what the commander mechanically does. Inclusion data is corroboration, not authority.

2. **Note where evidence streams diverge.** If players consistently include cards the text doesn't obviously suggest, that signals discovered synergy worth highlighting.

3. **Identify multiple build paths**, ranked by community support:
   - Mainstream consensus (high inclusion + community confirmation)
   - Emerging strategies (some inclusion + community discussion)
   - Text-supported but underexplored paths

4. **Surface anti-synergies** — popular cards that don't actually fit, with reasoning.

5. **Estimate power level range** using the WotC bracket framework (1-5), not a single number.

6. **Cite specific rules** when they affect your reasoning (e.g., "CR 702.2 confirms flash creatures count as cast for trigger purposes").

7. **Use rulings** when they clarify edge-case interactions relevant to the commander's strategy.
</task>

<output_format>
Output JSON matching this schema exactly:

{profile_schema}

Be specific. Avoid generic statements like "this commander is good." Every claim should be tied to evidence.
</output_format>
```

### Example Output

```json
{
  "commander_id": "abc-123",
  "commander_name": "Korvold, Fae-Cursed King",
  "card_analysis": {
    "core_mechanic": "Triggers when you sacrifice a permanent: draw a card and grow with +1/+1 counter",
    "triggered_abilities": ["Sacrifice payoff: draw card + grow"],
    "activated_abilities": [],
    "static_abilities": ["Flying"]
  },
  "strategic_profile": {
    "primary_archetype": "Sacrifice value engine with token generation",
    "game_plan_summary": "Generate sacrifice fodder via tokens and treasure, feed Korvold for card advantage and growth, eventually win through commander damage or convergent value plays.",
    "win_conditions": [
      {
        "description": "Commander damage with grown Korvold",
        "key_cards": ["Rogue's Passage", "Whispersilk Cloak"],
        "reliability": "primary"
      },
      {
        "description": "Convergent value through card advantage",
        "key_cards": ["Smothering Tithe", "Pitiless Plunderer"],
        "reliability": "secondary"
      }
    ],
    "anti_synergies": [
      {
        "description": "Cards requiring keeping permanents on board",
        "cards_to_avoid": ["Necropotence (life loss prevents sacrifice value)"],
        "reasoning": "Korvold wants to actively sacrifice; cards that punish sacrificing or require static board states fight the engine"
      }
    ],
    "power_indicators": {
      "estimated_ceiling_bracket": 4,
      "estimated_floor_bracket": 3,
      "notes": "Can be tuned to high power with Food Chain combo; casual builds remain at bracket 3"
    }
  },
  "sources": {
    "rules_chunks_referenced": ["CR 701.16", "CR 702.111"],
    "articles_referenced": ["EDHREC: Korvold archetype guide"]
  }
}
```

</context>

---

## PROMPT-002: Card Fit Scoring

<context name="prompt_002">

### Metadata

```yaml
id: PROMPT-002
name: card_fit
file: src/reasoning/prompts/card_fit.txt
model: claude-haiku-4-5
estimated_cost_per_call: $0.001
calls_per_deck: 50
total_cost_per_deck: ~$0.05
estimated_time_per_call: 200-400ms (parallelized)
called_by: src/reasoning/fit.py
output_schema: src/models/llm_responses.py CardFitResponse
```

### Caching Strategy

```yaml
cache_breakpoints:
  - position: "After system prompt"
    content: "Role, scoring rubric, output format"
    reuse: "100% within deck generation"

  - position: "After profile_summary"
    content: "Commander strategic profile (compressed form)"
    reuse: "100% across all 50 cards in this deck generation"

  - position: "After reference excerpts"
    content: "Archetype definition, relevant rules"
    reuse: "100% across all 50 cards in this deck generation"

uncached:
  - "Per-card data (name, text, price, EDHREC stats, CWE, co-occurrence)"

cost_savings_from_caching: "~94% vs uncached"
```

### Input Variables

```yaml
inputs:
  # Cached section
  profile_summary: str                       # Compressed profile (key sections only)
  archetype_definition: str                  # From reference layer
  relevant_rule_excerpts: str                # From reference layer

  # Per-call section
  card_name: str
  mana_cost: str
  type_line: str
  oracle_text: str
  price: float
  inclusion_pct: float                       # EDHREC inclusion
  cwe_score: float | None                    # Card Win Equity
  cooccurrence_avg: float                    # Avg co-occurrence with profile top cards
```

### Output Schema

See schema.md Section 4.2 (CardFitResponse JSON schema).

### Full Prompt

```text
[CACHED SYSTEM PROMPT]

<role>
You are evaluating individual cards for inclusion in a Commander deck. Score each card 1-10 for fit with the given commander's strategy and provide a one-sentence reason.
</role>

<scoring_rubric>
- 10: Format-defining for this strategy; near-mandatory inclusion
- 8-9: Strong fit; significantly improves the deck's plan
- 6-7: Solid fit; supports the strategy
- 4-5: Reasonable inclusion but interchangeable with alternatives
- 2-3: Weak fit; doesn't directly support the strategy
- 1: Anti-synergy; actively works against the strategy
</scoring_rubric>

<reference_context>
{archetype_definition}

Relevant rules:
{relevant_rule_excerpts}
</reference_context>

<commander_strategic_profile>
{profile_summary}
</commander_strategic_profile>

<output_format>
Output JSON only:
{
  "fit_score": <int 1-10>,
  "reasoning": "<one sentence, max 200 chars>",
  "slot_role": "<ramp|draw|removal|wincon|utility|land|other>"
}
</output_format>

[END CACHED SECTION]

[PER-REQUEST VARIABLE SECTION]

<card_to_evaluate>
Name: {card_name}
Mana Cost: {mana_cost}
Type: {type_line}
Text: {oracle_text}
Price: ${price}
EDHREC Inclusion: {inclusion_pct}%
Card Win Equity: {cwe_score}
Co-occurrence with profile's top cards: {cooccurrence_avg}
</card_to_evaluate>
```

### Example Output

```json
{
  "fit_score": 9,
  "reasoning": "Smothering Tithe punishes opponent draws while generating sacrifice fodder for Korvold — triple-purpose card.",
  "slot_role": "ramp"
}
```

### Notes

- Reasoning should be one sentence, mechanically specific
- Generic reasoning ("good card") indicates a prompt failure; retry with stronger guidance
- Fit scores should be calibrated; don't drift toward 7-8 for everything

</context>

---

## PROMPT-003: Deck Synthesis

<context name="prompt_003">

### Metadata

```yaml
id: PROMPT-003
name: deck_synthesis
file: src/reasoning/prompts/deck_synthesis.txt
model: claude-sonnet-4-6
estimated_cost: $0.05
estimated_time: 5-10 seconds
calls_per_deck: 1
called_by: src/reasoning/synthesis.py
output_schema: src/models/llm_responses.py DeckSynthesisResponse
```

### Caching Strategy

```yaml
cache_breakpoints:
  - position: "After system prompt"
    content: "Role, output format"
    reuse: "100% across all deck syntheses"

uncached:
  - "Profile (different per deck)"
  - "Deck composition (different per deck)"
```

### Input Variables

```yaml
inputs:
  profile_summary: str
  deck_summary_with_fit_reasoning: str       # Formatted list of 99 cards with fit scores
  bracket: int
  bracket_reasoning: str
```

### Output Schema

See schema.md Section 4.3 (DeckSynthesisResponse JSON schema).

### Full Prompt

```text
<role>
You are writing the strategic narrative for a generated Commander deck. Given the commander profile and the 99 chosen cards with per-card fit reasoning, produce a clear, actionable analysis.
</role>

<commander_profile>
{profile_summary}
</commander_profile>

<deck_composition>
{deck_summary_with_fit_reasoning}
</deck_composition>

<classification>
Bracket Classification: {bracket}
Reasoning: {bracket_reasoning}
</classification>

<task>
Produce:

1. **Game Plan Summary** (3-5 sentences): How does this deck win? What's the typical sequence of plays?

2. **Key Synergies** (3-7 items, each one sentence): Specific card interactions that define the deck's power. Be mechanical, not generic.

3. **Weaknesses** (1-5 items, each one sentence): Vulnerabilities a thoughtful opponent could exploit.

4. **Suggested Play Pattern** (1-2 sentences): How to actually pilot this deck — early plays, mulligan considerations, threat assessment.
</task>

<output_format>
Output JSON only:
{
  "game_plan": "<3-5 sentences>",
  "key_synergies": ["<sentence 1>", "<sentence 2>", ...],
  "weaknesses": ["<sentence 1>", ...],
  "suggested_play_pattern": "<1-2 sentences>"
}
</output_format>

<quality_bar>
- Synergies must reference specific cards, not categories
- Weaknesses must be exploitable, not vague
- Game plan must be actionable, not aspirational
</quality_bar>
```

### Example Output

```json
{
  "game_plan": "Establish ramp by turn 3-4 with Sol Ring, signets, and Cultivate-style spells. Deploy Korvold by turn 5-6 with sacrifice fodder ready (tokens or treasures). Use sacrifice payoffs for incremental advantage while growing Korvold to lethal commander damage range. Close out games either through commander damage with evasion (Whispersilk Cloak, Rogue's Passage) or by convergent value engines (Pitiless Plunderer + Black Market Connections).",
  "key_synergies": [
    "Smothering Tithe + Pitiless Plunderer + sacrifice outlets creates a self-sustaining mana engine",
    "Mayhem Devil + Korvold turns each sacrifice into double damage and value",
    "Dockside Extortionist + Mox Opal chain enables explosive Korvold turns",
    "Ophiomancer provides perpetual sacrifice fodder for Korvold's trigger"
  ],
  "weaknesses": [
    "Vulnerable to graveyard hate (Bojuka Bog, Rest in Peace) which shuts down recursion engines",
    "Slow against linear combo decks that can win before turn 6",
    "Dependent on Korvold being on board; aggressive tuck/exile effects neutralize the engine"
  ],
  "suggested_play_pattern": "Mulligan for ramp + sacrifice outlet + draw spell. Hold Korvold until you have at least one immediate sacrifice to fire his trigger; don't play him into open removal without protection."
}
```

</context>

---

## PROMPT-004: Relevance Screen

<context name="prompt_004">

### Metadata

```yaml
id: PROMPT-004
name: relevance_screen
file: src/reasoning/prompts/relevance_screen.txt
model: claude-haiku-4-5
estimated_cost: $0.001
estimated_time: 200-500ms
calls_per_set_release: ~20 (one per cached profile, only if commander has color-identity-matching new cards)
called_by: src/pipeline/refresh_scheduler.py during quarterly_set_refresh
output_schema: src/models/llm_responses.py RelevanceScreenResponse (list)
```

### Caching Strategy

```yaml
cache_breakpoints:
  - position: "After system prompt"
    content: "Role, output format"
    reuse: "100% across all profiles screened"

uncached:
  - "Profile summary (different per commander)"
  - "New cards list (different per call)"
```

### Input Variables

```yaml
inputs:
  profile_summary_short: str                 # Compressed profile (just key sections)
  new_cards_list: str                        # Bullet list of new cards in commander's colors
```

### Output Schema

See schema.md Section 4.4 (RelevanceScreenResponse JSON schema).

### Full Prompt

```text
<role>
You are checking whether new cards from a recent Magic set affect an existing commander's strategy. For each card, decide if it could meaningfully improve, change, or complicate the commander's optimal builds.
</role>

<existing_profile>
{profile_summary_short}
</existing_profile>

<new_cards_in_commander_colors>
{new_cards_list}
</new_cards_in_commander_colors>

<task>
For each card, answer:
- Does this card affect the commander's strategy?
- One sentence why or why not.

A card "affects strategy" if it would plausibly be added to a deck for this commander, OR if it changes how an existing card should be evaluated, OR if it enables a new build path.

Cards that are simply "good cards in this color" but don't specifically interact with the commander's strategy do NOT affect strategy for this purpose.
</task>

<output_format>
Output JSON array only:
[
  {
    "card_name": "<name>",
    "affects_strategy": <true|false>,
    "reason": "<one sentence, max 200 chars>"
  },
  ...
]
</output_format>
```

### Example Output

```json
[
  {
    "card_name": "Sheoldred's Edict",
    "affects_strategy": false,
    "reason": "Generic black removal in commander's colors but doesn't specifically interact with sacrifice/value strategy."
  },
  {
    "card_name": "Treasure Vault",
    "affects_strategy": true,
    "reason": "Treasure-generating land enables Korvold's sacrifice engine and ramps simultaneously — direct strategic enabler."
  }
]
```

### Cost Discipline

- Set ceiling: $0.10 total per set release for relevance screening
- If new set has many cards in commander's colors, batch into single call (multiple cards per request) rather than one-call-per-card

</context>

---

## Prompt Quality Standards

<context name="quality_standards">

All prompts must follow these standards:

```yaml
required_elements:
  - "Use XML tags to delineate sections (<role>, <task>, <output_format>, etc.)"
  - "Output format specified explicitly with example"
  - "Inputs documented with types in this file"
  - "Output validated against Pydantic schema in caller"
  - "Cache breakpoints explicit"
  - "Cost estimate documented"

forbidden:
  - "Embedding prompts in Python source code (must be .txt files)"
  - "Free-form output without schema validation"
  - "Calls without prompt caching"
  - "Calls outside src/reasoning/client.py wrapper"

revision_triggers:
  - "Output quality drops (per manual validation)"
  - "Cost exceeds documented estimate by >50%"
  - "Schema changes (must update prompt + Pydantic model together)"
  - "Hallucination patterns observed (e.g., invalid card names, wrong rules cited)"
```

</context>
