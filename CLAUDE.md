# CLAUDE.md

<instructions>
This file is auto-loaded by Claude Code at the start of every session in this repository. It provides project context, conventions, and decisions that should inform every code generation. Read this file in full before responding to any request. When making implementation choices not specified here, defer to the principles in this file.
</instructions>

---

<context name="project_identity">

## Project Identity

- **Name:** Sabermetrics for Magic
- **Type:** Multi-user, self-hosted web app — currently a **closed beta** (owner-invited testers). Was a single-user personal tool through the P8 build; pivoted 2026-07 (see ADR-015..018); pivoted again 2026-09 to a **cEDH Deck Lab** (see ADR-019..024).
- **Product direction (2026-09):** The product is exclusively a **cEDH Deck Lab**. It is no longer a general casual Commander power-level generator. The casual generator still ships and still works, but it is legacy: new work goes into `src/sabermetrics/cedh/`.
- **Owner / Admin:** Dylan Matthews (sole admin; provisions all accounts)
- **Purpose:** Build competitive Commander (cEDH) deck candidates deterministically from curated strategy packs, ground them in tournament evidence with stated sample sizes, validate them against a goldfishing simulator, and explain them. **There is no budget and no price anywhere in the cEDH engine** — cEDH is proxy-normal, so the objective is performance (ADR-025).
- **Phase 1 goal:** Invite trusted Magic players, let them generate decks and leave thumbs-up/down + comments per card (and a verdict per deck); the owner + Claude mine that feedback to improve the generator.
- **Inspiration:** Sabermetric "moneyball" methodology in professional sports analytics — find cards with the best cost-to-impact ratio.

### What this repository owns

| Owned here | Owned elsewhere |
|---|---|
| Product workflow and UI | Scryfall / tournament ingestion (`ingestion_pipeline_mtg`) |
| User intent and constraints | Physical ingestion tables (`mtg_internal`) |
| cEDH commander and strategy selection | Simulation mechanics (`commander_simulator`) |
| Evidence retrieval and ranking | Simulator strategy definitions |
| Deterministic candidate construction | Card facts, prices, legality |
| Model-provider integration | |
| Simulator orchestration | |
| Explanations and presentation | |

**Never edit or import the sibling repositories.** Integrate through the
`mtg_v1` Postgres schema and versioned JSON only. See
`docs/integration-handoff.md` for the exact contracts.

</context>

---

<context name="critical_constraints">

## Critical Constraints (Hard Requirements)

These constraints are non-negotiable. Every code generation must respect them.

```yaml
constraints:
  users: "multi-user, admin-provisioned only — NO self-registration; the admin creates every account (ADR-015)"
  hosting: "self-hosted on the Mac mini; the Flask app stays bound to 127.0.0.1 and is published to the TAILNET by `tailscale serve` (ADR-026, supersedes the never-deployed Cloudflare Tunnel of ADR-016). Never bind 0.0.0.0, never port-forward, and never run `tailscale funnel` — Funnel is public AND sets no identity headers, so every request would arrive anonymous."
  auth: "THREE MODES via SABER_AUTH_MODE. `tailscale` (deployed): identity comes from the `tailscale serve` proxy headers; no passwords, no invite links, no login form. `hybrid` (public Funnel deployment): tailnet identity when present, password form otherwise — REQUIRED for Funnel because Funnel traffic is anonymous. `password` (default; local dev + tests): email + argon2id + invite links. BOTH are admin-provisioned — a tailnet identity with no users row is REFUSED, never auto-created (ADR-015). Header trust requires the request source to be in Tailscale's CGNAT range 100.64.0.0/10 AND the absence of the Tailscale-Funnel-Request marker (Tailscale never sets identity on Funnel, so its presence there is a forgery); TRUST_TAILSCALE_HEADERS_FROM_ANY_ADDRESS is test-only and raises outside TESTING. CSRF on every POST; hardened session cookies; login throttling; per-user authorization (a user must never see another user's decks/feedback)."
  per_user_quota: "20 generated decks per calendar month (admin-overridable per user); resets on the 1st (ADR-017)"
  cost_ceiling: "global monthly $ ceiling remains the ultimate hard stop across ALL users (settings.llm.monthly_cost_ceiling_usd)"
  # Feedback-collection input (thumbs + comments from testers) is the point of Phase 1 and is NOT the forbidden 'manual data entry' below — that rule bars manual entry of the CARD/PRICE/METRIC corpus, which stays fully automated.
  manual_data_entry: "forbidden for the card/price/metric corpus (still fully automated via APIs + structured scrapes)"
  human_in_the_loop: "forbidden in the data-acquisition pipeline (does NOT apply to user feedback)"
  excluded_data_sources:
    - youtube_event_scraping  # Validation gap unacceptable
    - personal_pod_logging    # Breaks "no manual input" principle
    - manual_winner_labeling  # Breaks "no manual input" principle
  annual_cost_target_usd: 30
  annual_cost_ceiling_usd: 100
  per_deck_cost_target_usd: 0.15
  per_deck_cost_ceiling_usd: 0.50
  format_scope: "Commander (EDH) only; the NEW path is competitive (cEDH) only"
  ui_scope: "Desktop web UI; three portals — user-facing, admin (/admin), cEDH lab (/lab). No mobile."

  # --- cEDH path (ADR-019..024). Non-negotiable. ---
  cedh_data_boundary: "production repositories query mtg_v1 ONLY, as mtg_consumer. No query may name mtg_internal (assert_v1_only enforces it). The card view is card_any_medium, NEVER card — the default view silently drops 254 Reserved List cards including Tropical Island, Mox Diamond and Lotus Petal."
  cedh_no_network_in_generation: "the cEDH generation path never calls EDHTop16, Scryfall, TopDeck or a deck-hosting site. Strategy material is curated in config/cedh_evidence/, reviewed and versioned."
  cedh_no_legacy_ingestion: "no module under src/sabermetrics/cedh/ imports sabermetrics.ingestion, .pipeline, .reasoning or .analytics, or any vendor model SDK. Asserted by tests over the import graph."
  llm_may_not: "decide legality; invent cards or oracle text; bypass candidate constraints; create simulator mechanics; silently repair a malformed simulator result; select from the whole card corpus without deterministic narrowing."
  llm_may: "normalize free-text intent; select among supported strategy packs; summarize retrieved tournament evidence; compare deterministic alternatives; explain recommendations."
  structured_output: "every machine-used model response uses provider structured output and is validated with Pydantic before use. A response that never validates raises; it is never partially accepted."
  provider_pinning: "production pins the provider (model id ends ':deepinfra'). Dynamic ':cheapest' routing is refused — it makes the recorded model id a guess."
  absence_is_visible: "no tournament data renders as 'no tournament evidence', never an empty result. No simulator renders as 'not simulated', NEVER an invented neutral score. An unsupported commander renders as unsupported."
  popularity_is_not_quality: "an inclusion rate is displayed with its denominator, window and event-size floor, or not displayed."
  no_price_in_the_engine: "the cEDH engine has NO budget and NO price. CardFacts carries no price field, adapters_postgres does not select rep_prices, BuildConstraints has no budget_usd (and sets extra='forbid' so a stale caller passing one FAILS rather than being silently ignored), the UI offers no budget input, and the prompts forbid the model from mentioning cost. Do not reintroduce price 'just for display' — a price field that exists becomes a tie-break. (ADR-025)"
  no_collection_in_the_engine: "the engine does not know who is asking. NO owned-cards / collection input: BuildConstraints has no owned_oracle_ids, IntentClassification has no mentions_owned_cards, and _sort_key takes (oracle_id, pack, role) only. Acquiring or proxying cards for a new deck is the normal case. Consequence worth preserving: a build is reproducible from the pack alone, so the candidate deck_sha256 identifies a LIST, not a list-plus-requester. (ADR-025)"
```

> **Charter note:** This project began as a strictly single-user, localhost-only, budget-focused tool (constraints `user_count: 1`, localhost-only, "no multi-user / no public hosting"). The owner deliberately pivoted it in 2026-07 to a multi-user feedback platform. The constraints above reflect the new charter; ADR-015..018 record the decision and rationale.

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
    card_fit_scoring: "claude-sonnet-4-6"  # batched single call
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
- ~~Multi-user support~~ — **now in scope** as of the 2026-07 pivot (ADR-015); admin-provisioned accounts only, no self-registration
- ~~Public hosting~~ — **now in scope**, but only via Cloudflare Tunnel with the app bound to 127.0.0.1 (ADR-016); still no direct port exposure and no cloud-hosted compute/DB
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
| ADR-011 | Sonnet for the batched fit vet, Sonnet for synthesis, Haiku for screening | Old 12x pricing is stale (Haiku→Sonnet is 3x, Sonnet→Opus 1.67x); one batched vet call is cheaper than per-card Haiku was, and Haiku miscalibrated borderline judgment |
| ADR-012 | Profile cache w/ set-version invalidation | Cheap relevance screening |
| ADR-013 | mtgapi for rulings only, Scryfall for cards | Scryfall has bulk + prices; mtgapi has inline rulings |
| ADR-014 | Rulings join by oracle_id | Rulings persist across reprints |
| ADR-015 | Multi-user, admin-provisioned accounts (no self-register); one-time invite links | Owner controls exactly who can spend tokens; invite links avoid ever sharing/handling passwords |
| ADR-016 | Internet exposure via Cloudflare Tunnel; app stays bound to 127.0.0.1 behind it | Keeps "self-hosted on the Mac mini", free, no port-forwarding; the app is never directly exposed — a real security win over binding 0.0.0.0 |
| ADR-017 | Per-user quota: 20 decks/calendar month (admin-overridable) + retained global $ ceiling | Caps token spend per tester while a global hard stop bounds total cost across everyone |
| ADR-018 | Structured per-card (thumbs+comment) and per-deck (verdict+comment) feedback loop | Phase-1 product goal: harvest deck-literate players' judgments to measure and improve generator quality; this feedback is user input, distinct from the still-automated data corpus |

| ADR-019 | cEDH only; casual power levels removed from the new path | The product is a cEDH Deck Lab. Power-level heuristics answer a different question than "does this list win a tournament round" |
| ADR-020 | Card and tournament facts come from `mtg_v1` through repository interfaces; `card_any_medium`, never `card` | The schema is the contract and the boundary is one adapter. The default `card` view silently drops Reserved List staples, which is the worst failure mode: fewer rows and a success return |
| ADR-021 | Provider-neutral `ModelGateway`; DeepSeek-V4-Flash on DeepInfra, structured output required | A vendor SDK in the generation path makes the provider a rewrite. Structured output plus Pydantic validation is what stops "explain this deck" becoming "invent a deck" |
| ADR-022 | Deterministic construction from curated strategy packs; the model never selects a card | Selection finishes before the first model call, so a provider outage costs prose and nothing else. It is also the only mechanism that actually prevents corpus-wide hallucination |
| ADR-023 | Simulator boundary is versioned JSON over a subprocess; absence is a visible "not simulated" | A neutral score is indistinguishable from a measured one once it is in a table. The simulator's own honesty fields are required, so the number cannot be rendered without them |
| ADR-024 | One cost ledger and one monthly ceiling across both providers | A per-provider ceiling is two soft limits, not one hard stop |
| ADR-027 | Public exposure via Tailscale Funnel is opt-in (`SABER_PUBLIC=1`) and forces `hybrid` auth, a mandatory stable secret key, HSTS, and per-ACCOUNT login lockout | Funnel traffic is anonymous, so a public visitor cannot arrive authenticated and must have a password path. Once /login faces the internet, IP rate limiting alone is weak — an attacker rotates addresses and Funnel traffic may share one — so 5 failures lock the account for 15 minutes, and a locked account refuses even the correct password |
| ADR-026 | Tailnet-only hosting via `tailscale serve`; identity from its proxy headers; accounts still admin-provisioned | The Cloudflare Tunnel (ADR-016) was specified and never deployed. A tunnel puts a login page on the public internet where anyone can knock; a tailnet has no public surface at all, and Tailscale has already authenticated every device on it. Removes passwords, invite tokens, the login form and the reset flow nobody had built yet — one `grant-access` command per tester. Accounts stay because owner-scoping, quota and per-card feedback all need identity |
| ADR-025 | No budget, no card price, and no collection anywhere in the cEDH engine | cEDH is proxy-normal: expensive cards get proxied, so price is not a performance signal, and a budget would not trade money for power — it would just remove the best cards. Owned-cards preference is the same constraint wearing a different hat: building a new deck means acquiring or proxying cards, which is the normal case. Absence is enforced (no field to read, `extra="forbid"` on the constraints) because an available price or collection field always becomes a tie-break eventually. It also buys reproducibility: selection sees only the pack and the role, so the same pack yields the same 99 for everyone. NB: this is about *card* prices — the LLM **token** cost ceiling (ADR-024) is untouched |

> **Charter pivot (2026-07):** ADR-015..018 supersede the original single-user / localhost-only / no-public-hosting posture. Where older ADRs or docs assume one user, the multi-user charter above wins.
>
> **Product pivot (2026-09):** ADR-019..024 make the product a cEDH Deck Lab. Where older ADRs describe casual power levels, budget-as-objective, or local ingestion as the source of card facts, they describe the **legacy** path only. `src/sabermetrics/cedh/` is the product.

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

  - file: docs/deployment.md
    purpose: "How this is deployed and how accounts are provisioned: tailscale
      serve, the identity-header trust model and its stated limits, and the
      grant-access/revoke-access flow."
    read_when: "Before any auth, hosting or account-provisioning work"

  - file: docs/integration-handoff.md
    purpose: "The contracts with the two sibling repositories: what mtg_v1 must
      publish, what the simulator must accept and emit, and the deprecation plan
      for duplicate ingestion. Read before touching anything under cedh/."
    read_when: "Before any cEDH boundary, repository or simulator work"

  - file: docs/project_plan/design.md
    purpose: "High-level vision, goals, constraints, ADRs (LEGACY casual path)"
    read_when: "Before architectural changes"

  - file: docs/project_plan/SKILLS.md
    purpose: "Recurring task workflows and patterns"
    read_when: "When implementing a workflow that has a defined skill"

  - file: docs/project_plan/schema.md
    purpose: "All data schemas (SQL, Pydantic, YAML)"
    read_when: "Before any data model changes or DB queries"

  - file: docs/project_plan/api_contracts.md
    purpose: "Module interfaces and external API contracts"
    read_when: "Before module-to-module integration work"

  - file: docs/project_plan/prompts.md
    purpose: "LLM prompt templates with input/output schemas"
    read_when: "Before any reasoning layer changes"

  - file: docs/project_plan/build_plan.md
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
  original_build: "Complete — P1..P8 (single-user generator). Refresh automation, launchd plists, JSON logging, Karsten mana analysis, deep statistical profiles."
  current_initiative: "cEDH Deck Lab (ADR-019..024). The multi-user + feedback beta (P0..P6) is complete and is now the legacy path."
  current_phase: "cEDH-P1 — Kinnan vertical slice (DONE, offline against fixtures)"
  portal_completed: ["P0", "P1", "P2", "P3", "P4", "P5", "P6"]
  in_progress: []
  blocked: []
  cedh_notes: "src/sabermetrics/cedh/ holds the whole path. repositories.py = CardRepository/MetaRepository Protocols + assert_v1_only guard; adapters_postgres.py = live mtg_v1 (card_any_medium, card_face, card_legality; the four tournament views DO NOT EXIST yet, so PostgresMetaRepository probes and reports unavailable); adapters_fixture.py = the offline corpus every ordinary test uses. model_gateway.py = provider-neutral Protocol + strict JSON-Schema tightening; provider_deepseek.py = OpenAI-compatible over httpx (no vendor SDK); gateway_fixture.py = scripted fake that still validates through the real schema. cost_ledger.py writes the shared cost_log and reads the ONE ceiling. evidence.py = attributed chunks (source, locator, date, commander, snapshot, content hash) with hard bounds. packs.py = strategy packs authored BY NAME in config/cedh_packs/, resolved to oracle_ids at load; a name that does not resolve fails the pack loudly. builder.py = deterministic fill + legality repair (99, singleton, colour identity; basics capped at the land slot count, past which it REFUSES rather than returning a pile of Forests). NO price/affordability logic anywhere — ADR-025. simulator.py = SimulationResult | NotSimulated, no third state. candidate.py = cedh-deck-candidate.v1 by oracle_id. lab.py orchestrates and CROSS-CHECKS every model output against what was built. factory.py reports which mode each part is in; the UI renders those notices. UI at /lab (cedh_routes.py), CLI at `sabermetrics cedh`. Fixtures in fixtures/cedh/ regenerate via scripts/build_cedh_fixtures.py. The Kinnan pack's 99 ARE the list commander_simulator models. NEXT: waiting on the two contracts in docs/integration-handoff.md — mtg_v1 tournament views, and the simulator accepting cedh-deck-candidate.v1 / emitting cedh-simulation-result.v1."
  legacy_notes: "P0 schema; P1 auth (Flask-Login, invites, hardening, create-admin/invite-user CLI); P2 admin user mgmt (/admin, role-gated). Tailwind (PR#11) merged to main; portal branch rebased on it. P3: user portal — db.py FavoritesRepo (commanders+decks toggle/list/ids) + DecksRepo (list_for_owner, owner_of, set_owner, count_this_month) + UsersRepo.update_profile; ui/explore_filters.py (pure WHERE builder over commander_candidates: colors atmost/exactly, abilities=keywords LIKE, price/cmc ranges, sort, pagination); routes: home dashboard (quota meter, recent decks, fav shortcuts), /explore (filter sidebar + hearts + pagination), /favorites/commanders, /favorites/decks, /decks (owner-scoped, delete), /profile (edit + change password), favorite toggle endpoints (JSON, CSRF via X-CSRFToken). Owner-scoping: generate sets owner_id; view/delete/deck-fav authorized (owner or admin → else 403). Templates all Tailwind (home/explore/decks/favorites_*/profile + _macros.html + build form moved to profile_view). index.html DELETED. P4: quota ENFORCED in generate route — global $ ceiling pre-check (503) + per-user calendar-month count vs quota (429 with reset label); mid-build LLMCostCeilingExceeded handled. Cost attribution: contextvars + cost_attribution() CM in reasoning/client.py, _log_cost writes cost_log.user_id/deck_id; route wraps builder.build in cost_attribution(owner, deck_id) with a PRE-MINTED deck_id passed via new DeckBuildRequest.owner_id/deck_id fields (_build_deck_model uses request.deck_id). CLI builds unattributed. PR #12 (P0-P4) MERGED to main; P5+ on new branches. P5: feedback system (the Phase-1 payload). db.py FeedbackRepo (upsert_card ON CONFLICT user/deck/card, upsert_deck ON CONFLICT user/deck, card_map, deck read helpers). Routes: POST /deck/<id>/card/<card_id>/feedback and POST /deck/<id>/feedback — OWNER-ONLY (owner_of == current_user, else 403; admins viewing others' decks get read-only, can_feedback=False). deck_view.html: per-card thumbs+comment cell in the by-type table (prefilled from card_feedback map) + deck-level verdict(good/mixed/bad)+comment panel + feedback JS (autosave, CSRF X-CSRFToken); styles in style.css (.fb-thumb/.verdict-btn). Per-card feedback is ONLY in the by-type table view (default), not by-role/visual — deferred. P5 PR#13 MERGED to main. P6: admin analytics (db.py AdminAnalyticsRepo). Routes on /admin: enhanced overview (KPIs), users (per_user_stats: decks/spend/feedback + detail link), users/<id> drill-down, feedback explorer (card_feedback aggregated by card_name — up/down/net/total/comments, sortable) + feedback/card/<name> drill (all comments) + feedback/export?format=csv|json (CSV=card rows, JSON=card+deck), costs (totals + by call_type + by user, uses cost_log.user_id from P4), commanders (most-generated + most-favorited). Templates admin/feedback|feedback_card|costs|commanders|user_detail + enriched overview/users; admin nav gained Feedback/Costs/Commanders. NB: feedback rows are intentionally KEPT on deck delete (research data; aggregates group by card_name, LEFT JOIN decks). Tests: test_admin_analytics.py. NEXT: P7 deploy — waitress + Cloudflare Tunnel setup doc; set real SABER_SECRET_KEY; run /security-review BEFORE sharing the tunnel URL (release gate)."
```

</context>
