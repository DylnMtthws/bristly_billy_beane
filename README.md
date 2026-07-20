# Sabermetrics for Magic

**A Commander/EDH deck builder that reasons about *why* a commander wants a card — not just how often other people run it.**

Most EDH tools (EDHREC, deck power calculators, Moxfield analyzers) are frequency counters: they recommend cards because other decks include them. Sabermetrics starts from the commander's actual rules text and asks a different question — *what does this deck need to function, and which cards deliver the most impact per dollar?* It grounds that reasoning in four independent sources (card oracle text, aggregated decklists, community discussion, and the official rules) and spends LLM calls only where cheap deterministic filters can't decide.

The name is the thesis: apply *Moneyball*-style value analysis to Magic — find the cards with the best cost-to-impact ratio, not the most popular ones.

> **Scope.** This is a personal, single-user research tool, self-hosted on one machine, Commander format only. It is not a hosted service and has no multi-user support. See [Scope & non-goals](#scope--non-goals).

---

## Table of contents

- [What a build produces](#what-a-build-produces)
- [How it works](#how-it-works)
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
- **Secrets** live in `.env` (only `ANTHROPIC_API_KEY` today).
- **Reference data and synergy rules** live under `config/` (`synergy_rules.yaml`, `game_changers.yaml`, `auto_include_cards.yaml`, …).

Cost is bounded by design: every LLM call routes through a wrapper that logs token usage and enforces a monthly spend ceiling.

## Project layout

```
src/sabermetrics/
  analytics/        # scoring, filters, empirical valuation, clustering
  pipeline/         # deck builder, role generators, synergy optimizer, mana base
  reasoning/        # LLM client, profile synthesis, deck vet, prompts
  ingestion/        # Scryfall, Archidekt, EDHREC, TopDeck data sources
  reference_layer/  # rules/strategy RAG for grounded reasoning
  ui/               # local Flask app
config/             # settings + tunable rules (YAML)
scripts/            # setup, ingestion, scheduled refresh jobs
tests/              # ~690 tests
```

Automated data refresh (nightly prices, weekly decklists, monthly rulings, quarterly set releases) is wired for macOS `launchd`; see `scripts/install_launchd.sh`.

## Development

```bash
pytest                    # run the test suite (~690 tests)
ruff check src tests
black src tests
mypy src
```

Type hints are required; data structures that cross module boundaries use Pydantic v2. Core scoring functions, filters, and parsers must have unit tests.

## Scope & non-goals

Deliberately **not** built, and not planned:

- Multi-user support or public hosting
- Formats other than Commander/EDH
- Mobile UI (desktop localhost only)
- Real-time gameplay assistance or game simulation
- Manual data entry or human-in-the-loop labeling of any kind

The guiding constraints are locality (one process, one machine), bounded cost (every operation has a budget), and observability (every recommendation cites its sources).

---

*Unofficial fan project. Not affiliated with or endorsed by Wizards of the Coast. Magic: The Gathering is a trademark of Wizards of the Coast LLC. Card data and prices come from [Scryfall](https://scryfall.com).*
