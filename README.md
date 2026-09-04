# commander-deck-engine

**Sabermetrics for Magic**

[![CI](https://github.com/DylnMtthws/commander-deck-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/DylnMtthws/commander-deck-engine/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Generates Commander/EDH decklists by reasoning about *why* a commander wants a
card, grounding that reasoning in card text, aggregated decklists, community
discussion and the official rules.

> **Scope.** A multi-user web app, self-hosted on one machine, Commander format
> only. Accounts are provisioned by the owner — there is no self-registration.
> The app binds to localhost and is published to a private Tailscale tailnet;
> it has no public surface at all. See [Deployment](docs/deployment.md) and
> [Scope & non-goals](#scope--non-goals).

> **Two paths, one app.** The original generator above builds casual Commander
> decks and runs on a local SQLite corpus. The **[cEDH Deck Lab](#the-cedh-deck-lab)**
> is a separate, competitive-only path that reads card and tournament facts from
> the `mtg_v1` contract published by a sibling ingestion repository, builds from
> curated strategy packs deterministically, and hands its result to a goldfishing
> simulator. It shares this app's accounts, quota and cost ceiling and nothing
> else. New work goes there.

## Architecture in one paragraph

A build runs eight stages. Hard filters and role tagging reduce the legal card
pool; a Pareto filter drops cards dominated within their role; a deck template
is derived from the commander's profile; four deterministic generators fill
infrastructure (ramp, draw, removal, protection, lands); empirical staples the
role scorers reject are reserved from a decklist corpus; a synergy optimizer
computes role targets and a synergy matrix, greedily fills every remaining slot,
refines by swap, rebalances against budget, and repairs engine-type floors;
Commander legality is then enforced as a hard invariant — exactly 99 cards,
singleton, within colour identity; finally the deck is synthesised, classified
and persisted. The governing principle is that cheap deterministic work comes
first and model calls are spent only where scoring alone cannot decide: the
optimizer remains the selector, and the single batched LLM call is a safety vet
that audits the assembled deck last, so nothing bypasses it. Every model call
goes through one wrapper that pins the permitted model IDs, retries transient
failures with exponential backoff, records input, output and cached-read tokens
per call to a `cost_log` table, and refuses to make the call at all once the
configured monthly spend ceiling is reached.
<!-- TODO: state measured cost per build once cost_log has rows to aggregate.
     No figure is quoted here on purpose: every cost number in the repo is a
     target or an estimate, and OPTION_A_DOD.md records that the pricing
     constants were once wrong by 20-25%. -->

## At a glance

| | |
|---|---|
| **Language** | Python ≥ 3.11 |
| **Typing** | Type hints throughout; mypy configured, 42 errors outstanding (reported, not gated); the `cedh/` package is mypy-clean |
| **Tests** | 976 collected across 75 files — 948 pass, 28 skip (no local database, and the three opt-in live integrations) |
| **CI** | GitHub Actions; pytest gates the build, ruff (345 findings) and mypy report only, black not run |
| **LLM** | Two paths. Casual: Anthropic `claude-sonnet-4-6` (profile, fit, synthesis) and `claude-haiku-4-5` (refresh), prompt caching on. cEDH: a provider-neutral gateway, DeepSeek-V4-Flash on DeepInfra by default, structured output required and Pydantic-validated |
| **Data sources** | Casual: 11 ingestion modules — Scryfall, EDHREC, Moxfield, Archidekt, deckstats, TopDeck.gg, Commander Spellbook, magicthegathering.io, Reddit, Game Knights, WotC rules. cEDH: the `mtg_v1` Postgres contract only, plus curated strategy material |
| **Retrieval** | `all-MiniLM-L6-v2` on CPU; cosine similarity in numpy over embeddings stored as SQLite blobs |
| **Interfaces** | Flask web app (4 blueprints: portal, admin, auth, cEDH lab) and a 19-command Click CLI |
| **Auth** | Three modes: tailnet identity from `tailscale serve` (no passwords), `hybrid` for public deployments, or email + argon2id. CSRF on POST, per-account lockout, admin-provisioned in every mode, no self-registration |
| **Hosting** | `tailscale serve` on a private tailnet, or `tailscale funnel` for a public URL; app bound to 127.0.0.1, no port forward either way |
| **Scheduling** | 4 macOS launchd jobs — nightly, weekly, monthly, quarterly |
| **Commits** | 157 on `main`, 2026-05-06 to 2026-08-26 |

### Agent and tool integration

There is none, and that is a design choice rather than a gap. The codebase
contains no tool-calling, no function definitions passed to the model, and no
agent loop — a grep for `tools=`, `tool_use`, `tool_choice` and `function_call`
returns nothing. Orchestration is ordinary Python: the pipeline decides what to
call and when, and each model call is a single request returning structured JSON
that is parsed and validated by the caller. This is the thinnest of the four
areas below and is described here for completeness.

### Context engineering

Prompt caching is enabled on every call, with `cache_control: ephemeral` markers
placed so that the reusable prefix — profile, reference chunks, corpus evidence —
is cached across the calls that share it. Commander profiles are cached in SQLite
under a key of commander ID, user-intent hash and set version, so a profile is
regenerated only when one of those actually changes rather than on every build.
The reference layer retrieves rules and strategy chunks by cosine similarity and
injects only the top matches, and the safety vet is one batched call over the
assembled deck rather than a per-card call in the selection loop.

### Evaluation and guardrails

Correctness is pinned by 774 tests and by invariants the pipeline enforces rather
than hopes for: legality is re-checked after optimization and repaired to exactly
99 singleton cards in colour identity, and a separate assertion prevents the
commander appearing in its own 99. The model wrapper validates model IDs against
an allowlist and fails fatally on retired IDs instead of 404ing at runtime,
retries transient failures three times with exponential backoff, and raises
before spending once the monthly ceiling is hit. When the LLM safety vet throws,
the failure is logged with a traceback and surfaced in pipeline metrics as a
negative cost rather than silently skipped — a silently-skipped vet is recorded
in the project's debugging notes as having produced its worst deck.

### State management

All state lives in one SQLite database of 27 tables — cards, prices, decklists,
derived metrics, profiles, users, decks, feedback and cost. There is no external
store and no cache server. Cost attribution uses a `ContextVar` so that rows
written to `cost_log` by the model wrapper carry the user and deck they belong
to, which works correctly for web builds without threading identifiers through
every call signature and degrades cleanly to unattributed for CLI and scheduled
runs. Per-user monthly deck quotas and a global spend ceiling are both evaluated
against this same database, and generation traces are persisted for later
inspection.

---

## Table of contents

- [What a build produces](#what-a-build-produces)
- [How it works](#how-it-works)
- [The cEDH Deck Lab](#the-cedh-deck-lab)
- [Requirements](#requirements)
- [Setup](#setup)
- [Usage](#usage)
- [Grounding a commander in real decklists](#grounding-a-commander-in-real-decklists)
- [Configuration](#configuration)
- [Project layout](#project-layout)
- [Development](#development)
- [Scope & non-goals](#scope--non-goals)

---

## What a build produces

Ask for a deck by commander name and budget:

```bash
sabermetrics build "Eriette of the Charmed Apple" --budget 200 --output-format text
```

You get a legal 99-card deck (plus the commander), each slot chosen and, where it matters, vetted against the deck's strategy:

```
[Enchantment] (36)
  All That Glitters ($0.41)
  Gift of Immortality ($1.20)
  Kaya's Ghostform ($2.06)
  ...
[Land] (35)
  Command Tower ($0.28)
  Caves of Koilos ($0.51)
  ...

--- Generation Info ---
Total price: $175.67 / $200.00
LLM Cost:    $0.14
```

A typical build costs **$0.10–0.15 in API spend** and stays under the budget you set. Output formats: `text`, `json`, `moxfield`, `archidekt`.

Prefer a browser? `sabermetrics serve` launches a local web UI at `http://127.0.0.1:5000`.

---

## How it works

Card selection is a pipeline of cheap deterministic stages that narrow ~25,000 cards down to a candidate pool, followed by a single, focused LLM pass that audits the finished deck. Reasoning is layered over reasoning: expensive judgment is spent only where the numbers can't decide.

1. **Hard filters** — color identity, legality, singleton, and a per-card price ceiling.
2. **Structural scoring** — each card gets a composite value score (synergy with the commander, mana efficiency, replacement value). Price is a *constraint*, never a quality signal.
3. **Pareto filter** — drop cards strictly dominated within their role, protecting cards the corpus says are real staples.
4. **Template derivation** — target counts for lands, ramp, draw, removal, and engine pieces, grounded in what real decks of the archetype actually run.
5. **Infrastructure generators** — role packages (ramp, draw, removal, protection, mana base) built from pre-scored candidate tables.
6. **Empirical reservation** — reserve slots for consensus staples the role scorers would otherwise miss.
7. **Synergy optimizer** — an N×N pairwise synergy matrix drives a greedy fill and swap-refinement that fills every remaining slot to reach exactly 99.
8. **Budget rebalancing** — spend remaining budget on upgrades that measurably improve the deck.
9. **LLM safety vet** — one batched call reviews the riskiest picks against the deck's game plan and rules the numeric layer can't read (e.g. "this board wipe destroys your own engine"), with a re-vet pass so no replacement enters unreviewed.
10. **Legality repair** — a hard invariant guaranteeing exactly 99 legal, singleton, in-identity cards.

The differentiator is **strategic comprehension, not a better popularity heuristic** — made affordable by aggressive prompt caching and the filter-before-reason design.

---

## The cEDH Deck Lab

Competitive Commander only. Not a power-level slider on the casual generator —
a different product with a different definition of a good answer.

### What is different

| | Casual generator | cEDH Deck Lab |
|---|---|---|
| **Card facts** | local SQLite, filled by `ingestion/` | `mtg_v1.card_any_medium` (Postgres, sibling repo) |
| **Card selection** | scoring pipeline over the legal pool | deterministic fill from a curated strategy pack |
| **Model role** | scores fit, vets the assembled deck | classifies intent, summarises evidence, explains. Never selects a card |
| **Model provider** | Anthropic | provider-neutral gateway; DeepSeek on DeepInfra by default |
| **Budget** | the objective | **does not exist.** cEDH is proxy-normal, so the engine never sees a price |
| **Evidence** | EDHREC inclusion, Reddit, decklists | tournament results, with event counts and sample sizes |
| **Validation** | none | goldfishing simulator, or a visible "not simulated" |

### The rules it holds itself to

The three that shaped the design, each enforced by a test rather than a
convention:

- **The model never picks a card.** Selection is deterministic and finished
  before the first model call. If the provider is down, over budget, or returns
  something that will not validate, the deck is unchanged and the narrative is
  missing — with a note saying so.
- **Absence is shown as absence.** No tournament data is "no tournament data",
  never an empty result that reads like "no decks ran this". No simulator is
  "not simulated", never a neutral score — a neutral number is indistinguishable
  from a measured one once it is in a table.
- **Popularity is not quality.** An inclusion rate is displayed with its
  denominator, its window and its event-size floor, or it is not displayed.
- **Price is not a performance signal.** cEDH is proxy-normal: the expensive
  cards get proxied, so what a card costs says nothing about whether it wins a
  round. There is no budget setting, `CardFacts` has no price field, and the
  live card query does not select one — absence enforced by tests, because a
  price field that exists eventually becomes a tie-break.
- **The engine does not know who is asking.** No collection, no owned-cards
  preference. A new deck means acquiring or proxying cards, so preferring
  what's already in a binder is a price constraint wearing a different hat.
  Selection sees the pack and the role and nothing else, which is also what
  makes a build reproducible: the same pack yields the same 99 for everyone,
  so the candidate hash identifies a list rather than a list-plus-requester.

### Try it

```bash
sabermetrics cedh packs        # which commanders are supported, and in which mode
sabermetrics cedh build --pack kinnan_basalt --out candidate.json
```

Or use the **cEDH Lab** tab in the web UI. Both work with no database, no model
credential and no simulator binary — the first vertical slice (Kinnan, Bonder
Prodigy) runs against checked-in fixtures, and every page states which of its
inputs are fixtures rather than live.

### What is not built

One commander pack, because the simulator models one deck. The interfaces take
more; nothing claims more exists. Tournament evidence is derived from the live
atomic `mtg_v1` views. The simulator is a separate versioned JSON boundary; an
unavailable result remains visible in the product rather than worked around.
[`docs/integration-handoff.md`](docs/integration-handoff.md) states exactly what
is needed from each sibling repository.

## Requirements

- **Python 3.11+**
- An **Anthropic API key** (`ANTHROPIC_API_KEY`) — profile synthesis and the deck vet call Claude
- ~1 GB disk for the card database and reference embeddings
- Internet access for the initial data pull (Scryfall bulk data, decklists)

All data is acquired automatically through public APIs and structured pulls — there is no manual card entry.

## Setup

```bash
# 1. Environment
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# 3. Initialize the database
python scripts/setup_db.py

# 4. Pull card data + prices from Scryfall (this is the slow step)
python scripts/initial_ingestion.py
```

`initial_ingestion.py` accepts `--scryfall-only` (cards and prices only, skip decklist sources) and `--skip-prices` for a faster first run.

Verify everything is wired up:

```bash
sabermetrics health     # status of every data source
```

## Usage

The `sabermetrics` command is installed by `pip install -e .`. Core commands:

```bash
# Generate a deck
sabermetrics build "Korvold, Fae-Cursed King" --budget 150 --output-format text

# Inspect the strategic profile the builder reasons from
sabermetrics profile "Korvold, Fae-Cursed King"

# Launch the local web UI (localhost only)
sabermetrics serve --port 5000

# Query the rules/reference knowledge base
sabermetrics search-rules "how does deathtouch interact with trample"
```

`build` options: `--budget <usd>`, `--power <1-5>`, `--strategy <hint>`, `--user-intent "<free-text direction>"`, `--output-format <text|json|moxfield|archidekt>`, `--deck-name <label for the UI>`.

## Grounding a commander in real decklists

Deck quality improves sharply when the builder can compare against real decks of the commander's archetype. This corpus pipeline pulls verified decklists, clusters them into archetypes, and derives per-variant inclusion rates that feed selection:

```bash
sabermetrics pull-decks "Sauron, the Dark Lord"        # verified Archidekt decks
sabermetrics cluster-decks "Sauron, the Dark Lord"     # k-means archetype clusters
sabermetrics value-cards "Sauron, the Dark Lord"       # per-variant inclusion rates
sabermetrics characterize-variants "Sauron, the Dark Lord"
sabermetrics validate-clusters "Sauron, the Dark Lord"
```

Builds work without this — the empirical layer degrades cleanly to community-wide signals — but running it first is what lets the builder land an archetype's real staples.

## Configuration

- **Tunable behavior** lives in `config/settings.yaml` (scoring weights, budget policy, reservation caps, model choices). No magic constants in code.
- **cEDH settings** live in `config/cedh.yaml` — model provider, base URL, pricing, reasoning modes, evidence bounds, simulator mode. Model **pricing is configuration**, so a provider price change is a YAML edit rather than a code change.
- **Strategy packs** live in `config/cedh_packs/*.yaml`, authored by card name and resolved to `oracle_id`s at load. **Curated strategy material** lives in `config/cedh_evidence/*.yaml` — reviewed and versioned, never scraped at generation time.
- **Reference data and synergy rules** live under `config/` (`synergy_rules.yaml`, `game_changers.yaml`, `auto_include_cards.yaml`, …).

Secrets stay in the environment, never in `config/`:

| Variable | Needed for | Absent means |
|---|---|---|
| `ANTHROPIC_API_KEY` | the casual generator | that path cannot run |
| `HF_TOKEN` | the cEDH model gateway | decks still build; no narrative |
| `MTG_V1_DSN` | live `mtg_v1` card and tournament facts (`mtg_consumer` role) | the lab uses its fixture corpus and says so |
| `CEDH_MODEL_BASE_URL`, `CEDH_MODEL_ID`, `CEDH_MODEL_PROVIDER` | overriding the model per deployment | the values in `config/cedh.yaml` |
| `SABER_SECRET_KEY` | stable sessions and CSRF | a random key; sessions do not survive a restart |

Cost is bounded by design: every model call — Anthropic or DeepSeek — logs token usage to one `cost_log` table and is refused once **one** monthly spend ceiling (`llm.monthly_cost_ceiling_usd`) is reached. One ceiling across both providers, because a per-provider ceiling is two soft limits rather than one hard stop.

## Project layout

```
src/sabermetrics/
  cedh/             # the cEDH Deck Lab: repositories, model gateway, evidence,
                    #   deterministic builder, simulator boundary, candidate export
  analytics/        # scoring, filters, empirical valuation, clustering  (casual)
  pipeline/         # deck builder, role generators, synergy optimizer    (casual)
  reasoning/        # Anthropic client, profile synthesis, deck vet       (casual)
  ingestion/        # Scryfall, Archidekt, EDHREC, TopDeck                (casual)
  reference_layer/  # rules/strategy RAG for grounded reasoning           (casual)
  ui/               # Flask app: portal, admin, cEDH lab
config/             # settings, strategy packs, curated evidence (YAML)
fixtures/cedh/      # offline corpus, tournament and simulation fixtures
scripts/            # setup, ingestion, scheduled refresh, fixture generation
tests/              # ~980 tests
```

`cedh/` imports nothing from `analytics/`, `pipeline/`, `reasoning/`,
`ingestion/` or `reference_layer/`, and no vendor model SDK. Both rules are
asserted by tests over the import graph rather than left as conventions.

Automated data refresh (nightly prices, weekly decklists, monthly rulings, quarterly set releases) is wired for macOS `launchd`; see `scripts/install_launchd.sh`.

## Development

```bash
pytest                    # the whole suite: no network, no Postgres, no model, no binary
ruff check src tests
black src tests
mypy src
```

The default suite is offline by construction, and a test asserts it. Live
integrations are opt-in and each skips unless its dependency is configured:

```bash
pytest -m postgres        # needs MTG_V1_DSN
pytest -m model           # needs HF_TOKEN; spends real money
pytest -m simulator       # needs CEDH_SIMULATOR_BIN
```

Type hints are required; data structures that cross module boundaries use Pydantic v2. Core scoring functions, filters, and parsers must have unit tests.

## Scope & non-goals

Deliberately **not** built, and not planned:

- Formats other than Commander/EDH
- Mobile UI (desktop localhost only)
- Real-time gameplay assistance or game simulation
- Manual data entry or human-in-the-loop labeling of any kind

Multi-user support and hosted access were on this list until the July 2026 pivot, ruled out as firmly as the items above. Multi-user is now built, and access is via a private Tailscale tailnet rather than the public internet — the Cloudflare Tunnel that was once planned was never deployed and is no longer the design ([ADR-026](docs/deployment.md)).

The guiding constraints are locality (one process, one machine), bounded cost (every operation has a budget), and observability (every recommendation cites its sources).

---

*Unofficial fan project. Not affiliated with or endorsed by Wizards of the Coast. Magic: The Gathering is a trademark of Wizards of the Coast LLC. Card data and prices come from [Scryfall](https://scryfall.com).*
