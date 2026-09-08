# Deck Lab workspace

As of 2026-09-08, `~/Projects/deck_lab` is the canonical checkout of
[`DylnMtthws/commander-deck-engine`](https://github.com/DylnMtthws/commander-deck-engine),
on `main`. Open this directory in your editor and run commands from here.
The repository name and Python package (`sabermetrics`) are unchanged.

## Folder map

```text
~/Projects/
  deck_lab/                         # main checkout and shared Git history
    src/sabermetrics/                # application, including cedh/ and ui/
    config/                         # app settings, strategy packs, evidence
    docs/                           # guides, plans, deployment runbooks
    scripts/                        # setup, maintenance, smoke checks
    tests/                          # canonical test suite
    fly.production.toml             # current Fly production configuration
    .env, .venv/, data/              # private local configuration and state
    worktrees/                      # retained development history, local only
      deck-lab-refactor/
      partner-commanders/
      research-assistant/
    releases/                       # retained detached checkouts, local only
      2026-09-07-refactor/
      2026-09-07-partner-commanders/
  bristly_billy_beane -> deck_lab    # compatibility link for the old local server
  deployment-backups/               # existing private release evidence/backups
  ingestion_pipeline_mtg/           # separate upstream data repository
  commander_simulator/             # separate simulator repository
  edh_solver/                      # separate solver repository
```

The compatibility link preserves absolute paths held by the local preview
that was already running during the move. It contains no duplicate files.
After that preview and any editor sessions using the old path have been
restarted from `deck_lab`, the link can be removed with
`unlink ~/Projects/bristly_billy_beane`. Do not recursively delete through it.

`worktrees/` and `releases/` are excluded from both Git and Docker build
contexts. They share the root `.git` database; they are not independent copies
to upload to GitHub. A fresh clone contains the application and this guide,
without these optional historical checkouts, local credentials, or databases.
Release evidence and private backups remain in their existing external directory.

## Where the updates landed

| Retained checkout | Previous folder | Branch / commit | Status |
| --- | --- | --- | --- |
| Root | `bristly_billy_beane` | `main` | Current source of truth |
| `worktrees/deck-lab-refactor` | `bristly_billy_beane-deck-lab-refactor` | `deck-lab-refactor`, `0a3f3ec` | Merged in PR #29 |
| `worktrees/partner-commanders` | `bristly_billy_beane-partner-fix` | `fix/partner-commanders`, `c9d52c6` | Merged in PR #30 |
| `worktrees/research-assistant` | `bristly_billy_beane-research-assistant` | `research-assistant`, `f6f0bdd` | Code included through PR #29; planning documents tracked on main in `ed99d56` |
| `releases/2026-09-07-refactor` | `bristly_billy_beane-release-20260907` | Detached `3659908` | Historical refactor release |
| `releases/2026-09-07-partner-commanders` | `bristly_billy_beane-partner-release-20260907` | Detached `35f1269` | Source of the latest recorded production release |

All five retained checkout commits are ancestors of `main`. Their working
files, ignored development state, and the research checkout's two untracked
planning documents were preserved. Those planning documents are identical to
the versions already tracked on `main`; there is no unmerged application code
in these five checkouts at the time of organization.

Start with these documents for further work:

- [Research assistant plan](../RESEARCH_ASSISTANT_PLAN.md)
- [Upstream dependencies](upstream-dependencies.md)
- [Deck Lab UX refactor plan](deck-lab-ux-refactor-plan.md)
- [Local preview isolation](deck-lab-refactor-isolation.md)
- [Production deployment](deployment.md)

Plans and previous release records describe their original inspection dates;
their old paths and readiness statements are historical, not current status.

## Daily workflow

```bash
cd ~/Projects/deck_lab
git status
git worktree list
source .venv/bin/activate
pytest -q
```

Use `main` as the starting point for new work. If an isolated checkout is
useful, put it under `worktrees/` and create a fresh branch:

```bash
git worktree add worktrees/my-change -b feat/my-change main
```

Create a separate virtual environment in a new worktree and use a disposable
database as described in the preview isolation guide. Do not share the main
local database or production credentials with an experimental preview.
Use `git worktree move` to relocate a linked checkout; manually moving it
requires `git worktree repair`. Keep release snapshots as historical references.

## Production continuity

Production is [https://dylnmtthws-decklab.fly.dev](https://dylnmtthws-decklab.fly.dev),
Fly app `dylnmtthws-decklab`, with configuration in root `fly.production.toml`.
The latest local release record is
`../deployment-backups/partners-20260907/RELEASE.md`, built from `35f1269`.
Its database and uploaded assets live on Fly's existing persistent volume,
independently of the paths on this Mac.

The workspace cleanup changes documentation and local checkout organization;
application source, dependencies, Dockerfile, and production configuration
are unchanged. No production deployment or migration is needed for it.
On 2026-09-08 the GitHub repository had no webhooks or deployment records,
and its only Actions workflow was CI (tests and local container checks, with
no image publication or deployment). Recheck external hooks before future
pushes when deployment behavior matters.

For a future application release, build from the reviewed `main` commit and
follow the deployment runbook, including state backups and installed-image
checks. Run from the root checkout, never from a retained release directory.
