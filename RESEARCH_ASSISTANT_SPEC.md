# Research Assistant — product and build specification

_Authored 2026-09-08. Branch `research-assistant`, worktree
`~/Projects/deck_lab/worktrees/research-assistant`, based on `deck-lab-refactor`
at f6f0bdd._

**This document supersedes [`RESEARCH_ASSISTANT_PLAN.md`](RESEARCH_ASSISTANT_PLAN.md)
as the authoritative statement of what is being built and in what order.** The
plan remains the reference for substrate detail — the mechanic tag format, the
retrieval fusion design, the Query IR, and the gate definitions — and those parts
are adopted here unchanged. Three of its product decisions are reversed, and one
of its self-descriptions is retired; §2 says which and why.

---

## 1. The promise

> **Understand your deck, investigate options, and test your ideas.**

The unit of value is not an answer. It is a **completed loop**:

```
question ──▶ evidence ──▶ experiment ──▶ decision ──▶ saved explanation
                 │                           │
                 └──────── re-enter ─────────┘
```

The player keeps authorship at every step. The assistant orchestrates the
corpus, the rules layer, the simulator and (later) the solver; it does not make
the change. Every capability it orchestrates is **also reachable by an ordinary
button** — the assistant is an accelerant on the Deck Lab, not a gate in front of
it.

Success is judged by three player outcomes, not by model quality:

1. A deck question is resolved faster than by hand.
2. The player can **restate what the result establishes** — and what it does not.
3. The player comes back to test another idea.

### 1.1 Naming — resolved

Deck Lab already has a top-level **Research** section (`/research`, commanders /
cards / metagame browse, `research_routes.py`, `research.py`). Shipping a
"Research Assistant" beside it produces two things called research that are not
the same thing.

**Decision (2026-09-08): the Deck Lab refactor's shipped taxonomy wins.** The
primary nav stays exactly as `deck_lab/base.html` renders it today — **Research**
and **Build**, in the header, the mobile drawer and the account popover — and
nothing is renamed. The assistant is **Ask** in every player-facing label.
"Research Assistant" is retained internally as the package (`assistant/`) and
document name.

Consequences, so the labels do not have to be rediscovered later:

- **Ask is not a nav item and has no page of its own.** It is a dock, opened from
  wherever the player already is, always bound to a context (§6.1). Adding a
  third nav link would imply a destination, and a destination invites exactly the
  behaviour §6.4 forbids — scrolling old messages to recover an experiment.
- Threads are reached through the deck or the research context they belong to,
  never through a global inbox.
- The four starting actions in §6.2 are phrased as questions because that is the
  dock's whole affordance.

---

## 2. What changes from the previous plan

| # | Previous plan | This spec | Why |
|---|---|---|---|
| 1 | UI is out of scope; A9, after ~13 weeks of headless work | **Thin UI pilot at R4, ~week 5–6**, with two actions | The plan's own thesis is that the substrate carries the intelligence. That is testable at two actions as well as at ten, and the *other* untested hypothesis — that players want this shape of help — cannot be tested headlessly at all. Thirteen weeks is too long to hold both risks open. |
| 2 | A8 "diff proposals" is the INTELLIGENT milestone, before UI | **Automatic diff proposals move to R8**, after variant comparison | Player-selected alternatives and player-authored variants serve the authorship goal directly, are far easier to evaluate, and do not require the deterministic candidate-generation and scoring machinery A8 assumes. An auto-proposed swap the player did not ask for is also the hardest thing in the product to prove is good. |
| 3 | Gate G4 measures citation *coverage* | **Two separate rigs**: a correctness rig and a usefulness rig, and citation *support* is measured, not just citation *presence* | A cited source can fail to support the claim attached to it. Coverage is a structural invariant (it should be 100% and a miss is a bug); support is a quality measurement and needs adjudication. Conflating them makes a passing scorecard uninformative. |
| 4 | "It is not a card evaluator. It does not say a card is good." | **Reasoned assessments of fit are in scope**, as a typed, visually distinct, citation-bearing tier | A feature restricted to reciting facts leaves most of the value behind, and it is the thing `bristly_billy_beane`'s reasoning is uniquely good at. §4 defines the guardrails that make this safe, and §4.3 shows it is already inside the `llm_may` charter rather than a change to it. |

Everything else from the plan is adopted: the mechanic tag corpus and its
precision discipline, hybrid retrieval with a local reranker, the typed
`ResearchPlan` IR with provenance-bearing result envelopes, oracle-id-only card
references, the golden question set, and the G1/G2/G3 gates.

---

## 3. Capabilities, in release order

| Capability | Example question | Output | Release |
|---|---|---|---|
| **Deck-aware discovery** | "Find instant-speed answers in my colors that also advance my engine." | Bounded card shortlist, the conditions each card matched, stated tradeoffs | R4 (first) |
| **Explain cards and interactions** | "Why would this card belong here?" | Mechanical explanation, relevant rules, dependencies, conflicts | R4 (first) |
| **Focused deck analysis** | "What should I investigate in this list?" | ~3 actionable findings, each linked to specific cards and evidence | R5 (first) |
| **Goldfish studies** | "Run 30,000 games against this assembly objective." | Assembly probability by turn, censoring, model coverage, saved report | R6 (supported-deck pilot) |
| **Variant comparison** | "Did these three changes improve consistency?" | Paired measurement of two player-authored lists, and what it cannot evaluate | R7 (next) |
| **Automatic diff proposals** | "What would you change?" | Previewable changeset, never auto-applied | R8 |
| **Solver decision studies** | "How does attempting now vs. waiting change across these scenarios?" | Conditional comparisons, sensitivity to declared assumptions | R9 (experimental) |

The assistant has **no deck-generation tool**. Generating a candidate from a
strategy pack stays where it is, on the deterministic `cedh/` path, reached by
its own button.

---

## 4. The epistemic contract

This is the core of the design and the thing that distinguishes the product from
a chatbot with a Magic prompt.

### 4.1 Four tiers, typed and visually distinct

Every assertion the assistant renders carries exactly one tier. The renderer
styles each tier differently and drops any assertion whose tier requirements are
unmet.

| Tier | What it is | Required citation | Renderer treatment |
|---|---|---|---|
| **Fact** | Card text, type, mana cost, colour identity, legality, rules text, rulings | `corpus:<snapshot_hash>` or `rules:<CR section>` or `ruling:<oracle_id>/<date>` | Plain, no hedging |
| **Field evidence** | What recorded tournament lists actually contain | `field:<commander>/<window>/n=<denominator>` — denominator, window and event-size floor are **required struct fields**, not prose | Always rendered with its denominator inline |
| **Measurement** | Simulator output | `sim:<simulation_input_sha256>` plus the honesty envelope (§5.2) | Never rendered without coverage and censoring |
| **Interpretation** | The model's reasoned assessment of fit | ≥1 citation drawn from the tiers above, and a `confidence` enum | Visually marked as interpretation, attributable to the assistant |

An interpretation with zero supporting citations is dropped by the renderer. That
is a structural rule, not a prompt instruction.

### 4.2 The shape of a good interpretation

The target register, from the product brief:

> "This card supports your recursion plan, but its activation competes with the
> mana you need for your commander. Its tournament inclusion supports
> investigating it; that alone doesn't establish that it improves this list."

Three things are happening there and all three are required by the schema:

- A **mechanical claim** tied to card text (Fact).
- A **stated tension**, not just a benefit.
- An **explicit statement of what the evidence does and does not establish**.

`InterpretationAssertion` therefore carries `claim`, `tension` (nullable but
prompted for), `supports` (citation ids), and `does_not_establish` (required,
non-empty). An interpretation that cannot name its own limit does not ship,
for the same reason a tag with an empty `limitations` field does not ship.

### 4.3 Why this is inside the charter

`CLAUDE.md`'s `llm_may_not` forbids: deciding legality, inventing cards or oracle
text, bypassing candidate constraints, creating simulator mechanics, silently
repairing a malformed simulator result, and **selecting from the whole card
corpus without deterministic narrowing**. None of those is fit assessment.
`llm_may` already permits "compare deterministic alternatives" and "explain
recommendations."

So the guardrail that matters is the last `llm_may_not` clause, and it is
preserved exactly: **every card the assistant assesses arrived from a query
result set as an `oracle_id`.** The model never reaches the corpus. It reasons
about a bounded, deterministically retrieved set, which is what "compare
deterministic alternatives" describes.

**ADR-029** records this: the assistant may render Interpretation-tier
assertions, subject to §4.1's citation requirement and §4.2's schema, and the
previous plan's "not a card evaluator" self-description is retired.

### 4.4 What must never appear

Inherited from the charter and enforced structurally, not by prompt wording:

- **No card name outside a result set.** The field is an `oracle_id` checked
  against the executor's outputs before rendering.
- **No rate without its denominator, window and event-size floor**
  (`popularity_is_not_quality`).
- **No invented neutral score.** Absence renders as absence
  (`absence_is_visible`).
- **No card price and no owned-cards input, anywhere** (ADR-025). The assistant
  inherits this without exception: no price citation kind exists, no retrieval
  filter reads a price column, and the prompts forbid mentioning cost. Note the
  legacy `card_fit.txt` passes `Price: ${price}` into the prompt — that is one of
  the generator assumptions §8.1 removes.

---

## 5. Experiments: what a measurement establishes

### 5.1 Capability is stated at the question level, not as a score

A single "82% supported" badge conceals the thing the player needs. The
assistant returns a `CapabilityStatement` before it returns a number:

```
can_measure:
  - "this assembly line" (objective id, from the pack)
  - "these mana and card-flow effects" (modeled_cards, by effect class)
cannot_measure:
  - "this deck's opponent-dependent value engine"  [reason: opponent_trigger]
  - "the proposed replacement's relevant ability"   [reason: unauthored]
```

The reason codes come straight from `coverage.inert_by_reason` in
`cedh-simulation-result.v3`: `interaction`, `opponent_trigger`,
`opponent_permanent`, `timing_only`, `no_object_in_model`, plus
`unauthored_cards`.

**Today those are counts, not lists.** Naming the specific affected cards — which
is what makes the statement actionable — requires **D3** in
[`docs/upstream-dependencies.md`](docs/upstream-dependencies.md), the structured
per-card inert/unauthored list (`result.v4`). Until D3 lands, R6 ships the
by-reason counts and the pack's declared `known_blind_spots`, and says plainly
that it can report how many cards are unmodeled but not which. That is a smaller
claim honestly made, and it is the correct behaviour regardless of whether D3
ever lands.

### 5.2 Acceptance is not representation

The simulator has **one installed registry pack**,
`kinnan-midrange-goldfish@1.0.0`, supporting one commander oracle id. It also has
a reserved generic execution mode, `derived-generic@1.0.0`
(`data/derived-generic.defaults.toml`), which is explicitly *not* a registry pack
and cannot be selected accidentally.

The generic mode declares its own blind spot in the file:

> `"only inherited card-set patterns can terminate a run"`

That sentence is the whole risk. A deck submitted under generic execution can be
**accepted, executed, and return a completely censored result** — not because the
deck is slow, but because the model has no terminal state for its plan. A player
reading "0% assembled by turn 12" would draw exactly the wrong conclusion.

Therefore:

- **R6 is Kinnan-scoped.** Only the authored pack's commander gets a goldfish
  study in the first release.
- Generic execution is not offered to players in R6. When it is offered (R7+), a
  run whose censoring exceeds a configured threshold **with no matched inherited
  pattern** renders as `NotRepresented`, not as a probability. This is
  `absence_is_visible` applied to a result that technically validated.
- The `strategy_pack.derived` boolean in `result.v3` is surfaced in every study
  report header, in words.

### 5.3 More games is precision, not realism

The study header is fixed copy:

> **Goldfish study · 30,000 games · Deck version 12**
> Objective: reach the declared assembly state.
> Results: assembly probability by turn, games that never assembled within the
> horizon, and effects the model could not evaluate.

And, beside the game count:

> More trials make this estimate more precise **within the model**. They do not
> make omitted interactions more realistic.

The objective and the coverage are given more visual weight than the game count.

### 5.4 30,000 games is feasible; the numbers, and what they are not

| Measurement | Value | Source |
|---|---|---|
| Service cap on games per request | `SIM_MAX_GAMES=60000`; above it, 400 | `STATE.md` |
| Deck Lab client today | `games=20000`, `objective_turn=3`, `timeout_seconds=330.0` | `cedh/simulator.py`, `config/cedh.yaml` |
| 20,000-game single run | **0.68 s** | Native GHA `ubuntu-latest` x64, seed 1, **two threads, idle runner**, manual run `33906314023` |
| Same, emulated linux/amd64 on the arm64 Mac mini | 2.641 s | `STATE.md` |
| 30,000-game **full 98-ablation sweep** | 297.61 s | Same runner |
| Same sweep, emulated on the Mac mini | 1,115.99 s | `STATE.md` |

Raising the request to 30,000 games is a **parameter change**, not new
machinery. Two cautions carry into the spec:

1. **Neither timing is a production latency or a concurrency measurement.** They
   are single runs on idle machines. The interactive tier is `shared-cpu-2x` with
   `SIM_MAX_CONCURRENT=1`; excess requests receive 429. The load test that would
   size this is specified in the simulator's `docs/hosting-cost-model.md` and
   **has not been run**. R6 therefore treats a study as an async job (§7.3) from
   the first commit, and does not assume sub-second response.
2. **The client's 330 s timeout exceeds the service's `SIM_TIMEOUT_SECONDS=300`.**
   That ordering is correct — the client should outlast the server so it receives
   the server's own error rather than inventing one — but it means a study's
   worst case is a five-minute server-side kill, which the job model must render
   as a failure, not a pending state.

Full card-ablation sweeps are a different workload entirely: they run on the
separate `sim-sweep` batch tier, are gated server-side by `SIM_ALLOW_SWEEP=1`
plus a bearer token (403/401 otherwise), and `ablate` is capped at
`SIM_MAX_ABLATIONS=8` on the interactive tier. **The assistant never requests a
sweep.** If sweep-backed analysis is wanted later it is an admin-triggered batch
job with its own budget, not a chat turn.

### 5.5 Removal is not replacement

An ablation removes a card's *effect*. A swap replaces a card with another card.
These answer different questions and the second one is what players ask.

- Existing leave-one-out ablations are a **diagnostic** — "how much does the
  model's outcome depend on this card being present" — and are labelled as such.
- **A swap requires both lists executed.** That is R7, and it is why R7 is a
  phase rather than a flag on R6.
- Paired comparison needs a validated method. The simulator already supports
  common random numbers with a measured 28× variance reduction on the sweep path;
  R7's acceptance criterion is that the paired interval is validated against
  independently seeded arms before any comparison is shown to a player.

### 5.6 Studies are objects with a lifetime

Each saved study records: the list (by `deck_sha256`), the deck document revision,
the settings (`games`, `objective_turn`, pack id/version/`content_sha256`,
`simulation_input_sha256`), the full result envelope, and **the player's recorded
decision**.

After a relevant edit — determined by comparing `deck_sha256`, since it covers
the list and nothing else (ADR-025) — the report is labelled **"Applies to an
earlier list"** and offers a rerun. It is never silently updated and never
silently shown as current.

The provenance plumbing for this is nearly free: `deck_documents.py` already
carries an event log, monotonic `revision`, `expected_revision` optimistic
concurrency, and `mutation_id` on `apply_commands`. Recording the originating
`research_turn_id` on a deck entry is one column, and it is what lets the deck
answer *"why is this card here?"* six weeks later.

### 5.7 A hard input constraint, stated up front

The simulator requires **exactly 99 library cards** plus 1–2 commanders
(`coverage.library_cards` is `const: 99`). A work-in-progress deck document of 63
cards **cannot be simulated at all**.

This is not an edge case; it is the normal state of a deck in the builder. The
"Test a variant" action is therefore disabled with a stated reason — *"a study
needs a complete 100-card list; this one has 64"* — rather than failing after the
player waits. Discovery, explanation and analysis all work on incomplete lists
and are unaffected.

---

## 6. The interface

### 6.1 Shape — resolved against the refactor's existing idioms

**Decision (2026-09-08): mobile is in scope, and the dock reuses the refactor's
`.dl-add-panel` pattern rather than inventing a new one.**

`CLAUDE.md`'s `ui_scope: "Desktop web UI ... No mobile."` is **stale**. The
refactor ships a real breakpoint at `@media (max-width: 767px)` in
`deck-lab.css` with substantial rules, plus `dl-drawer` / `dl-menu-button`,
`dl-bottom-action`, `desktop-only` opt-outs, `viewport-fit=cover` and
`env(safe-area-inset-bottom)`. R0 corrects the charter line (§7.5).

An earlier draft of this section put the dock "beside the existing
`dl-stats-rail`." **That was wrong on inspection and is corrected here**, because
the refactor's own CSS rules it out twice: the builder is a fixed two-column grid
(`grid-template-columns: minmax(0,1fr) 292px`), so a third rail has no column;
and at the mobile breakpoint `.dl-stats-rail{display:none}`, so a dock parented
to it would vanish exactly where the brief wants a full-screen view.

The refactor already contains the right pattern, twice:

| Existing | Desktop | At `max-width: 767px` |
|---|---|---|
| `.dl-add-panel` (Add cards) | 360px slide-over, `top:70px`, `translateX(-101%)` → `.open` | `top:0; width:100%; z-index:120` — **full screen** |
| `.dl-filters` (Research) | 250px sticky sidebar | bottom sheet, `max-height:84vh`, `border-radius:18px 18px 0 0` |

So:

- **Builder:** Ask is a slide-over **mirroring `.dl-add-panel` on the right**
  (`right:0`, `border-left`, `translateX(101%)` → `.open`), overlaying the stats
  rail rather than competing with it for grid space. It reuses the existing
  open/close mechanism verbatim — `classList.toggle("open", …)` plus the
  `aria-hidden` flip and focus move, as `deck-lab-builder.js` already does for
  `#add-panel`. Substantial studies expand into a full report view.
- **Mobile:** the same element, full-screen via the same breakpoint rule that
  `.dl-add-panel` already uses. The deck title stays in the panel header so deck
  context is never lost. **Not** the `.dl-filters` bottom sheet — 84vh is fine
  for a filter list and cramped for a conversation with charts.
- **Research pages:** the same dock, same full-screen mobile treatment,
  pre-bound to the current commander, active filters and evidence window.

#### Required behaviours (approved 2026-09-08)

Three are integration details that will be bugs if missed, and the fourth is the
one that reuse does **not** give you for free:

1. **Mutual exclusion with Add cards.** Both are ~360px overlays. Opening one
   closes the other; below ~1100px they are never both open. This is a shared
   open/close controller, not two independent toggles — two independent toggles
   is how you get both panels open at 900px with the deck invisible behind them.
2. **Feedback-launcher suppression.** `deck-lab.css` line 136 already hides
   `.feedback-launcher` under `:has(.dl-add-panel.open)`, `:has(.dl-filters.open)`,
   `:has(.dl-drawer.open)` and `:has(dialog[open])`. The Ask panel's selector
   joins that list, or the launcher floats over the dock.
3. **Accessible focus handling.** `deck-lab-builder.js` currently does the
   minimum for `#add-panel` — `classList.toggle("open", …)`, an `aria-hidden`
   flip, and a focus move to the search input. That is the floor, not the
   ceiling, and a conversational panel needs more than a search box does:
   - **Focus trap while open.** Tab and Shift+Tab cycle within the panel.
   - **Escape closes**, and **focus returns to the control that opened it** —
     the toolbar Ask button, the bulk-actions button, or the row action, as
     appropriate. Losing focus to `<body>` on close is the most common form of
     this bug.
   - **`aria-hidden` is not sufficient on its own.** An `aria-hidden="true"`
     subtree that is still in the tab order is reachable by keyboard and
     invisible to a screen reader simultaneously, which is worse than either.
     Pair it with `inert` on the panel when closed, or make the closed state
     genuinely unfocusable.
   - **Results are announced.** The results region is a polite live region, as
     `#table-view` already is (`aria-live="polite"`), so an answer arriving
     after a long job is not silent for a screen-reader user.
   - **Reduced motion.** The `.2s` slide inherits `deck-lab.css`'s existing
     `prefers-reduced-motion` block; confirm it does rather than assuming it.

#### Verify the mobile behaviour; do not assume one CSS rule completes it

Mirroring `.dl-add-panel` makes the full-screen treatment **cheap**, not
**automatic**. The following are known not to be covered by the mirrored rule and
must be checked on a real device or an emulated viewport before R4 is called
done:

| To verify | Why it is not free |
|---|---|
| `.dl-bottom-action` with two buttons | It currently styles a single `.dl-button` at `width:100%`. Adding Ask beside Add cards needs a two-up rule, or the second button wraps |
| Panel height under the mobile keyboard | A conversation has a text input at the bottom. `100vh` and the on-screen keyboard interact badly on iOS; `dvh` or a visual-viewport listener may be needed. The Add cards panel never had a bottom-anchored input, so this path is untested |
| Safe-area insets | `.dl-bottom-action` already uses `env(safe-area-inset-bottom)`; the dock's own composer needs the same or it sits under the home indicator |
| Scroll containment | The panel scrolls its transcript; the page behind must not scroll with it |
| `.dl-bulk-controls` is `desktop-only` | "Ask about selection" has no mobile home in the current markup — §6.2 routes it to the row action sheet, which is new work, not a mirrored rule |
| The 767px boundary itself | A 768–1100px tablet gets the desktop overlay *and* rule 1's mutual exclusion. That band is the one nobody looks at |

Treat the mirrored CSS as the starting point that removes the layout work, and
budget explicit device verification inside R4 rather than after it.

### 6.2 Discoverable starting actions

**Find cards · Explain selection · Analyze deck · Compare versions**

These are buttons. Each has an identical conversational form. Where they live,
in the refactor's existing furniture:

| Entry point | Placement |
|---|---|
| Builder, desktop | An **Ask** button in `.dl-builder-toolbar`, beside `Add cards` |
| Builder, mobile | A second button in `.dl-bottom-action` — which today holds one full-width button and needs a two-up rule |
| Card selection | **Ask about selection** in `.dl-bulk-controls`, beside the existing bulk-move control. Note that group is `desktop-only`; on mobile the selection question is offered from the row action sheet instead |
| Research pages | An **Ask** button in the research toolbar, carrying the active commander, filters and window |
| Deck list | **Analyze deck**, per §6.5 |

### 6.3 Results are usable objects, not prose

| Result kind | Affordances |
|---|---|
| Card results | The builder's ordinary consider / add controls, into a chosen zone |
| Rules answers | Expandable source excerpts with CR section ids |
| Findings | Highlight the affected entries in the deck list |
| Study reports | Charts, comparison tables, the capability statement, the honesty header |
| Saved notes | The player's own recorded reason for a change |

### 6.4 Things the assistant must never require

- Copying the deck into a prompt.
- Restating the commander.
- Scrolling old messages to recover an experiment.

Long jobs survive navigation and return to the same deck (§7.3).

### 6.5 "Analyze deck" specifically

A button beside the deck; the same action available in conversation.

1. It uses **the current saved list**. No questionnaire.
2. It shows **its understanding of the deck's plan as an editable assumption**,
   at the top, before the findings. The player can correct it and re-run. This is
   the single most important element on the screen: it is where a wrong analysis
   becomes visibly wrong instead of confidently wrong.
3. It returns **~3 high-value findings**. Each answers, in this order:
   1. What did you find?
   2. Why does it matter *here*?
   3. What evidence supports it?
   4. What can I do next?
4. Next-step actions: **Show affected cards · Find alternatives · Inspect
   interaction · Test a variant**.

Two prohibitions, both of which are how this feature fails:

- **Analysis is not an obligation to find fault.** *"I couldn't establish a
  problem from the available evidence"* is a valid and shippable result. The eval
  set includes healthy decks precisely to measure whether the assistant
  manufactures findings on them.
- **Do not restate the sidebar.** Curve, colour counts and type distribution are
  already on screen. A finding that only reports one of them is spam and is
  counted as such in the usefulness rig.

For a supported list, a goldfish study is attached. For an unsupported list, the
research analysis completes in full and names **precisely which experiment is
unavailable and why** — which is a `CapabilityStatement`, not an error.

---

## 7. Architecture

### 7.1 Ownership

| Owner | Responsibility |
|---|---|
| **Deck Lab** | Context, permissions, jobs, saved reports, all UI |
| **The model** | Translate questions into bounded tool operations; explain their outputs |
| **The simulator** | Its own calculations, its own honesty fields |
| **The solver** | Its own calculations, its own declared field and expiry |

The model plans and narrates. It does not retrieve, rank, score, or compute.

### 7.2 Packages

```
src/sabermetrics/
  mechanics/    pure functions: card text -> mechanic tags. stdlib + re only.
  substrate/    the indexed knowledge layer. reads mtg_v1, writes local indexes.
  assistant/    query IR, executor, planner, narrator, threads, eval harness.
```

Import rules, asserted by a test over the import graph in the style of the
existing `cedh_no_legacy_ingestion` check, adopted from the plan §3.1 unchanged.
`assistant/` sits **beside** `cedh/`, borrowing `model_gateway`, `cost_ledger` and
`simulator`; `cedh/` may not import `assistant/`.

Module promotion from `analytics/` (plan §3.2) is adopted with its completed
audit: four pure modules move with deprecated re-export shims. `role_tagger.py`
stays behind because it reads configuration and performs scored classification;
`cvar.py`,
`empirical_valuation.py`, `card_win_equity.py`, `cluster_valuation.py` and
`synergy_matrix.py` stay behind because they encode the budget/casual objective.

### 7.3 Jobs

Reuse the existing `BuildJobsRepo` + `ThreadPoolExecutor` pattern from
`cedh_routes.py` verbatim. No broker, no SSE, no second process — *locality over
distribution*.

- `research_thread` (owner, title, optional `deck_id`, unread flag)
- `research_turn` (role, content, structured payload, citations, cost)
- `research_task` (status: `queued → clarifying → planning → searching →
  simulating → writing → done | failed`, progress notes, result)

**Progress notes are the product, not decoration.** *"searching corpus: phyrexian
mana in cost… 23 found… profiling 42 recorded Vivi lists…"* makes a wait legible
and makes a wrong answer traceable to the wrong query.

One in-flight task per user; a global queue-depth cap; visible queue position.

**Two different constraints bind at two different phases, and an earlier draft
conflated them.** They have different fixes, so they are separated here:

| Phase | What binds | Why |
|---|---|---|
| **R4–R5** | The web tier: one `shared-cpu-1x` machine running Flask, SQLite, a CPU embedding model **and** a cross-encoder reranker in-process | The reranker is the expensive resident. Nothing in R4 touches the simulator, because `sim_study` does not exist until R6 |
| **R6+** | `SIM_MAX_CONCURRENT=1` on the interactive simulator tier; excess receives 429 | One study in flight across all users, on a separate machine |

The R6 constraint is the harder one and it is a **queue** problem, not a sizing
problem — which is why studies are async jobs with a visible queue position from
their first commit (§5.4). The R4 constraint is a **memory and CPU residency**
problem on the web tier, and it is what sizes the pilot audience (§9, R4).

**`SIM_MAX_CONCURRENT=1` is not evidence that the R4–R5 residency constraint is
addressed.** It is a limit on a different machine, on a workload that does not
exist until R6. Serializing simulations says nothing about whether a
cross-encoder and an embedding model fit in a `shared-cpu-1x` alongside Flask and
SQLite while three testers ask questions. The residency question is answered by
measuring resident memory and p95 latency for a concurrent question on the web
tier — which is R4 acceptance work, not something inherited from the simulator's
configuration.

A killed worker leaves a task recoverable, not lying.

### 7.4 Caching and budget

Plan and result cached on `(clarified_intent_hash, corpus_snapshot_hash,
tag_version, prompt_version)`, extending the gateway's existing `cache_key`, which
deliberately excludes the model id so a model swap is visible rather than hidden.
A repeated question is free.

Budget: a per-user monthly research-question cap, configurable and not surfaced
as a number, **alongside** the existing global
`settings.llm.monthly_cost_ceiling_usd` hard stop, which remains the only hard
stop (ADR-024). Research questions are an unbounded surface in a way manual deck
builds are not.

### 7.5 Stale charter lines, to fix in R0

Five lines of record are stale and each would mislead someone implementing
against this spec. All are corrected in R0. (An earlier draft of this section
listed three and named one of them wrongly; the audit that produced the table
below checked each against the code.)

| Stale line | Reality | Why it matters here |
|---|---|---|
| `CLAUDE.md` `cloud_alignment`: "simulator over private HTTP with **cedh-simulation-result.v2** validation" | `cedh/simulator.py` pins **v3** and vendors `contracts/cedh-simulation-result.v3.schema.json` | §5.1's capability statement reads `coverage.inert_by_reason`, which exists in v3 and **not** in v2. A reader of the stale line would conclude the field is unavailable and design around its absence |
| `cedh/simulator.py:223` `_parse_http_result` docstring: "Validate and adapt a **v2** service document" | The function validates v3 | Same, at the point of use. **Correction:** an earlier draft called this function `_adapt`; no such symbol exists in the repo |
| `cedh/simulator.py:119` `SimulationResult` class docstring: "A validated `cedh-simulation-result.**v1**` document" | Its own `schema_id` literal nine lines below says v3 | A fourth stale string, missed by the earlier draft. A class whose docstring and its own field literal disagree is worse than either being wrong alone |
| `CLAUDE.md:418`: "Full text for **ADR-001..027** is in `design.md` Section 11" | `design.md` §11 holds **ADR-001..014 only**. ADR-015..027 exist solely as one-line rows in CLAUDE.md's own table | Anyone following the pointer to read the rationale for a settled decision finds nothing. Worse: `docs/project_plan/sabermetrics_v2_spec.md` §3 defines its **own** ADR-015..020 for entirely different decisions, so the numbers collide |
| `CLAUDE.md` `ui_scope`: "Desktop web UI ... **No mobile.**" | The refactor ships `@media (max-width: 767px)` with substantial rules, a mobile drawer, `dl-bottom-action`, `desktop-only` opt-outs and safe-area insets | §6.1 resolves mobile as in scope. Leaving the charter contradicting the shipped CSS means the next person to read it builds the wrong thing, or reverts the right one |

Correcting a charter line is a deliberate act, not housekeeping: the `ui_scope`
edit is recorded with its reason so it reads as the refactor's decision being
written down, not as scope quietly widening.

---

## 8. What to preserve from the legacy reasoning

The most valuable thing in `bristly_billy_beane`'s reasoning layer is its ability
to explain **why a mechanic matters in this particular deck**. Concretely,
`reasoning/prompts/profile_synthesis.txt` already distinguishes:

- **Engine vs. dependent output** — cards that *feed* an engine from cards that
  merely *resemble its outputs*. Its worked example (a lifelink creature drains
  life but does not increase Aura count, so it is a **false synergy**) is exactly
  the reasoning the assistant needs and exactly what "find more draw" cannot
  express.
- **Value inversion** — commander abilities that change normal card evaluation,
  typed as stat / keyword / cost / quantity inversions, each with
  `desired_characteristics` **and** `undesired_characteristics`.
- **Mechanical support vs. superficial text similarity.**
- **Strategic dependencies and conflicts** — including anti-synergies among
  popular cards.

That vocabulary becomes the assistant's **vocabulary for investigation**. It is
the difference between:

> "Find more draw."

and

> "Find draw that works with my casting restrictions and doesn't depend on
> opponents."

### 8.1 Reuse, and what must be stripped

**Reuse:** mechanic detection (`effective_cost.py`, `oracle_keywords.py`,
`oracle_patterns.py`, `theme_patterns.py`, `role_tagger.py`), the retrieval and
`reference_layer/` infrastructure (built, idle, and exactly right for the rules
category), the strategic concepts above, and the evidence infrastructure in
`cedh/evidence.py`.

**Audit and rewrite the prompts for research.** They are generator prompts and
carry generator assumptions that must not survive the move:

| In the legacy prompts | Disposition |
|---|---|
| `card_fit.txt`'s `fit_score` 1–10 and its calibration rubric | **Removed.** A numeric fit score is a ranking, and ranking is deterministic and not the model's job. Interpretation-tier prose (§4.2) replaces it. |
| `Price: ${price}` and `Average Deck Price` | **Removed.** ADR-025, no exceptions. |
| WotC bracket power level 1–5 | **Removed.** ADR-019: cEDH only. |
| `cwe_score`, `cooccurrence_avg` | **Removed** from the assistant. These are the casual-objective valuations §7.2 leaves behind. |
| EDHREC inclusion % as a bare number | **Replaced** by Field-evidence-tier assertions with required denominator, window and event-size floor. |
| Value inversions, engine dependencies, false-synergy warnings, anti-synergies | **Kept**, restructured into typed Interpretation assertions with `does_not_establish` required. |

**Keep outside the assistant:** the generator's selection loop, budget
optimization, and all numerical card-fit scoring.

---

## 9. Phases

Sizes are order-of-magnitude for one person working with an agent, and are the
least reliable numbers in this document.

```
R0 ──▶ R1 ──▶ R2 ──▶ R3 ──▶ R4 ──▶ R5 ──▶ R6 ──▶ R7 ──▶ R8 ──▶ R9
1w    1w    1.5w   1.5w   1.5w    2w    1.5w    2w     2w    (exp)
             G1     G2     G3+G4
                     ▲      ▲
              the gate that  first release,
              matters        in players' hands (~wk 6.5)
```

### R0 — Foundations, both measuring rigs, ADRs · ~1 week

1. Package skeletons; import-graph and `mechanics/`-purity tests from day one.
2. Promote the five modules with deprecated re-export shims.
3. **Golden question set** (`assistant/eval/questions/*.yaml`), ~80 at R0 growing
   to ~150, each carrying the ask, a clarified variant, `required_oracle_ids`,
   `forbidden_oracle_ids`, `expected_absences`, category, difficulty, **and the
   labeller's identity and date**. Categories per the plan §4/A0, plus a new
   **healthy-deck** category (§6.5) that must produce no manufactured finding.
4. **Both rigs stood up** (§10), reporting an all-zeros scorecard.
5. Fix the three stale charter lines (§7.5): the two v2/v3 references, and
   `CLAUDE.md`'s `ui_scope`.
6. **ADR-029** (Interpretation tier is permitted; §4.3) and **ADR-030**
   (assistant sits beside `cedh/`; query-planner rule; `assistant_may` /
   `assistant_may_not`).
7. **Propagate the naming decision** (§1.1) before any label exists to migrate:
   the package is `assistant/`, the player-facing word is **Ask**, and the
   refactor's Research / Build nav is untouched.
8. **Fix D0a, the missing-absence bug** (§14.1) — warnings on the
   `gateway is None` early returns in `lab.py:_summarise_evidence` and `_explain`;
   persisted build-time warnings passed to `candidate.html`; an `{% else %}` on
   the explanation block. The candidate page deliberately does not reconstruct
   current `modes`: those could misdescribe a historical build after provisioning
   changes.
   This is in R0 rather than R4 because the assistant inherits the pattern, and
   building R4 on a silent-absence precedent propagates it.

**Acceptance:** tests green; legacy imports unbroken; golden set validates; both
rigs run and report zeros; **with no model credential set, a candidate page states
the absence of a narrative rather than omitting the section** — asserted by a
test, since that is the invariant R4 depends on.

#### R0 definition of done

Every line is a command that exits 0 or non-zero. **`python scripts/check_r0.py`
runs all of them and is the single gate**; `--verbose` prints each step. A box is
ticked only when that command passes on a clean tree.

- [x] **Regression parity.** `ruff check src tests`, `black --check src tests`,
      `mypy src`, `pytest -q` all pass, and the suite is at or above the
      pre-R0 baseline of **1288 passed / 31 skipped**. The count is pinned in
      `scripts/check_r0.py`, so a test deleted to make the gate pass fails it.
- [x] **D0a — absence is visible.** `pytest -q tests/test_cedh_lab.py -k AbsenceOfAModel`
      and the two `test_cedh_ui.py` absence tests pass. Verified by reverting the
      fix and confirming all five fail.
- [x] **Package boundaries.** `pytest -q tests/test_package_boundaries.py` — 16
      tests, including four evasion cases (aliased submodule, dynamic
      `import_module`, `__import__`, lazy in-function import) and a
      false-positive guard on `pipelines_new`.
- [x] **`mechanics/` is pure.** Asserted three ways: stdlib-only imports, no
      `sabermetrics` import, and no filesystem call anywhere in the package.
- [x] **Promotion without breakage.** Four modules promoted, shims at every old
      path, and **no consumer file edited**. The suite proves it: 15 files import
      these modules and none was touched.
- [x] **Stale charter lines corrected.** `pytest -q tests/test_research_assistant_charter.py`
      binds each doc claim to the code that decides it, rather than to a literal
      string, so the next drift fails rather than rots.
- [x] **Golden question set.** 80 questions load and validate against a
      schema, with labeller and date recorded per question.
- [x] **Eval rigs.** `python -m sabermetrics.assistant.eval --mode substrate`
      emits a scorecard whose quality metrics are `0.0` and whose invariants read
      `not_measured` over zero assertions — **not** 100% coverage over nothing,
      which would be an invented number.

**R0 is complete as of 2026-09-08; `python scripts/check_r0.py` is the evidence.**
Two things about this checklist are deliberate:

**One task here cannot be made machine-checkable, and pretending otherwise would
be the worst outcome.** A golden question's `required_oracle_ids` are a human
judgement about what a good answer contains. The schema can check the field
exists, is a list, and resolves to real cards; it cannot check the judgement. The
honest substitute is the one §10.3 already states — record who labelled each
question and when, carry a `contested` flag, and treat a contested label as
data rather than as something to resolve silently.

**The gate asserts a floor, not equality.** `pytest` must report *at least* 1288
passed; adding tests is progress, and pinning equality would punish it. Skips are
pinned at exactly 31, because a test that starts skipping is a test that stopped
running and that is precisely the failure a count is for.

### R1 — Mechanic tags, first two families · ~1 week · no LLM

Narrowed from the plan's five families so the pilot can happen. Ship `cost:*` and
`mana:*` complete; the remaining families (`draw:*`, `interact:*`, `win:*`)
continue in the background and land through R2–R5.

Tag format, precision discipline (≥0.95 on fixtures, ≥20 hand-labelled fixtures,
a **required non-empty `limitations` field**), `matched_span` non-optional, and
the coverage report of untagged cards grouped by type line: all adopted from the
plan §4/A1 unchanged.

**Acceptance:** ≥25 tags shipped across two families; every tag ≥0.95 precision;
coverage report printed; rebuild deterministic and content-hashed.

#### R1 definition of done

Every line is a command that exits 0 or non-zero. **`python scripts/check_r1.py`
runs all of them and is the single gate**; `--verbose` prints each step, and
`--skip-r0` omits re-running R0 first. R1 sits on top of R0, so the gate runs the
R0 gate before its own — a green R1 over a red R0 measures the wrong thing.

- [x] **Regression parity.** `ruff check src tests`, `black --check src tests`,
      `mypy src`, `pytest -q`, at or above the post-R0 floor of **1325 passed /
      31 skipped**, pinned in `scripts/check_r1.py`.
- [x] **≥25 tags across two complete families.** Read from the shipped registry,
      not from a file count, and both `cost:*` and `mana:*` must be non-empty.
- [x] **Every tag ≥0.95 precision on real oracle text.**
      `sabermetrics tags verify` recomputes precision and recall from
      `fixtures/mechanics/tag_cards.json` and fails on any gap between the figure
      a definition **declares** and the figure it **measures**.
- [x] **Coverage report.** `sabermetrics tags coverage` prints the untagged set
      grouped by card type — the analogue of the simulator's inert table.
- [x] **Deterministic and content-hashed.** Two dry-run builds of the same
      library against the same snapshot must print the same `content` hash.

**R1 is complete as of 2026-09-09; `python scripts/check_r1.py` is the
evidence.** The shipped registry contains 28 tags (17 cost, 11 mana). Its 581
hand-labelled fixture assignments cover 175 unique cards with real oracle text;
all 28 definitions measured 1.0 precision and 1.0 recall on those fixtures. A
full 34,551-card development snapshot produced 6,664 tag rows across 4,583 cards
and reported the remaining 29,968 cards as untagged. The completion run reported
1,410 passed and 31 skipped tests.

Seven decisions were taken while building this and each would otherwise have to
be rediscovered from the code:

**Tag definitions are Python data in `mechanics/`, not YAML in `config/`.**
`mechanics/` may not touch the filesystem, which is the rule that kept
`role_tagger` out of it, so a YAML tag library would have had to live in
`substrate/` and the predicates would have separated from the family that owns
them. The definitions are frozen dataclasses instead: one file per family, as the
plan asks, checked in and versioned.

**The purity rule was narrowed, on purpose, to let `mechanics/` import itself.**
R0 shipped the package as four independent modules, so "imports nothing from
`sabermetrics`" and "imports nothing from a sibling package" were the same rule
and were written as the former. One predicate algebra shared by two family files
makes them different rules, and the one always meant is the latter. The exemption
is segment-bounded and its own test proves it does not spare a
`sabermetrics.mechanics_v2`; the three invariants the rule exists for — no sibling
package, no third-party import, no filesystem — are unchanged.

**`confidence` is measured fixture precision, and it is named for that.** It is
not a per-card probability that a tag is correct. A definition declares the
number; `tests/test_mechanic_tags.py` recomputes it from the fixture cards and
fails on drift, so it cannot become a claim nobody checks.

**`matched_span` is non-optional structurally, not by convention.**
`always_yields_span` walks the predicate at construction and refuses any tag that
could match without producing a span — a bare mana-value bound, or a lone
negation. A tag row that asserts a mechanic with nothing to point at is an
unattributable claim, which is what a tag exists not to be.

**Reminder text is masked before matching, and masking preserves offsets.** The
cascade reminder contains "without paying its mana cost"; the convoke reminder
contains "help cast this spell". Parenthesised runs are overwritten with spaces
rather than deleted, so a span taken from masked text still indexes the real
printed text a player would see.

**Tags stay atomic; compositions belong to the query layer.** The plan asks for
"mana rock (net-positive vs. net-neutral)". That comparison is arithmetic between
a printed cost and a printed production, and baking it into a tag would put a
derived judgement inside `mechanics/`. It ships instead as
`mana:adds_two_or_more` joined with a mana-value filter at query time, and both
tags' `limitations` say so, so the composition reads as intended rather than
missing.

**Near-miss quality of negative fixtures is a human judgement and is not faked.**
Whether a given negative is a near miss for a given predicate is the same kind of
claim as a golden question's `required_oracle_ids`, which §10.3 is explicit about
not machine-checking. The suite checks the shape of the pool as a whole — that
most negative fixtures carry some shipped tag, so the pool is adjacent cards
rather than vanilla filler — and leaves the per-tag judgement with the author,
recorded in each tag's `limitations`.

### R2 — Retrieval substrate · ~1.5 weeks · no LLM · **GATE G1**

Lexical (SQLite FTS5, replacing the current `LIKE '%q%'` in `research.py`),
structured (pushed down to SQL), dense (`bge-small-en-v1.5`, numpy memmap, no
vector DB), and a local cross-encoder rerank over the fused top ~200. RRF fusion
with weights in config, tuned against the golden set.

The reranker is the single most important substitution of local ML for LLM
capability in this design: it is the reranking the model would otherwise be asked
to do, done better, deterministically, and for free.

**G1:** recall@50 per retriever and fused over `required_oracle_ids`. Target
≥0.90 fused. **Below 0.80: stop and fix the substrate.** No model fixes
retrieval.

### R3 — Query IR and deterministic executor · ~1.5 weeks · no LLM · **GATE G2**

The `ResearchPlan` schema, its validator, and one handler per step kind, each
returning a provenance-bearing envelope.

R3 ships a **reduced step set**: `card_search`, `tag_filter`, `rules_lookup`,
`deck_profile`, and the set ops. `field_stats` moves to R5 because it is shaped by
the **D1** corpus census; `sim_study` moves to R6; `combo_lookup` waits on the
**D4** decision.

**Hand-write a plan for every golden question.** That is the phase's real
deliverable.

**G2:** ≥80% of golden questions answered with full `required_oracle_ids` recall
and zero `forbidden_oracle_ids`, by hand-written plans and **no model**.
**Below 70%: stop — the tool vocabulary is wrong, and a planner would only
obscure that.**

### R4 — UI pilot · ~1.5 weeks · **first LLM · GATES G3 + G4 · FIRST RELEASE**

**Audience, resolved (2026-09-08): the owner plus up to three testers**, behind
`SABER_RESEARCH_ASSISTANT=1`, off by default and independent of
`SABER_DECK_LAB_REDESIGN` — the same env-gated rollout shape the refactor already
uses for `SABER_DECK_LAB_BUILDER` / `_RESEARCH` / `_PLAYMAT`.

Three testers rather than one, because R4 exists to test a **product hypothesis**
and a sample of one cannot falsify it; and not the whole tester list, because the
web tier holds a CPU embedding model and a cross-encoder reranker resident
alongside Flask and SQLite on one `shared-cpu-1x` machine (§7.3). Note that
`SIM_MAX_CONCURRENT=1` does **not** constrain R4 — R4 has no `sim_study` step.
That constraint arrives at R6, and it is a queue problem there rather than a
sizing one.

**Two actions only: Find cards, Explain selection.** The dock — the right-hand
slide-over mirroring `.dl-add-panel`, full-screen at the 767px breakpoint (§6.1)
— the deck binding, the four result-tier renderers, the citation contract, the
job model, progress notes, and thread persistence. No analysis, no studies, no
comparison.

The planner is the plan §4/A5 design at reduced scope — closed tag vocabulary in
a cached system prompt, strict schema, decomposition into narrow calls, plan
memory, self-consistency by union, deterministic validation with cheap repair.

**G3** (bake-off against the R3 hand-written baseline across ≥3 cheap models with
one frontier model as the **control**, so a mediocre score is attributable) and
**G4** (citation coverage 100%, cards-outside-result-set 0, bare rates 0) are both
measured here, on the reduced step set. **Publish the gap whatever it is.**

**This phase is blocked on D0c** — the provider decision is made (HuggingFace
router, `deepseek-ai/DeepSeek-V4-Flash:deepinfra`, `HF_TOKEN`, no code changes),
but the secret is not yet set on `dylnmtthws-decklab` and no live call has been
verified. R0–R3 are unaffected. **The gate is a passing
`scripts/smoke_model_gateway.py`, not the decision** — a decision does not
un-gate a release; a measured call does. This is the only remaining P0.

**Acceptance:** a tester resolves a real card-search question in the builder, adds
a result to a deck, and the thread is still there tomorrow — **on a phone as well
as a laptop**, since §6.1 puts mobile in scope and the dock inherits the
breakpoint for free. Usefulness rig (§10.2) runs its first sessions here.

### R5 — Field intelligence and Analyze deck · ~2 weeks

`deck_tag_profile`, `card_field_stats`, `archetype_cluster`, the `field_stats`
step, and the §6.5 analysis flow with saved findings and the editable plan
assumption.

Every field query returns denominator, window, event-size floor and coverage as
**required fields**. A thin-sample commander returns a stated absence, never a
percentage over n=3. Thresholds ship as **configuration** so the **D1** census can
set them without a code change.

**D6 constraint, which applies regardless of whether D6 lands:** EDHTop16 sends no
card quantities, and the missing cards are deduplicated basics correlated with
colour identity (mono-colour averages 9.1 missing, five-colour 0.1). Mechanic tag
profiles are unaffected because Commander is singleton. **Mana-base comparison is
affected with a direction**, so R5 excludes basics from every field-derived count
and restricts any land comparison to `is_complete = true` with a stated
denominator.

### R6 — Goldfish study pilot, Kinnan-scoped · ~1.5 weeks

The `sim_study` step, the `CapabilityStatement`, the study report view, saved
studies with decisions, and staleness labelling by `deck_sha256`.

Scoped per §5.2 to the one authored pack. Generic execution is not offered.
`games` becomes a study parameter (30,000 within `SIM_MAX_GAMES=60000`), the run
is an async job from the first commit, and the 300 s server-side kill renders as
a failure.

**Acceptance:** a player runs a study, reads the coverage before the number, saves
a decision, edits the deck, and sees the report correctly labelled "applies to an
earlier list."

### R7 — Variant comparison · ~2 weeks

Two player-authored lists, one objective, compatible execution settings, both
executed. Paired interval validated against independently seeded arms **before**
any comparison is shown. Every comparison carries what it cannot evaluate,
beside the finding — the removed-interaction case from the brief is the canonical
example and should be in the fixtures.

Generic execution may be offered here, subject to §5.2's `NotRepresented` rule.

### R8 — Automatic diff proposals · ~2 weeks

Moved from A8. Deterministic candidate generation, deterministic scoring, the
model groups and narrates and may choose among near-ties with a stated reason.
Output is a previewable `deck_documents` changeset with a `mutation_id`, never
auto-applied, every swap carrying its evidence and its originating
`research_turn_id`.

### R9 — Solver decision studies · experimental, unscheduled

`edh_solver` is **not a dependency for R0–R8** and no work order is opened against
it. It is Milestone 0: one synthetic vertical, uncalibrated opponent clocks, and
a node-locked comparison of `attempt_now` vs. `wait_one_turn` under a **declared**
field with an expiry.

Its honest current question is narrow, and the eventual good experience —
*"explore how this decision changes with turn order, available protection, and
different opponent assumptions,"* shown side by side, with the cells where the
preferred action **changes sign** called out — is genuinely instructive. Position
it as preparation and retrospective analysis.

Two limits to carry into any integration: whole-deck goldfish results **cannot**
substitute for held-hand or midgame evaluation (the simulator refuses held hands,
midgame states and seat overrides over HTTP; `--hands` and `--grid` are CLI-only),
and no layer claims a GTO certificate for a four-player pod — that is a
definitional limit, not a compute limit.

---

## 10. Evaluation: two rigs, deliberately separate

### 10.1 Correctness rig — machine, every phase

Two modes, and the distinction is the point: **substrate mode** (hand-written
plans, no model) and **end-to-end mode** (model-planned). The difference between
the two scores is the model's contribution, isolated. It is the only way to answer
"would a better model help?" without guessing.

| Metric | What it catches | Kind |
|---|---|---|
| recall@k on `required_oracle_ids` | substrate failing to find the answer | quality |
| `forbidden_oracle_ids` hit rate | substrate returning wrong things confidently | quality |
| citation **coverage** | unsourced prose | **invariant — must be 100%** |
| cards-named-outside-result-set | hallucination | **invariant — must be 0** |
| bare-rate count | denominators dropped | **invariant — must be 0** |
| absence-stated rate on out-of-scope | improvisation instead of honesty | quality |
| manufactured-finding rate on healthy decks | analysis treated as an obligation | quality |
| clarification appropriateness | asking when it should answer, and vice versa | quality |
| cost per question, p50/p95 | the economics of the bet | operational |
| latency p50/p95 | whether it is usable | operational |

The three invariants are **structural**. A non-zero result is a bug, not a tuning
target.

### 10.2 Usefulness rig — human, from R4 onward

The plan had no equivalent, and this is the second reversal in §2.

| Measured | How |
|---|---|
| **Citation support**, not presence | Sample N assertions per release; adjudicate whether the cited source actually supports the claim. Frontier-model judge, with a human-audited subsample, because an LLM judge grading its own family is a known failure mode. Report support rate separately from coverage. |
| **Rules accuracy** | Golden rules questions adjudicated against the CR text by a person, not by string overlap. |
| **Retrieval relevance** | Beyond recall on required ids: are the *other* returned cards defensible? Graded on a sample. |
| **Comprehension** | After a study, ask the tester to state in their own words what the result establishes and what it does not. This is the metric that catches a beautiful, misleading report — and no automated check can. |
| **Task time and return rate** | Time to resolve a stated deck question, vs. their own baseline. Whether they come back with a second question. |

The rigs report separately and neither substitutes for the other. A release can
have a perfect correctness scorecard and fail the usefulness rig; that outcome is
informative and is the reason for the split.

### 10.3 The golden set is a judgment, not a fact

`required_oracle_ids` are hand-verified by the owner. The set records **who
labelled each question and when**, and questions whose labels are contested are
**marked rather than silently resolved**. An eval set that quietly encodes one
person's opinion as ground truth produces a system that is confidently wrong in
exactly that person's blind spots.

---

## 11. Cost

Per research question, with the R4 techniques applied:

| Component | Calls | Rough cost, cheap model |
|---|---|---|
| Clarification (CLASSIFY, tiny schema) | 0–1 | ~$0.001 |
| Plan (3× self-consistency, cached system prompt) | 3 | ~$0.004 |
| Tool execution | 3–8 | $0 — server-side |
| Narration over ~10–15k tokens of results | 1 | ~$0.01–0.02 |
| **Total** | | **~$0.02–0.03** |
| Cache hit on a repeated question | | **$0** |

Against `annual_cost_ceiling_usd: 100`, that is roughly 3,000–5,000 questions per
year before caching, and deck-building questions cluster hard around popular
commanders, so caching should move that materially.

Two cautions. The pricing in `config/cedh.yaml` carries its own warning — those
are recorded DeepInfra list prices, and the router bills what the provider charges
on the day. And the G3 frontier control costs perhaps 10× per question over ~150
questions a handful of times: a few dollars total, and the cheapest information in
this document.

Simulator compute is a separate line item on a separate machine
(`docs/hosting-cost-model.md`), budgeted outside the LLM-only annual target.

---

## 12. Dependencies

Full work orders are in [`docs/upstream-dependencies.md`](docs/upstream-dependencies.md).
Re-prioritized against this spec's ordering:

| # | Repo | Item | Now gates | Priority |
|---|---|---|---|---|
| **D0c** | deployment | **Decision made** (HF router, `HF_TOKEN`). Remaining: set the Fly secret and pass `scripts/smoke_model_gateway.py` | **R4 — the first release** | **P0** |
| ~~D0b~~ | deployment | Provider and model choice — **resolved 2026-09-08**, no code changes (§14.1) | nothing | — |
| ~~D0a~~ | this repo | Silent model absence on the candidate page — **not a dependency; scheduled into R0** (§14.1) | nothing | — |
| **D3** | `commander_simulator` | Per-card inert/unauthored list (`result.v4`) | R6's card-level capability statement (§5.1) | **P1** |
| **D1** | `ingestion_pipeline_mtg` | Corpus depth census + recurring nightly | R5 thresholds | P1 |
| **D2** | this repo | Resolve the two-corpus fork | R2, R5 integration | P1 |
| **D4** | `ingestion_pipeline_mtg` | Combo corpus (Commander Spellbook) | `combo_lookup`; R8 candidates | P1 |
| **D5** | `ingestion_pipeline_mtg` | Card rulings in `mtg_v1` | `rules_lookup` completeness | P2 |
| **D6** | `ingestion_pipeline_mtg` | Moxfield adapter — real quantities | R5/R8 mana-base analysis | P2 |
| **D7** | `commander_simulator` | Pack discovery via `/healthz` at scale | R6/R7 `sim_study` | P2 |
| **D8** | `ingestion_pipeline_mtg` | `card_name_index` as a view | minor dedup | P3 |

Changes from the previous ordering: **D0 is now the gate on the first release
rather than on a late phase**, which is the direct cost of moving the UI earlier
and is worth paying. **D3 rises to P1** because §5.1's capability statement is a
headline feature of R6 rather than a narration detail. Nothing blocks R0–R3.

### 12.1 The two carried-over decisions, now resolved

Both were left open by the plan. Both are closed here (2026-09-08) on the
recommendation the plan itself made; neither is decided by the refactor, so both
are flagged as reversible if the owner disagrees.

**Combo corpus (D4) — defer out of the first release; ingest into `mtg_v1` when
it lands. Never local.**

`combo_lookup` is not in R3's reduced step set and not in R4's two actions, so
D4 gates nothing before R5. Deferring it costs the first release nothing.
Building it *locally* to move faster is the tempting wrong answer: it re-creates
the exact duplicate-ingestion problem **D2** exists to close, and it would put a
second card corpus behind the assistant while D2 is trying to remove one.
Decision point: **before R5 closes**, since combo completions are one of the
stronger sources of R5 findings and the whole of R8's candidate generation.

**Embedding model swap — one model, `bge-small-en-v1.5`, re-index both indexes,
at R2.**

MiniLM-L6 → bge-small changes the `reference_layer` index as well as the new card
index. Running two encoders would mean two notions of similarity inside one
product, which surfaces as a rules answer and a card result disagreeing about
what "similar" means, with no way for a player to tell why. The cost of doing
both at once is one re-index; the cost of doing them separately is a class of bug
that is very hard to attribute. R2 already rebuilds the card index, so it is the
cheapest moment.

Sequencing note, **and the assumption this decision rests on**:
`reference_layer` appears to be **idle**, so re-indexing it should have no live
consumer to break. That is only true until `rules_lookup` is wired at R3, which
is why R2 is the cheap moment rather than "later."

**Verify before re-indexing — do not assume it.** The claim is that nothing
currently reads the `reference_layer` index. Establish it, at R2, before the
rebuild:

1. Grep the import graph for consumers of `reference_layer.retriever` /
   `.evidence`, including the legacy `reasoning/` path — `profile_synthesis.txt`
   takes a `{reference_chunks}` slot and `card_fit.txt` takes
   `{relevant_rule_excerpts}`, so the **legacy casual generator is a plausible
   live consumer** and is the specific thing to rule in or out.
2. Check `scripts/index_references.py` and `index_set_mechanics.py` for what they
   populate and whether anything in a scheduled job reads it.
3. Confirm the stored vectors' dimensionality is recorded alongside them, so a
   mixed-dimension index fails loudly rather than returning silently wrong
   neighbours. MiniLM-L6 and bge-small are both 384-dim, which means **a stale
   row and a fresh row are the same shape and will not error** — that is the
   failure this check exists to prevent, and it is the reason to verify rather
   than assume.

If a live consumer is found, the decision does not flip; the swap simply
re-indexes that consumer too, in the same change, per "one model, two indexes."
If the swap must be split across phases for any reason, that is the new evidence
that reopens 4b.

---

## 13. Prior art, and where the opportunity actually is

Card search, combo discovery and playtesting are **established expectations**, not
differentiators. Archidekt ships an integrated playtester
([FAQ](https://archidekt.com/faq)); Commander Spellbook finds existing and
potential combos from a list
([find-my-combos](https://commanderspellbook.com/find-my-combos/)); DeckFlow
documents AI-assisted analysis workflows
([deck analysis](https://www.deckflow.gg/help/deck-analysis)).

Deck Lab's opportunity is not any one of those capabilities. It is
**connecting research to reproducible experiments and remembered decisions** —
the loop in §1, with §5.6's saved studies and §5.1's honest capability statements
as the parts nobody else ships. That is also the part that is hardest to copy,
because it requires the simulator's honesty fields and the deck document's
command log to already exist. They do.

---

## 14. Decisions taken

All four questions this spec opened were closed by the owner on **2026-09-08**
and **approved as recorded**. Recorded here with the basis for each, because two
were settled by the refactor's shipped code and two were not — and a reader six
weeks from now should be able to tell which is which before relying on them.

**Do not reopen these without new evidence that contradicts their stated
assumptions.** Where an assumption is named below as *to verify*, verifying it is
part of the phase that depends on it; a failed verification is exactly the new
evidence that would justify reopening.

| # | Decision | Basis | Where |
|---|---|---|---|
| 1 | **Nav stays Research / Build, unchanged. The assistant is "Ask".** Not a nav item; a context-bound dock with no page of its own | **Refactor.** `deck_lab/base.html` already ships that taxonomy in the header, drawer and account popover | §1.1 |
| 2 | **Mobile is in scope.** Dock mirrors `.dl-add-panel` on the right; full-screen at the existing 767px breakpoint. `CLAUDE.md`'s `ui_scope` is stale and R0 corrects it | **Refactor.** `deck-lab.css` ships `@media (max-width: 767px)` with substantial rules, a drawer, `dl-bottom-action`, `desktop-only` opt-outs and safe-area insets | §6.1, §7.5 |
| 3 | **R4 pilot: owner plus up to three testers**, behind `SABER_RESEARCH_ASSISTANT=1`. **Explicitly reversible** | **Recommendation.** The refactor supplies the env-gated rollout shape but not the number. Three because one cannot falsify a product hypothesis; not more because of web-tier residency | §9 R4 |
| 4a | **D4 combo corpus: defer past the first release; ingest into `mtg_v1` when it lands, never local.** Decide before R5 closes. **Explicitly reversible** | **Recommendation**, matching the plan's own | §12.1 |
| 4b | **Embedding swap: one model (`bge-small-en-v1.5`), re-index both indexes, at R2. Explicitly reversible**, and conditional on verifying `reference_layer` has no live consumer first | **Recommendation**, matching the plan's own | §12.1 |

**Reversibility is a property of the mechanism, not a promise.** Decisions 3, 4a
and 4b are reversible because each is a configuration or a rebuild rather than a
migration:

- **3** is one env var. Widening the pilot is `SABER_RESEARCH_ASSISTANT=1` on more
  accounts; narrowing it is removing it. No data changes either way.
- **4a** is reversible *because* it is a deferral. The irreversible move is the
  one it rules out — ingesting the combo corpus locally, which would create a
  second card corpus behind the assistant and is why "never local" is stated as a
  constraint rather than a preference.
- **4b** is reversible by re-indexing back, at the cost of one rebuild. What makes
  it cheap is the timing, not the model choice — see the verification below.

Two of these corrected something the first draft of this spec had wrong, and both
corrections came from reading the refactor's CSS rather than from the decision
itself:

- The dock cannot sit **beside** `.dl-stats-rail`. The builder is a fixed
  two-column grid with no third column, and the rail is `display:none` at the
  mobile breakpoint — so a dock parented to it would have vanished exactly where
  the brief wants a full-screen view. It is a right-hand overlay instead (§6.1).
- `SIM_MAX_CONCURRENT=1` does **not** constrain the R4 pilot, because R4 has no
  `sim_study` step. That constraint arrives at R6. The two were conflated; §7.3
  now separates them, since they have different fixes — a queue at R6, and
  process residency at R4.

### 14.1 D0 — the one real blocker

**D0 splits into two things that were filed as one, and only the second is a
decision.**

#### D0a — the missing-absence bug. Not a decision; fix it now, independent of D0

The work order asked whether the model outage on the 2026-09-05 acceptance run
degraded *visibly* or *silently*. **Answered from the code: partly silently, and
production is in the silent case.**

| Path | `gateway is None` | Warning appended? |
|---|---|---|
| `lab.py:_intent` | line 267 | **Yes** — but only when `raw_intent` is set. An explicit `pack_id` returns at line 256 before the gateway check, and with one supported pack that is the UI's normal path |
| `lab.py:_summarise_evidence` | line 314 | **No** — silent `return None` |
| `lab.py:_explain` | line 342 | **No** — silent `return None` |

And `templates/cedh/candidate.html:164` is `{% if explanation %}` **with no
`{% else %}`**, while `cedh_routes.py:candidate` does not pass `modes` to the
template at all. So in production today — `HF_TOKEN` unset, Kinnan pack chosen
explicitly — the candidate page renders **no explanation, no evidence summary and
no warning**. The only signal is `LabModes.notices` on `lab.html`, which is a
different page, seen before the build, and not carried onto the result.

That violates `absence_is_visible`. Note the contrast that makes it a bug rather
than a design: when a credential *is* present and the provider *fails*,
`lab.py:_call` appends a warning and `candidate.html:19` renders it. The
degradation path is correct; the never-configured path is the one that is silent.

**Fix, in this repo, with no dependency on D0:** append a warning on the
`gateway is None` early return in `_summarise_evidence` and `_explain`; persist
that warning with the build and pass it to `candidate.html`; give the explanation
block an `{% else %}` that states the absence. Do not infer a historical build's
mode from the current environment: provisioning a credential later must not
retroactively claim the old build used it. Scheduled into **R0**, because the
Research Assistant inherits this exact pattern — an assistant whose model is
unavailable must say so, and shipping R4 on top of a silent-absence precedent
would propagate it.

#### D0b — the provider decision. Yours, and it gates the first release

**The exact unresolved decision:** production has no model credential, and the
incumbent configuration routes through an intermediary whose token name is now
misleading. `config/cedh.yaml` sets `provider: deepinfra`, `base_url:
https://router.huggingface.co/v1`, `model_id:
deepseek-ai/DeepSeek-V4-Flash:deepinfra`, `credential_env: HF_TOKEN`. So the
model is served *by* DeepInfra but reached *through* the HuggingFace router, and
the credential is an HF token. Nothing is set on `dylnmtthws-decklab`.

**Recommendation: keep the HuggingFace router and provision `HF_TOKEN`.** Do not
switch to a direct DeepInfra account for R4.

Evidence:

- **The `:deepinfra` suffix is router syntax.** `provider_deepseek.py:115`
  `_assert_pinned` requires a provider suffix on the model id and refuses to
  start without one unless `require_pinned_provider: false`. Going direct makes
  the suffix meaningless and forces either a config override that weakens the pin
  or a rework of the check. That is real work bought for no R4 benefit.
- **G3 needs several models under one credential.** The R4 gate is a bake-off
  across ≥3 cheap models plus one frontier control (§9, R4). A router gives that
  for one credential and one adapter; direct accounts mean three signups before
  the gate can run.
- **The ceiling is already real.** `config/settings.yaml` sets
  `monthly_cost_ceiling_usd: 15.0`, and `lab.py:_call` catches
  `LLMCostCeilingExceeded` and surfaces it as a warning rather than swallowing
  it. Requirement 5 of the work order is substantially satisfied already.
- **Switching later is an adapter change, not a rewrite** — that is what ADR-021's
  provider-neutral gateway bought. The cost of being wrong here is low, which is
  itself an argument for the cheaper path now.

Caveat to carry: the pricing in `config/cedh.yaml` is recorded DeepInfra list
pricing and the file says so — the router bills what the provider charges on the
day. Until one real call lands a `cost_log` row, §11's cost model is unvalidated
arithmetic.

> **DECIDED 2026-09-08.** The owner has a HuggingFace account with billing
> enabled. **D0b is resolved as recommended: keep the HuggingFace router, keep
> `deepseek-ai/DeepSeek-V4-Flash:deepinfra`, keep `credential_env: HF_TOKEN`.
> No code changes.**
>
> What remains is provisioning and verification, not a decision — see §14.2.

#### D0c — provisioning and verification. Not a decision; an owner action

Three steps, and the second is one command:

1. **Set the secret.** `fly secrets set HF_TOKEN=… --app dylnmtthws-decklab`.
   The recorded production secrets are `CEDH_SIMULATOR_URL`, `MTG_V1_DSN` and
   `SABER_SECRET_KEY`; this makes four. Setting a secret restarts the app.
2. **Run `scripts/smoke_model_gateway.py`** (§14.2). It walks D0's five
   acceptance criteria in order and prints the measured tokens, cost and latency.
3. **Record the measured cost** against §11's estimate. Until one real call lands
   a `cost_log` row, the whole cost model is unvalidated arithmetic — that is
   stated in §11 and this is what removes the caveat.

**R4 stays gated until step 2 passes.** The decision being made does not
un-gate the release; a verified live call does.

### 14.2 D0 verification tooling — built and offline-verified

`scripts/smoke_model_gateway.py` closes D0's acceptance in one command. It walks
the five criteria in the order the work order states them:

| Step | Proves | Verified offline |
|---|---|---|
| 1 | The credential named by `model.credential_env` is present | Yes — refuses with the config path when unset |
| 2 | The gateway constructs, running `_assert_pinned` | Yes — `:cheapest` and a bare id both raise `ModelConfigurationError`; the configured id is accepted |
| 3 | One live call returns a **validated Pydantic instance** | **Needs the real token.** The gateway raises rather than partially accepting, so reaching the assertion *is* the proof |
| 4 | A `cost_log` row lands with model, call type and non-zero tokens | Yes — row insert, count delta and `_latest_row` exercised against a scratch DB |
| 5 | The ceiling **raises** rather than being logged and ignored | Yes — `check_ceiling(15.0)` passes, `check_ceiling(0.0)` raises `LLMCostCeilingExceeded` |

Three design choices worth stating, since each is a way the check could have been
weaker than it looks:

- **The schema is two fields, one of them a bounded int.** A single free-text
  field would validate even if the provider ignored the schema entirely, so it
  would prove nothing about structured output.
- **Zero tokens is an explicit failure.** A call can succeed while usage
  accounting silently returns zero, and every cost figure downstream would then
  read zero. The script fails rather than reporting a free call.
- **A missing ledger file is an explicit failure.** `CostLedger.record` swallows
  write failures by design — accounting must not fail a build — which means a
  missing row is silent in production. The script checks the file exists *before*
  spending money, and asserts the count delta afterwards.

It writes one row to the ledger, so `--db` defaults to `data/sabermetrics.db` and
should be pointed at a scratch database unless the production ledger should
record the check.

### 14.3 D3 — resolved from the code; a default is set either way

**Resolved: R6 ships without D3, and D3 remains worth doing.** Not a blocker.

From the code, the split is exact. `cedh-simulation-result.v3` gives
`coverage.modeled_cards`, `inert_cards`, `unauthored_cards` as **integers**, plus
`coverage.inert_by_reason` across a closed set of five categories — `interaction`,
`opponent_trigger`, `opponent_permanent`, `timing_only`, `no_object_in_model` —
and `strategy_pack.known_blind_spots` as prose. Deck Lab already validates v3 and
`simulator.py` already reads all of it.

So R6's `CapabilityStatement` (§5.1) can say, today and with no upstream work:

> *"31 of 99 cards were invisible to this model: 12 because the model has no
> opponents, 9 because their effect has no object in the model, 10 unauthored.
> The per-card list is not available from this simulator version."*

What it cannot say is **which** card. That is the actionable half, and it is
`result.v4`.

**The remaining choice is upstream's, not this spec's:** whether
`commander_simulator` publishes v4 with `coverage.inert_cards[]`,
`unauthored_cards[]` and `misclassified_cards[]`. Two things make the case
stronger than "nice to have":

- The data demonstrably exists inside the simulator — it prints an inert table in
  the human run report, which is how `Hullbreaker Horror`'s known misclassification
  is documented at all. v4 is a serialization change, not new analysis.
- A known misclassification that appears in a human report and not in the machine
  contract is **visible to a person and invisible to a consumer**. That asymmetry
  is the argument.

**Default if D3 never lands:** R6 renders the counts and the by-reason breakdown,
states that the per-card list is unavailable, and **does not parse warning
strings** to reconstruct it. The handoff rules that out explicitly and it is
right to: a display that breaks when a warning is reworded is a contract nobody
agreed to.

### 14.4 D1 — census specified and executable; numbers need the live database

**Done as far as the repository allows.** Two artifacts:

- **[`docs/d1-corpus-census.md`](docs/d1-corpus-census.md)** — scope (six
  measures, and what is deliberately excluded), counting method with the reason
  each choice changes the answer, the figures already on record, and seven
  numbered uncertainties.
- **`scripts/d1_census.py`** — eight read-only queries in one
  `REPEATABLE READ READ ONLY` snapshot. Every query passes `assert_v1_only`
  (verified, including a negative control on `mtg_internal`). It warns if the
  connected role is not `mtg_consumer`, and **refuses to run without a DSN**
  rather than falling back to fixtures, because a census of a fixture measures
  the fixture.

**The blocker is access, and it is the correct blocker.** This worktree has no
`MTG_V1_DSN`, no `psql` and no `flyctl` — a feature branch should not hold
production database credentials. Running the script is a task for whoever does.

Three method choices worth surfacing, because each changes the number:

- **Group by `deck.commander_identity`, never by name.** It is the sorted-oracle_id
  key, so a partner pair is one identity. Grouping by name splits
  Thrasios/Tymna from Tymna/Thrasios into two populations that each look half as
  popular as the real one.
- **`unresolved:` identities are counted separately, never merged.** Folding them
  in inflates the identity count with names rather than commanders; dropping them
  silently hides a resolution problem worth seeing.
- **Two floors are measured, not one:** ≥30 decks, and ≥30 decks *across ≥5
  events*. Thirty decks from a single tournament is one metagame snapshot, not a
  trend, and only the second floor can tell them apart.

What is already known without running it: `card_any_medium` at 34,570 rows,
**200 tournaments from a single source in one manual import**, and Kinnan at
n=635 with 42 incomplete. The single source is the finding most likely to matter
— with one `source`, archetype clustering inherits EDHTop16's coverage biases
entirely, and the census will show that rather than fix it. And the corpus does
not currently refresh: `NIGHTLY_ENABLED` is unset, so these numbers will age
silently. Enabling the nightly needs explicit owner approval and is deliberately
not bundled into running a census.
