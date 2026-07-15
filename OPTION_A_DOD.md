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

- (not started)
