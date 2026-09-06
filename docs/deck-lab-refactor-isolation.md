# Deck Lab refactor isolation

The refactor is developed on branch `deck-lab-refactor` in the dedicated
worktree `/Users/dylan/Projects/bristly_billy_beane-deck-lab-refactor`.

- Master feature gate: `SABER_DECK_LAB_REDESIGN=1` (off by default).
  `SABER_DECK_LAB_BUILDER`, `SABER_DECK_LAB_RESEARCH`, and
  `SABER_DECK_LAB_PLAYMAT` inherit the master gate and can each be set to `0`
  for a staged rollout or rollback.
- Local guard: `SABER_DECK_LAB_DEV=1` refuses public mode,
  `/data/sabermetrics.db`, and the repository's `data/sabermetrics.db`.
- Local database: `.deck-lab-dev/decklab.db` (ignored and disposable).
- Local server: bind `127.0.0.1` on a port that is not used by an existing app.
- Integrations: no `MTG_V1_DSN`, `HF_TOKEN`, `CEDH_SIMULATOR_URL`, Resend, or
  Linear delivery configuration. Tests use local corpus fixtures and mocks.
- CI audit: `.github/workflows/ci.yml` tests and builds an unpublished local
  container. It has no deploy step. External repository hooks must be checked
  before any push.
- Protected production targets: Fly app `decklab`, volume `decklab_data`, and
  `/data/sabermetrics.db`. Refactor development must not address them.

Running a local preview:

```bash
SABER_SKIP_DOTENV=1 \
SABER_DECK_LAB_REDESIGN=1 \
SABER_DECK_LAB_DEV=1 \
SABER_DB_PATH=.deck-lab-dev/decklab.db \
SABER_AUTH_MODE=password \
SABER_COOKIE_SECURE=0 \
SABER_SECRET_KEY=deck-lab-isolated-preview-only \
PYTHONPATH=. sabermetrics serve --host 127.0.0.1 --port 5179
```

Production release, migration, configuration, or deployment is outside this
implementation and requires separate explicit approval.
