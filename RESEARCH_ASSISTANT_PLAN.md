# Research Assistant: phased build plan

_Authored 2026-09-06. Branch `research-assistant`, worktree
`~/Projects/bristly_billy_beane-research-assistant`, based on `deck-lab-refactor`
at f6f0bdd. Companion plan for strategy packs lives in
`~/Projects/commander_simulator-strategy-packs/STRATEGY_PACK_PLAN.md`._

> **SUPERSEDED IN PART, 2026-09-08.**
> [`RESEARCH_ASSISTANT_SPEC.md`](RESEARCH_ASSISTANT_SPEC.md) is now the
> authoritative statement of scope, product shape and phase order. It reverses
> four decisions made here — the UI moves to a thin pilot at ~week 6 instead of
> after week 13; automatic diff proposals move after variant comparison;
> evaluation splits into a correctness rig and a separate usefulness rig; and
> "it is not a card evaluator" (§0) is retired in favour of a typed, cited
> Interpretation tier. **Everything else in this document is adopted unchanged
> and this remains the reference for substrate detail** — the mechanic tag
> format, retrieval fusion, the Query IR, and the G1/G2/G3 gate definitions.
> Note also that the worktree paths below are stale: the work now lives in
> `~/Projects/deck_lab/worktrees/research-assistant`.

---

## 0. What this builds, and what it does not

This builds a **headless research engine** for cEDH deck building: a typed query
surface over the card corpus, the tournament corpus, the rules, and the
simulator, driven by a small language model that plans queries and narrates
results.

**It does not build a UI.** Every phase here is exercised by a CLI and a test
suite. The dock, the drag-and-drop, the visual/tabular toggle and the
notification badge are a separate branch that consumes the interfaces defined
here. That split is deliberate: the interesting risk in this project is whether
a cheap model plus a strong substrate can answer real deck-building questions,
and a UI cannot tell you that.

**It is not a chatbot with a Magic prompt.** The model never names a card. Every
card in every answer is an `oracle_id` that came out of a query result set.

**It is not a card evaluator.** It reports what the corpus contains, what the
field plays, what the rules say, and what the simulator measured. It does not
say a card is good.

---

## 1. The thesis, and the gates that test it

The owner's stated bet, and the one this plan is organized around:

> Use a cheap or free model, and make the staged layers of understanding robust
> enough that a strong model is not required.

This is a real architectural position and it can be right. It can also fail
quietly, in the specific way that produces a demo that impresses and a product
that misleads. So it is written here as a **falsifiable claim with measurement
gates**, and each gate has a stated action on failure.

| Gate | After phase | The claim | Measured by | If it fails |
|---|---|---|---|---|
| **G1** | A2 | The substrate can *retrieve* the right cards at all | recall@50 on the golden set's required-card lists, per retriever and fused | Stop. No model fixes retrieval. Fix tags/embeddings/fusion. |
| **G2** | A4 | Hand-written query plans answer real questions end to end | ≥80% of golden questions answered with full required-card recall, by a human-authored plan and no LLM | Stop. The tool vocabulary is wrong. Widen it before adding a planner. |
| **G3** | A5 | A cheap model can produce those plans | plan agreement + end-to-end recall vs. the G2 hand-written baseline; measured across ≥3 models with one frontier model as a ceiling reference | Report the gap honestly. Either accept a stronger model or narrow the question surface. |
| **G4** | A6 | The narration is trustworthy | citation coverage = 100%; cards-named-outside-result-set = 0; rates-without-denominator = 0 | Structural bug. These are enforced by schema, not by prompt. |

**G2 is the most important gate in this document.** It is the one that separates
"the substrate is doing the work" from "the model is doing the work and we hope
it is right." A hand-written plan hitting 80% means the intelligence genuinely
lives in the corpus layer, and the model's job is reduced to translation — which
is the job small models are actually good at. If hand-written plans cannot answer
these questions, no amount of model quality will make the answers trustworthy;
it will only make them fluent.

**The frontier-model ceiling reference at G3 is not a proposal to ship a frontier
model.** It is the control. Without it, a mediocre score is unattributable — it
could be the model or it could be the substrate, and those have opposite fixes.

---

## 2. Isolation

```
~/Projects/bristly_billy_beane                     main                (untouched)
~/Projects/bristly_billy_beane-deck-lab-refactor   deck-lab-refactor   (untouched)
~/Projects/bristly_billy_beane-research-assistant  research-assistant  ← this work
~/Projects/commander_simulator                     main                (untouched)
~/Projects/commander_simulator-strategy-packs      strategy-packs      ← Track B
```

Inherited from `docs/deck-lab-refactor-isolation.md` and extended:

- **Base branch:** `deck-lab-refactor` @ f6f0bdd, chosen because Phase A8 needs
  `deck_documents.py`, which exists only there. Merge target is
  `deck-lab-refactor`, or `main` after that lands. Rebase before each phase
  boundary, not during a phase.
- **Feature gate:** `SABER_RESEARCH_ASSISTANT=1`, off by default, independent of
  `SABER_DECK_LAB_REDESIGN`. Nothing in this branch is reachable from a running
  app until Phase A9.
- **Local database:** `.research-dev/research.db`, disposable, gitignored.
  `SABER_DECK_LAB_DEV=1` guard applies unchanged — it must keep refusing
  `/data/sabermetrics.db`.
- **Protected targets, never addressed from this branch:** Fly apps `decklab`,
  `sim-worker`, `sim-sweep`; volume `decklab_data`; the Neon production
  database; `/data/sabermetrics.db`.
- **Postgres access is read-only, as `mtg_consumer`, against `mtg_v1` only.**
  `assert_v1_only` applies to every new query. The card view is
  `card_any_medium`, never `card`.
- **No network in the research path at query time.** Every corpus, tag, embedding
  and combo fact is materialized ahead of time from a named snapshot. The only
  live calls are to the model provider and to the simulator service.

---

## 3. Architecture

### 3.1 Three new packages

```
src/sabermetrics/
  mechanics/    pure functions: card text -> mechanic tags. stdlib + re only.
  substrate/    the indexed knowledge layer. reads mtg_v1, writes local indexes.
  assistant/    query IR, executor, planner, narrator, threads, eval harness.
```

Import rules, asserted by a test over the import graph, in the same style as the
existing `cedh_no_legacy_ingestion` check:

| Package | May import | May **not** import |
|---|---|---|
| `mechanics/` | stdlib, `re` | everything else, including `config` and any DB |
| `substrate/` | `mechanics`, `db`, adapters, numpy | `pipeline`, `reasoning`, `ingestion`, `analytics`, any vendor SDK |
| `assistant/` | `mechanics`, `substrate`, `cedh.model_gateway`, `cedh.cost_ledger`, `cedh.simulator`, `deck_documents`, `reference_layer` | `pipeline`, `reasoning`, `ingestion`, `analytics`, any vendor SDK |
| `cedh/` | unchanged | unchanged — and now also may not import `assistant` |

`assistant/` sits **beside** `cedh/`, not inside it. It borrows the gateway and
the ledger; it does not join the deterministic generation path. `cedh/`'s
existing prohibition on `analytics` is preserved by promoting the pure modules
out of `analytics` rather than by amending the rule.

### 3.2 What "promote" means concretely (owner decision 1)

These modules were audited for pure text-to-fact behavior. Four move to
`mechanics/`; the fifth candidate stays behind for the reason recorded below:

| From `analytics/` | Why it qualifies |
|---|---|
| `effective_cost.py` | Parses alternative casting costs. Already 60% of the tag engine. |
| `oracle_keywords.py` | CR 702.x keyword extraction, module-level compiled regexes. |
| `oracle_patterns.py` | Oracle text pattern matching. |
| `theme_patterns.py` | Same shape, same conventions. |
| `role_tagger.py` | **Not promoted after audit.** It reads `config/role_tag_overrides.yaml` on each call and performs scored role classification, so it is neither filesystem-pure nor free of judgment. |

Each move leaves a thin re-export shim at the old path so the legacy casual
generator keeps working unchanged. The shims are marked deprecated and are not
imported by anything new.

Everything else in `analytics/` stays where it is. `cvar.py`,
`empirical_valuation.py`, `card_win_equity.py`, `cluster_valuation.py` and
`synergy_matrix.py` encode judgments built for the budget/casual objective. They
are not promoted, not imported, and not consulted. Some of their *methods* may be
worth rebuilding against the cEDH objective later; that is a decision to make on
purpose, with new acceptance criteria, not by importing a module.

### 3.3 The Query IR

The single most important interface in this design. The model emits a
**ResearchPlan**: a small, typed, versioned DAG of steps. The executor validates
and runs it. The model never emits SQL, never emits card names, and never emits
a ranking.

Conceptually:

- A plan has a version, a restatement of intent, and an ordered list of steps.
- Each step is one of a closed set of typed operations, names its inputs by
  prior step id, and declares its own limit.
- Step kinds at Phase A4: `card_search`, `tag_filter`, `field_stats`,
  `deck_profile`, `deck_diff`, `rules_lookup`, `combo_lookup`, `sim_study`,
  and the set operations `union`, `intersect`, `difference`.
- Every step's **result envelope** carries provenance as required fields:
  corpus snapshot hash, tag version, window, denominator, event-size floor,
  coverage, and result truncation state. A result cannot be constructed without
  them, so it cannot be rendered without them.

Three properties follow from this, and they are the reason for the design:

1. **The plan is inspectable.** A user can be shown the query that produced an
   answer, in words. An answer whose plan looks wrong is visibly wrong before
   anyone reads the prose.
2. **The plan is testable without a model.** Phase A4 writes plans by hand for
   the entire golden set. That is gate G2.
3. **The plan is a cache key.** Identical plans against an identical snapshot
   return identical results, for free.

### 3.4 Where intelligence actually comes from

Stated plainly, because it is the whole bet:

| Layer | Carries | Model involvement |
|---|---|---|
| Mechanic tags | What a card *does*, as auditable predicates | none |
| Hybrid retrieval | What is *similar*, lexically and semantically | none |
| Field statistics | What the metagame *plays*, with denominators | none |
| Combo graph | What *assembles* with what | none |
| Rules RAG | What the rules *say* | none |
| Simulator | How fast a declared line *assembles* | none |
| Ranking | Ordering of results | none — deterministic |
| **Planning** | intent → typed query | **yes, small model** |
| **Narration** | result set → cited prose | **yes, small model** |

Two rows have a model in them. Everything else is deterministic, cacheable and
testable. That is the mechanism by which a cheap model becomes sufficient — not
prompt engineering.

---

## 4. Track A phases

Sizes are rough order-of-magnitude for one person working with an agent, and are
the least reliable numbers in this document.

### A0 — Foundations and the measuring rig · ~3 days

The eval harness is built **first**, before there is anything to evaluate,
because a quality bet with no measurement is a preference.

1. Package skeletons for `mechanics/`, `substrate/`, `assistant/` with the
   import-graph test enforcing §3.1 from day one.
2. Promote the five modules per §3.2, with re-export shims. Add a purity test:
   `mechanics/` imports nothing outside stdlib.
3. **Golden question set**, `assistant/eval/questions/*.yaml`. Target ~80
   questions at A0, growing to ~150. Every question carries:
   - the natural-language ask, and a clarified variant
   - `required_oracle_ids` — cards that must appear, hand-verified
   - `forbidden_oracle_ids` — cards whose presence is an error
   - `expected_absences` — what the answer must *state it cannot answer*
   - a category and a difficulty
4. Categories, with rough target counts:

   | Category | n | Example |
   |---|---|---|
   | Mechanic search | 25 | the Gut Shot / alternate-cost question |
   | Rules | 12 | "does paying Phyrexian mana still cast the spell for a cast trigger?" |
   | Metagame | 20 | "how many recorded Vivi lists play ≥3 free spells?" |
   | Deck-local | 15 | "am I light on blue sources by turn two relative to the field?" |
   | Combo | 12 | "what two-card lines am I one piece away from?" |
   | Ambiguous | 10 | must trigger a clarification, not a guess |
   | Out of scope | 12 | must state an absence, not improvise |

5. **ADR-029** recording the query-planner rule, `assistant_may` /
   `assistant_may_not`, and the decision to place `assistant/` outside `cedh/`.

**Acceptance:** import-graph and purity tests green; legacy imports unbroken;
golden set loads and validates; the harness runs and reports an all-zeros
scorecard.

---

### A1 — The mechanic tag corpus · ~2 weeks · no LLM

The highest-value single investment in this plan, and the reason the example
question is answerable at all.

**Tag definition format** — checked in, versioned, one file per family:

- `id` (e.g. `cost:phyrexian_mana`), `version`, prose `description`
- `predicate` — regex or small AST over oracle text, mana cost and type line
- `positive_fixtures` / `negative_fixtures` — hand-labeled card names, ≥20 total
- `limitations` — a required prose field stating what the tag misses

**Precision discipline, borrowed from the simulator's card model:** every tag
ships with measured precision and recall against its fixtures. A tag below
threshold does not ship. A tag with an empty `limitations` field does not ship,
for the same reason an explanation with no weaknesses is marketing copy.

**Families, in build order:**

1. `cost:*` — Phyrexian mana, alternative cast cost, additional non-mana cost,
   conditional-free (the Force of Will shape), cost reduction, effective-zero.
   This family answers the driving example. `effective_cost.py` already covers
   morph/evoke/dash/madness/unearth/disguise and extends here.
2. `mana:*` — fast mana, ritual, mana rock (net-positive vs. net-neutral), dork,
   land-tutor, treasure.
3. `draw:*` — cantrip, draw engine, wheel, impulse, tutor-to-hand,
   tutor-to-battlefield, tutor-to-top, transmute-class.
4. `interact:*` — hard counter, soft/tax counter, conditional counter, spot
   removal by target class, sweeper, bounce, graveyard hate, stax/tax effect.
5. `win:*` — combo piece, combo enabler, outlet, alt-win-condition, protection.

**Output:** `card_mechanic_tag(oracle_id, tag_id, tag_version, confidence,
matched_span, snapshot_hash)`, rebuilt per corpus snapshot by a CLI.

`matched_span` is not optional. It is what lets an interface show *why* a card
carries a tag, and it is the difference between a tag and an unattributable
claim.

**Coverage is reported, not assumed.** The build prints how many cards carry no
tag at all, grouped by type line — the direct analogue of the simulator's inert
table. A large untagged set is the tag library being narrow in a direction it
should then state.

**Acceptance:** ≥60 tags shipped; every tag ≥0.95 precision on its fixtures;
coverage report printed; the alternate-cost query returns a hand-verified set for
the driving example; tag rebuild is deterministic and content-hashed.

---

### A2 — Retrieval substrate · ~1.5 weeks · no LLM · **GATE G1**

Four retrievers over the card corpus, fused.

1. **Lexical.** SQLite FTS5 over name + type line + oracle text. Replaces the
   current `LIKE '%q%'`, which is the single weakest link in today's research
   page.
2. **Structured.** Color identity (with `⊆` / `=` / `∩` modes), type, mana value,
   legality, and tag predicates from A1. Pushed down to SQL, not filtered in
   Python.
3. **Dense.** Oracle-text embeddings. **Replace `all-MiniLM-L6-v2` with
   `bge-small-en-v1.5` as the new baseline** — same CPU cost class, materially
   better retrieval, and the existing `reference_layer` indexer already has the
   loading and storage pattern. Embed a templated document per card (name, type
   line, mana cost, oracle text) rather than raw text. ~31k cards × 384 dims ≈
   46 MB float32; numpy memmap, no vector database. Locality over distribution.
4. **Rerank.** A small cross-encoder (`bge-reranker-base` class) over the fused
   top ~200. This is the technique that most directly substitutes local ML for
   LLM capability — it is the reranking the model would otherwise be asked to do,
   done better, deterministically, and for free.

**Fusion:** Reciprocal Rank Fusion over lexical + dense, hard-filtered by
structured predicates, then reranked. Weights are config, not code, and are
tuned against the golden set rather than by taste.

**Optional, deferred to A2b if G1 is close but short:** domain-adapt the
embedding model by contrastive fine-tuning on card pairs that co-occur in
tournament decks for the same commander. Cheap, CPU-feasible for a small model,
and it teaches the encoder cEDH's notion of similarity rather than English's.
Do not start this before G1 is measured with the off-the-shelf model — it may
not be needed, and it is the kind of work that expands to fill available time.

**GATE G1 acceptance:** recall@50 reported per retriever and fused, over every
golden question's `required_oracle_ids`. Target ≥0.90 fused. Report the
per-retriever contribution so it is clear what is carrying the result. **If fused
recall is below 0.80, stop and fix the substrate before proceeding.**

---

### A3 — Field intelligence · ~1.5 weeks · no LLM

What makes the assistant contextually understand the metagame. Not a bigger
prompt — materialized deck-level statistics.

Materialized from `mtg_v1` per corpus snapshot:

- `deck_tag_profile(deck_id, tag_id, count)` — every recorded tournament deck
  profiled against the A1 tag library. This is the join that answers *"31 of the
  42 recorded Vivi lists play ≥3 cards tagged `cost:phyrexian_mana`; the median
  is 4; among the 9 that made top 16 the median is 5."*
- `card_field_stats(commander_oracle_id, card_oracle_id, window, n_decks,
  n_including, n_top16_including, event_count, event_size_floor)`.
- `archetype_cluster(deck_id, cluster_id, snapshot)` — clustering over tag
  profiles, so "decks like this one" is answerable for commanders with thin
  individual samples.

**Every field query returns its denominator, window, event-size floor and
coverage as required fields.** `popularity_is_not_quality` becomes a type
signature rather than a convention. A commander with too few recorded lists
returns a stated absence — never a percentage over n=3.

**Acceptance:** the driving metagame query answers in <200 ms with full
provenance; a thin-sample commander returns an absence rather than a number;
refresh is idempotent and keyed to `corpus_snapshot`.

---

### A4 — Query IR and deterministic executor · ~2 weeks · no LLM · **GATE G2**

1. The `ResearchPlan` schema per §3.3, versioned, with a validator that rejects
   unknown step kinds and dangling input references.
2. The executor: one handler per step kind, each returning a provenance-bearing
   result envelope.
3. Tools wired: `card_search` (A2), `tag_filter` (A1), `field_stats` /
   `deck_profile` / `deck_diff` (A3), `rules_lookup` (existing
   `reference_layer`, reactivated), `combo_lookup` (see §7 — may be deferred),
   `sim_study` (stubbed to return `NotSimulated` until Track B lands), set ops.
4. **Hand-write a plan for every golden question.** This is the phase's real
   deliverable and the reason the phase exists.

`rules_lookup` deserves a note: `reference_layer/` is built, idle, and exactly
right for the rules category. Reactivating it is close to free and it is the
only honest way to answer "does Phyrexian mana still count as casting."

**GATE G2 acceptance:** ≥80% of golden questions answered with full
`required_oracle_ids` recall and zero `forbidden_oracle_ids`, by hand-written
plans and no model. Out-of-scope questions produce a stated absence. **If below
70%, stop: the tool vocabulary is wrong, and a planner would only obscure that.**

---

### A5 — The planner · ~2 weeks · first LLM · **GATE G3**

Natural language plus clarification answers, to a `ResearchPlan`. Nothing else.

**The techniques that make a small model sufficient here**, in expected order of
impact:

1. **Closed vocabulary.** The model *selects* tag ids from an enumerated list
   supplied in the (cached) system prompt. Selection from a closed set is a far
   easier task than generation, and it is the single biggest lever.
2. **Strict schema.** `json_schema_for()` already tightens every object to
   `additionalProperties: false` with all properties required. Structured output
   is mandatory; the gateway raises rather than partially accepting.
3. **Decomposition into narrow calls.** Classify intent → fill slots → assemble
   plan, each with a small schema, rather than one call producing a whole DAG.
   Small models degrade sharply with schema size.
4. **Plan memory.** Embed accepted plans; retrieve the k nearest past
   (question, plan) pairs as few-shot examples. This is retrieval-augmented
   prompting applied to the planner itself, and it compounds — the system gets
   better at planning as it is used, with no fine-tuning.
5. **Self-consistency by union.** Sample the plan 3× at non-zero temperature and
   take the *union* of retrieval steps. Safe by construction: extra queries widen
   recall, the executor is deterministic, and ranking is not the model's job.
   This buys much of a large model's reliability for roughly 3× a small model's
   cost, which is still far below a large model's.
6. **Deterministic validation with cheap repair.** An invalid plan is caught by
   the validator, and the specific violation is fed back. The gateway already
   counts repair attempts and raises when exhausted.

**Clarification turns** are a separate, tiny schema: restated intent plus up to
three multiple-choice questions with typed options. Rules: at most one round by
default; the model must be able to skip it; answers are structured data and join
the cache key. An assistant that always asks questions is one people stop using.

**GATE G3 acceptance:** report a bake-off table — plan agreement with the A4
hand-written baseline, end-to-end required-card recall, cost per question, and
p50/p95 latency — across at least DeepSeek-V4-Flash (the incumbent), two other
cheap or free candidates, and one frontier model as the ceiling reference.
Target: cheap model within 10 points of hand-written recall, and within 5 points
of the frontier ceiling. **Publish the gap whatever it is.** ADR-021's
provider-neutral gateway means acting on this result is an adapter change, not a
rewrite — that is the design paying off.

---

### A6 — Narrator and the citation contract · ~1 week · **GATE G4**

Result envelopes to cited prose.

The response schema is a list of **assertions**, each carrying its citation ids.
The renderer drops uncited assertions. This is `absence_is_visible` applied to
prose: an assistant that cannot say where it got something does not say it.

Citation kinds: `corpus:<snapshot_hash>`, `tag:<tag_id>@<version>`,
`field:<commander>/<window>/n=<denominator>`, `rules:<CR section>`,
`sim:<simulation_input_sha256>`.

Enforced by schema rather than by prompt wording:

- No card may be named that is not in a result set — the field is an
  `oracle_id` checked against the executor's outputs.
- No rate may be stated without its denominator and window.
- No simulator number may be rendered without the no-opponents framing and the
  inert-set disclosure. The existing `SimulationResult` already carries the
  honesty fields as required values, so this is inherited.
- Every answer states what it could not determine.

**GATE G4 acceptance:** citation coverage 100%; cards-named-outside-result-set
0; bare rates 0; every simulator citation carries its framing. These are
structural, so a non-zero result is a bug, not a tuning target.

---

### A7 — Threads, async and budgets · ~1 week · headless

Reuse the existing `BuildJobsRepo` + `ThreadPoolExecutor` pattern from
`cedh_routes.py` verbatim. No broker, no SSE, no second process.

- `research_thread` (owner, title, optional `deck_id`, unread flag)
- `research_turn` (role, content, structured payload, citations, cost)
- `research_task` (status: `queued → clarifying → planning → searching →
  simulating → writing → done | failed`, progress notes, result)

**Progress notes are the product, not decoration.** The task records each tool
call as it resolves — "searching corpus: phyrexian mana in cost… 23 found…
profiling 42 recorded Vivi lists…" — so a long wait is legible rather than
anxious, and so a wrong answer can be traced to the wrong query.

**Budgets (owner decision 3):** a per-user monthly research-question cap,
configurable and not surfaced as a number to the user, alongside the existing
global `monthly_cost_ceiling_usd` hard stop. Research questions are an unbounded
surface in a way manual deck builds are not, which is why the removal of deck
quotas does not generalize here.

**Caching:** plan and result cached on `(clarified_intent_hash,
corpus_snapshot_hash, tag_version, prompt_version)`. A repeated question is free.
Extends the gateway's existing `cache_key`, which deliberately excludes the model
id so a model swap is visible rather than hidden.

**Concurrency:** one in-flight task per user; a global queue-depth cap; visible
queue position. The production target is one `shared-cpu-1x` machine running
Flask, SQLite, a CPU embedding model and a reranker, talking to a simulator with
`SIM_MAX_CONCURRENT=1`. This is the constraint that will bite first.

**Acceptance:** a CLI drives a full multi-turn session end to end; cache hits are
free and provably identical; caps enforced; a killed worker leaves a task in a
recoverable state, not a lie.

---

### A8 — Diff proposals · ~2 weeks

The "INTELLIGENT" milestone (owner decision 5). Same discipline, extended from
retrieval to synthesis.

1. **Deterministic candidate generation** — the pool of possible additions:
   cards in field lists for this commander not in this deck, tag matches for
   under-covered roles, combo-graph completions, cluster-neighbor inclusions.
2. **Deterministic scoring** — field frequency with denominators, role-coverage
   delta, curve and color-source math (Karsten-style, from the existing mana base
   work), and where a strategy pack exists, paired ablation deltas from the
   simulator with common random numbers.
3. **The model groups and narrates the top-k diff**, and may choose among
   near-ties with a stated reason. It does not score and it does not rank.
4. **Output is a previewable changeset** — a `deck_documents` command list with
   `mutation_id`, never auto-applied. Every swap carries its evidence, and the
   originating `research_turn_id` is recorded on each entry so the deck can
   answer *"why is this card here?"* six weeks later. Given the existing command
   log, that provenance is nearly free and it is a genuinely rare feature.

**Acceptance:** on a seeded set of decks, every proposed diff passes legality,
color identity and the 100-card constraint deterministically; every swap carries
a citation; no diff applies without explicit acceptance; reverting is one
command.

---

### A9 — UI integration · separate branch · out of scope here

Consumes the interfaces above. The dock, the visual/tabular toggle, drag-and-drop
into the builder, the unread badge. Noted here only so the interfaces are
designed for it: the assistant must be usable as a persistent panel alongside the
builder, which means the thread API cannot assume a page of its own.

---

## 5. The eval harness

Built at A0, run at every phase, and the spine of the whole plan.

**Two modes, and the distinction matters:**

- **Substrate mode** — hand-written plans, no model. Measures the corpus layer.
- **End-to-end mode** — model-planned. Measures the corpus layer *plus* the
  model.

The difference between the two scores is the model's contribution, isolated. It
is the only way to answer "would a better model help?" without guessing.

**Scorecard, per category and overall:**

| Metric | What it catches |
|---|---|
| recall@k on `required_oracle_ids` | the substrate failing to find the answer |
| `forbidden_oracle_ids` hit rate | the substrate returning wrong things confidently |
| citation coverage | unsourced prose |
| cards-named-outside-result-set | hallucination (should be structurally 0) |
| bare-rate count | denominators being dropped |
| absence-stated rate on out-of-scope | improvisation instead of honesty |
| clarification appropriateness | asking when it should answer, and vice versa |
| cost per question, p50 / p95 | the economics of the bet |
| latency p50 / p95 | whether it is usable |

**A note on the golden set's honesty:** `required_oracle_ids` are hand-verified
by the owner, which makes them a judgment, not a fact. The set should record who
labeled each question and when, and questions whose labels are contested should
be marked rather than silently resolved. An eval set that quietly encodes one
person's opinion as ground truth produces a system that is confidently wrong in
exactly that person's blind spots.

---

## 6. Cost model

Per research question, with the A5 techniques applied:

| Component | Calls | Rough cost, cheap model |
|---|---|---|
| Clarification (CLASSIFY, tiny schema) | 0–1 | ~$0.001 |
| Plan (3× self-consistency, cached system prompt) | 3 | ~$0.004 |
| Tool execution | 3–8 | $0 — server-side |
| Narration over ~10–15k tokens of results | 1 | ~$0.01–0.02 |
| **Total** | | **~$0.02–0.03** |
| Cache hit on a repeated question | | **$0** |

Against the existing `annual_cost_ceiling_usd: 100`, that is roughly 3,000–5,000
research questions per year before the ceiling, before caching. Caching should
move that materially, since deck-building questions cluster hard around popular
commanders.

The frontier ceiling reference at G3 will cost perhaps 10× per question. It is
run over the ~150-question golden set a handful of times, not in production —
call it a few dollars total, and the cheapest information in this plan.

---

## 7. Cross-repo dependencies and outstanding decisions

**The dependencies are specified as self-contained work orders in
[`docs/upstream-dependencies.md`](docs/upstream-dependencies.md)** — eight items
across `ingestion_pipeline_mtg`, `commander_simulator` and this repository's
`deck-lab-refactor` branch, each with current state, requirement, acceptance
criteria, what this branch does in the meantime, and the integration handshake.
Each is written so a separate session can pick it up cold.

**Nothing there blocks the start of Track A.** Phases A0, A1, A2 and A4 build in
full against what exists today. The one P0 is **D1** — running the nightly
ingest end to end against real data, which has been the named blocker of record
since 2026-09-04 and which gates A3 entirely.

Two decisions remain the owner's rather than an implementer's:

| # | Decision | Recommendation |
|---|---|---|
| 1 | **Combo corpus (D4).** Ingest Commander Spellbook into `mtg_v1`, keep it local to Track A, or defer the category? | Ingest, or defer. Not local — that re-creates the duplicate-ingestion problem D2 exists to close. |
| 2 | **Embedding model swap.** MiniLM-L6 → bge-small changes the `reference_layer` index too. | One model, re-index both. Two encoders means two notions of similarity in one product. |

Two constraints are settled and recorded here because they shape A3 and A8:

- **`sim_study` returns `NotSimulated` until Track B lands.** Track A ships
  useful with zero strategy packs.
- **EDHTop16 sends no card quantities** (`STATE.md`, measured over 311 lists),
  and the missing cards are deduplicated basics correlated with color identity —
  mono-colored lists average 9.1 missing, five-color 0.1. Mechanic tag profiles
  are unaffected because Commander is singleton. **Mana-base comparison is
  affected with a direction**, so A3 excludes basics from every field-derived
  count and any land comparison restricts to `is_complete = true` with a stated
  denominator. See D6; this behaviour is correct regardless of whether D6 lands.

---

## 8. Sequencing and honest sizing

```
A0 ──▶ A1 ──▶ A2 ──▶ A3 ──▶ A4 ──▶ A5 ──▶ A6 ──▶ A7 ──▶ A8    ──▶ A9
 3d    2w    1.5w   1.5w    2w     2w     1w     1w     2w      (separate)
              G1                   G2     G3     G4
                                    ▲
                          the gate that matters

Track B (commander_simulator, parallel, independent):
B0 ──▶ B1 ──▶ B2 ──▶ B3 ──▶ B4
       ▲
   sizing gate — B1 tells you what B really costs
```

**~13 weeks for A0–A8**, one person with an agent, and that estimate is soft in
both directions. The first four phases carry no model at all and roughly 55% of
the calendar — which is the plan agreeing with the owner's thesis in the only way
a plan can.

**Start A0 and A1 immediately.** A1 is pure offline batch work with no LLM, no
UI and no cross-repo dependency; it is the asset that makes the driving example
answerable; and `effective_cost.py` is already most of the way into its first
family. It is also the piece that keeps paying off in A3, A4 and A8.

**Track B starts in parallel and is gated on its own B1 finding.** See the
companion plan — the honest sizing there is worse than it looks, for a reason
that is architectural rather than a matter of effort.
