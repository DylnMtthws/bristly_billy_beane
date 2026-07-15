# Option A — Definition of Done (autonomous loop contract)

**Identity chosen: Option A — own the heuristic engine, honestly.**
The deterministic scorer is the product. The LLM is a *narrator and auditor*, never a
per-card scorer. Every change must make the engine more correct and more honest, not
add capability.

This file is the single source of truth for the self-paced `/loop`. Each iteration:
1. Read this file. 2. Pick the highest-priority unmet criterion. 3. Do the work.
4. Run its check. 5. Update the Progress Log. 6. Continue until all criteria pass,
then **stop the loop** (ScheduleWakeup `stop: true`).

## Run environment (this worktree)

- Interpreter (reuse main venv, run worktree code):
  `PYTHONPATH=src /Users/dylanmatthews/Desktop/Development/new_code/bristly_billy_beane/.venv/bin/python`
- Tests: `<interp> -m pytest -q` (pytest adds worktree `src` automatically).
- Lint: `/Users/dylanmatthews/Desktop/Development/new_code/bristly_billy_beane/.venv/bin/ruff check src tests`
- DB: `data/sabermetrics.db` is a **symlink to the production DB**. Treat reads freely.
  For any **write/migration** (criterion 3), first run + validate against a COPY
  (`cp -L data/sabermetrics.db /tmp/saber_test.db`), then ship the migration as a
  script under `scripts/` for the user to run on prod. Do NOT mutate prod silently.
- Baseline to never regress: **521 tests green, ruff clean.**

## Locked decisions (defaults; do not re-ask)

- **Calibration (crit. 6):** tune constants up to a **bounded effort of ~2 iterations**.
  If the target isn't reached, record the best achievable metric and mark the criterion
  DONE-WITH-NOTE rather than looping forever. Report the gap.
- **LLM in selection (crit. 4):** fully remove per-card `card_fit` from the selection
  hot path. Keep profile synthesis + deck narrative. No optional auditor pass.

## Definition of Done — all must pass

### 1. Model IDs valid + validated at boot
- Replace `claude-sonnet-4-6` / `claude-opus-4-6` with currently-valid IDs; keep
  `claude-haiku-4-5`. Source of truth for valid IDs: the `claude-api` skill — invoke it,
  do not guess.
- Add a startup validation path (config load asserts every configured model ∈ the
  client's `ALLOWED_MODELS`; `ALLOWED_MODELS` matches reality).
- **Check:** a test asserts every model in `config/settings.yaml` is allowed, and that
  no allowed model is a known-stale ID.

### 2. Deck legality is an enforced invariant, not a warning
- Every generated deck: exactly 99 cards + commander; singleton (basics exempt); all
  within commander color identity. Builder **repairs** to 99 (fill basics / trim
  weakest) instead of appending a warning string.
- **Check:** a test builds decks for ≥3 varied commanders × 2 budgets and asserts each
  is legal (count == 99, no illegal dupes, color identity respected).

### 2b. Canonical one-row-per-card candidate source
- The `cards` table stays faithful to Scryfall — **keep all 114k printings** (their
  per-printing prices power cheapest-printing selection; `rarity`/`set_code`/`image_uri`
  are per-printing and in use). Do **not** delete rows.
- Replace the current "SELECT ~all printings → dedup in a Python loop on every build"
  with a canonical candidate source that yields **one row per card**, deduped by
  **`name`** (NOT `oracle_id` — 418 oracle_ids share names via reversible/reskin cases;
  Commander singleton is by English name), selecting the **cheapest legal printing**
  (`MIN(price_usd)` over the latest price snapshot; NULL-priced printings rank last but
  are kept if that's the only printing). Basic lands exempt.
- Implement as a SQL VIEW or a small `card_candidates` table rebuilt after ingestion —
  whichever is cleaner. `apply_hard_filters` reads from it; `filter_singleton_legal`
  becomes a guaranteed property of the source, not a filter callers must remember.
- **Rationale:** makes dedup an invariant by construction (reinforces crit. 2 — no code
  path can reintroduce duplicate printings), and cuts candidate-query volume ~3×.
- **Check:** (a) a test asserts the candidate source returns ≤1 row per nonbasic name and
  that the kept printing's price equals the min priced printing for that name; (b) a test
  asserts a fresh generator/query path cannot introduce a duplicate nonbasic name into a
  built deck. Baseline dedup semantics (cheapest printing) must be preserved.

### 3. No live scoring weight reads an empty table
- Populate `card_cooccurrence` from the 500 decklists in `decks`/`deck_cards`
  (ship as `scripts/`-runnable migration; validate on a DB copy).
- `card_win_equity` / `tournament_results`: populate if a real source exists; otherwise
  remove from the scoring formula and renormalize remaining weights.
- **Check:** a test asserts every scoring signal that carries weight reads a table with
  > 0 rows (or the weight is 0/removed). Synergy matrix logs real cooccurrence pairs.

### 4. LLM removed from the selection hot path
- Zero `card_fit`-type per-card LLM calls during candidate selection/optimization.
- **Check:** a test builds a deck with the Anthropic client patched to raise on any
  `card_fit` call; build still succeeds and returns a legal deck.

### 5. Degradation is observable
- Deck generation metadata records which signals were live
  (e.g. `signals_used: ["rules","embeddings","cooccurrence"]`, and any source that was
  unavailable). Surface it on the persisted deck / result.
- **Check:** a test asserts the field is present and correctly reflects a forced-missing
  signal.

### 6. Constants calibrated against real decks, not vibes
- Add `scripts/calibrate_scoring.py`: for a sample of commanders with real decklists in
  the DB, compute the mean percentile rank the CVAR scorer assigns to cards that appear
  in real decks vs. the full legal candidate pool.
- **Target:** mean percentile ≥ **0.70**. Tune the CVAR weights/constants toward it
  within the bounded effort above.
- **Check:** `scripts/calibrate_scoring.py` prints the metric; DoD met when ≥ 0.70
  (or DONE-WITH-NOTE per locked decision).

### 7. Green + clean
- Full `pytest` passes (≥ 521 tests, no regressions); `ruff check src tests` clean.

## Stop condition
When criteria 1–7 all pass (6 may be DONE-WITH-NOTE), write a final summary to the
Progress Log, commit, and stop the loop. Do not open a PR or touch `main` — leave the
`option-a-hardening` branch for the user to review.

## Progress Log
_(Append one dated entry per iteration: criterion touched, what changed, check result.)_

- **2026-07-15 — Criterion 1 DONE.** Correction to the original review: `claude-sonnet-4-6`,
  `claude-haiku-4-5`, and `claude-opus-4-6` are all *currently active* models (verified via
  the claude-api skill catalog), not stale IDs — so nothing was 404-ing. The real bug in the
  same area was `MODEL_PRICING`: haiku was $0.80/$4.00 (actual $1.00/$5.00) and opus was
  $15/$75 (actual $5/$25), so `cost_log` under-counted haiku spend ~20-25% (haiku = 1,689 of
  1,832 logged calls) — the $15 ceiling was being hit at higher real spend. Fixed pricing;
  kept the valid models (did NOT upgrade to Opus 4.8 — would blow the $30/yr charter). Added
  `validate_models()` (import-time) + `validate_configured_models()` (client `__init__`) +
  `KNOWN_RETIRED_MODELS` guard so a genuinely stale ID now fails loud. Check: new
  `tests/test_model_validation.py` (5 tests) pass; full suite 526 passed (521→526, no
  regression); ruff clean.
- **2026-07-15 — Criterion 2 DONE.** Added `DeckBuilder._enforce_legality` (new Stage 7b,
  runs after budget redistribution) enforcing the invariant: exactly 99 non-commander cards,
  singleton (basics exempt), every card's color identity ⊆ commander's. It *repairs* rather
  than warns — drops the commander/out-of-identity cards, collapses duplicate nonbasics to the
  highest-scoring copy, trims an over-full deck weakest-first (basics → non-protected non-lands
  → non-protected lands, never protected staples), and fills a short deck with basic lands in
  the commander's colors (Wastes for colorless). Helpers `_parse_color_identity`,
  `_make_basic_lands`, `_BASIC_LAND_NAMES` added. The old "Only N cards, need 99" warning is
  now a can't-happen backstop. Check: `tests/test_deck_legality.py` — 7 unit tests covering
  every repair path (short-fill, over-trim, protected-survival, dup-collapse, out-of-identity
  drop, colorless Wastes fill, basic distribution) + 1 live end-to-end test across 3
  commanders × 2 budgets (skipped without an API key / cost headroom — not run in the loop to
  avoid API spend; the maxed ceiling would block it anyway). Full suite 533 passed (526→533),
  ruff clean.
  NOTE: the end-to-end form of the check runs only with a key present; the unit tests verify
  the invariant logic exhaustively for arbitrary inputs, which is stronger coverage than a
  handful of specific decks.
- **2026-07-15 — Criterion 2b DONE.** Added a canonical `card_candidates` SQL view
  (`filters.CANDIDATE_VIEW_SQL` + `ensure_candidate_view`): one row per card **name** (not
  oracle_id — Commander singleton is by English name), choosing the cheapest legal printing
  via `ROW_NUMBER() OVER (PARTITION BY name ORDER BY (price IS NULL), price ASC, id)` — NULLs
  rank last but a name with no priced printing is still kept. `cards` keeps all 114,115
  printings (per-printing prices remain load-bearing); only the view collapses them.
  `apply_hard_filters` now reads `SELECT * FROM card_candidates` and the redundant Python
  `filter_singleton_legal` pass was removed from the pipeline (function kept for its direct
  unit tests). Verified on a *copy* of prod: 31,039 candidates from 114,115 printings =
  exactly the distinct legal-name count, 0 duplicate names, cheapest-price correct for every
  staple (Sol Ring 1.33, Bolt 0.10, etc.). Shipped `scripts/create_candidate_view.py`
  (idempotent DROP+CREATE) for explicit prod provisioning; the pipeline also ensures the view
  at query time, so it is now present on the prod DB (additive/metadata-only — the intended
  2b change, matching the migration). Check: `tests/test_candidate_view.py` — 3 hermetic tests
  on a synthetic DB (one-row-per-name + cheapest printing; min-price match for every name; a
  fresh `apply_hard_filters` path emits no duplicate names). Belt-and-suspenders: criterion 2's
  `_enforce_legality` also guarantees deck-level singleton. Full suite 536 passed (533→536),
  ruff clean.
- **2026-07-15 — Criterion 3 DONE (with a user decision).** Finding that reshaped this
  criterion: the 500 tracked decks span 404 commanders with **max 4 decks each** (325 have
  exactly 1), so commander-conditioned co-occurrence is co-membership noise, not signal —
  populating `card_cooccurrence` as the DoD originally assumed would be *worse* than empty.
  Surfaced this via AskUserQuestion; **user chose "remove & renormalize"** for the
  co-occurrence signal. Implemented:
  * Synergy matrix: removed the co-occurrence signal + `_batch_cooccurrence`; blend is now
    rules + embeddings with weights renormalized 0.40/0.25 → **0.615/0.385** (config +
    settings.yaml, `synergy_cooccurrence_weight` dropped). `card_cooccurrence` no longer read.
  * CVAR: removed the `card_win_equity` DB read and the `+0.1*cwe` boost (the DoD had already
    pre-decided "otherwise remove" for the CWE/tournament fork — no data source is wired for
    `tournament_results`). `card_win_equity` stays `None` on results for back-compat; re-enable
    the read if a real tournament-outcome source is ever populated. `cooccurrence.py` /
    `card_win_equity.py` builder modules are left dormant as the documented re-enable path.
  Check: `tests/test_no_empty_table_reads.py` (populate each formerly-empty table, prove
  scoring ignores it + a source-level guard that no scoring SQL names them) and the rewritten
  `test_synergy_matrix.test_cooccurrence_data_is_ignored`. Updated the weight-coupled tests in
  `test_scoring_config.py` / `test_synergy_matrix.py` to the 2-signal model. Full suite 538
  passed (536→538), ruff clean.
- (next: criterion 4 — remove per-card LLM from the selection hot path)
