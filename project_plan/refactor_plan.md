# Refactoring Plan — Sabermetrics for Magic

_Authored 2026-06-04. Based on a full read of the ~19k-LOC `src/sabermetrics/` tree._

## Overall assessment

The architecture is sound: clean layering (`ingestion → analytics → reasoning →
pipeline → ui`), a strong central LLM client with prompt caching + cost logging
(`reasoning/client.py`), a single-source schema (`scripts/setup_db.py`), and a
central `config.py` Settings singleton. The problems below are **erosion under
growth** — duplication, two god modules, and one architectural gap (DB access).
None require relitigating the ADRs.

**Leave alone (already solid):** `reasoning/client.py`, the decklist ingestion
base (`ingestion/_decklist_base.py`), the `config.py` Settings singleton.

**Cross-cutting risk:** items 1 and 4 can change generated-deck output. Land
golden-output snapshot tests for a few fixed (commander, budget, power) inputs
*before* touching scoring/DB hydration. The existing ~414 tests likely don't pin
full-deck output.

---

## Tier 1 — Highest leverage

### 1. Database access layer (`db.py` + repositories)
**Problem.** No central DB module. `sqlite3.connect()` is opened independently in
**36 files** (6× `ingestion/edhrec.py`, 5× `ui/routes.py`, 5× `_decklist_base.py`,
4× `pipeline/deck_builder.py`). **227+ raw SQL statements**, with the same queries
copy-pasted — e.g. `SELECT last_successful_sync FROM source_health WHERE source = ?`
in 6 ingestion modules; `SELECT * FROM cards WHERE id = ?` in 4+. DB rows flow as
bare `dict` through the whole pipeline; Pydantic models only appear at the end.

**Refactor.**
- `db.py`: a `connect(db_path)` context manager setting `row_factory = sqlite3.Row`,
  `PRAGMA foreign_keys`, WAL — one place instead of 36.
- Thin repositories for hot tables: `CardRepo`, `DeckRepo`, `SourceHealthRepo`,
  `CostLogRepo`. Named methods wrapping repeated queries — not a full ORM.
- A single `row_to_card(row) -> Card` hydration helper.

**Why first.** Everything else becomes mockable/testable once DB access is
centralized. Biggest testability win. **Behavior-preserving** if hydration matches
current dict shapes — guard with snapshot tests.

### 2. Collapse the three detectors into one parameterized engine
**Problem.** `ramp_detector.py` / `removal_detector.py` / `protection_detector.py`
(360/390/342 lines) are ~80% identical: byte-for-byte `_strip_reminder_text`, same
positive/negative pattern-match loop, same version-check-then-batch-insert
population pipeline, same `_ensure_*_table`. Risk of silent drift between them.

**Refactor.** `analytics/detectors/base.py` with a `Detector` definition (positive
patterns, negative patterns, classifier fn, scorer fn, table name, detection
version) and a generic `populate_candidates(detector, db_path)`. Each detector
shrinks to a config object + its scoring function. **Removes ~600 lines. Low risk.**

### 3. Split `deck_builder.py` (1603 lines, god orchestrator)
**Problem.** One `DeckBuilder` class owns 20+ methods across unrelated concerns:
validation, filtering, structural scoring, Pareto, infrastructure fill,
optimization, budget redistribution (`_redistribute_budget` ~180 lines), LLM safety
check, narrative synthesis, classification, model assembly, persistence. Stray
helpers (`_tokenize_engine_traits`, `_is_ramp`, `_heuristic_role`) live here too.

**Refactor.** Keep `DeckBuilder.build()` as a thin 8-stage coordinator; extract:
- `pipeline/candidate_prep.py` — filter + role tags + structural score + pareto
- `pipeline/budget.py` — `_redistribute_budget`
- `pipeline/assembly.py` — `_build_deck_model` + `_persist_deck`
- Move `_tokenize_engine_traits` / `_is_ramp` / `_heuristic_role` into `analytics/`.

**Behavior-preserving** but touches the core path — do after snapshot tests exist.

---

## Tier 2 — Meaningful cleanups

### 4. Centralize scoring weights & magic constants
Hardcoded and scattered: CVAR weights (`cvar.py:48`), synergy weights
(`synergy_matrix.py:24`), detector normalization caps (`9.5`, `10.5`), deck-objective
weights (`greedy_optimizer.py`). These are the tuning knobs you'll want to sweep.
Move to `config/scoring.yaml` (matches existing config convention).
**Caveat:** changes output — pair with snapshot tests.

### 5. Shared base for non-decklist ingestion sources
Decklist sources are well-abstracted already. The others (scryfall, edhrec, topdeck,
spellbook, mtgapi) each re-implement identical `_update_source_health()`,
`last_updated()`, and per-source `RateLimiter` wiring. Lift into a concrete base off
the `base.py` protocol, and standardize HTTP retry (Scryfall retries 3×, others 0×).

### 6. Split `deck_patterns.py` (1290 lines)
Two unrelated god-classes in one file: `GameKnightsAnalyzer` (stats/correlations,
174-line `_analyze_decks`) and `KnowledgeBaseBuilder` (17 markdown-formatting
methods). Split into `analytics/deck_stats.py` and `analytics/kb_builder.py`.

### 7. Unify oracle-text pattern matching
Same regexes (e.g. `"search your library for a land"`) and pattern dicts duplicated
across `components.py`, `role_tagger.py`, `theme_patterns.py`, and all three
detectors. Consolidate into `analytics/oracle_patterns.py` as the single source.

---

## Tier 3 — Nice to have

### 8. Shared "evidence → prompt context" helpers
`profiler.py`, `fit.py`, `synthesis.py` each re-implement JSON unpacking, list
formatting, and markdown-code-block stripping of LLM JSON output. Extract to a
`reasoning/prompt_context.py`.

### 9. Schema migrations
`setup_db.py` re-applies idempotent DDL with only a `_schema_version` marker. A
small numbered-migration runner would help as the schema evolves.

---

## Suggested sequencing

1. Golden-output snapshot tests (prerequisite for #1, #3, #4).
2. #2 Detector consolidation (low risk, big line reduction — good warm-up).
3. #1 DB access layer.
4. #3 Split deck_builder.
5. #6, #5, #7 (independent cleanups, any order).
6. #4 Centralize weights (after snapshots are trusted).
7. #8, #9 as time allows.
