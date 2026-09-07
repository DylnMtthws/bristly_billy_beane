# Refactor production rollout

Prepared 2026-09-07. This is a deployment plan; production has not been changed.

## Outcome and approach

Release the completed `deck-lab-refactor` worktree to the existing Fly app
`dylnmtthws-decklab`, retaining `https://dylnmtthws-decklab.fly.dev`, accounts,
saved decks, secrets, integrations, and the attached `/data` volume. Use an
in-place update of the single app machine, with a short maintenance window.
Keep exactly one app process/machine writing SQLite. No DNS cutover or new
production app is needed.

Fly supports targeting an existing app with `-a` and deploying a prebuilt image.
Its canary and blue/green strategies create additional machines, and release
commands run without mounted volumes. Use a rolling update of the one existing
machine and perform SQLite migrations through normal app startup instead.
References: [deployment behavior](https://fly.io/docs/launch/deploy/),
[CLI options](https://fly.io/docs/flyctl/deploy/).

## Evidence and remaining uncertainty

- Main worktree: `/Users/dylan/Projects/bristly_billy_beane`, branch `main`,
  commit `729d3b8`. Refactor worktree: this repository, branch
  `deck-lab-refactor`, commit `f6f0bdd`, plus substantial uncommitted changes
  and untracked avatar/template/playmat files. The release must include those
  reviewed changes, not just the current branch commit.
- Public `/healthz` and `/login` returned HTTP 200 during planning.
  Health reports version `0.1.0`; that does not identify a deployed commit or
  prove database, Research, email, or simulator functionality.
- Focused tests passed: 41 tests across `test_deploy_config.py`,
  `test_deck_documents.py`, `test_deck_lab_redesign.py`, and
  `test_profile_avatar.py`. Full CI and artifact verification remain release gates.
- Latest local deployment record is
  `/Users/dylan/Projects/deployment-backups/linear-feedback-implementation-status.md`:
  machine `2872537a9e3348`, volume `vol_vp2q3wklozkm6z24`, image digest
  `sha256:ec3764ff12352932417ccaf880c5ba98c895116062ac27a3634379cab58ae330`.
  These are historical references, not freshly verified rollback targets.
- Fly CLI exists at `/private/tmp/mtg-deployment-prs/fly-cli/flyctl` but has no
  authenticated session available. Current machine/image/config/secret names
  must be read after login before any release action.

## 1. Freeze a reproducible release

Review tracked and untracked changes, commit the intended refactor, and record
the complete release SHA. Keep preview databases, local configuration, and
credentials out of the commit and Docker build context. Review `.dockerignore`:
exclude `.deck-lab-dev/` and `.git` worktree metadata as well as existing DB/env
exclusions. Build from a clean release checkout.

Preferred workflow: PR from `deck-lab-refactor` to `main`, required checks,
merge, and build the resulting main SHA. Inspect repository deployment hooks
before pushing; checked-in CI currently tests/builds but does not deploy.
Merging alone will not make the refactor live. A direct release of a clean
feature-branch SHA is technically possible but would leave main behind production.

Resolve these readiness issues before calling the artifact deployable:

1. Static assets: `pyproject.toml` declares `ui/static/*`, while the new image
   is nested at `ui/static/playmats/night-ritual.jpg`. Verify the installed wheel
   includes it and add an explicit nested package-data glob if needed. Test
   `/static/playmats/night-ritual.jpg` from the installed container, outside the
   source checkout. Include all new avatar templates, CSS, and JavaScript.
2. Research data: `ResearchRepo` queries SQLite `cards`,
   `commander_candidates`, `tournament_results`, `deck_cards`, and `card_rulings`.
   It does not automatically use `MTG_V1_DSN`. Check production-copy schema,
   counts, date coverage, known cards/commanders, and representative queries.
   If that corpus is absent or stale, implement and verify a production data
   source/refresh path before full rollout. An empty screen is not acceptance.
3. Schema and old data: startup adds deck-document/tag/activity tables,
   presentation dimensions, and `user_avatars`. Rehearse two startups on a
   disposable production backup; verify integrity, existing ownership/account
   state, saved artifacts, and legacy-to-editable imports. Keep old generated
   decks and cEDH candidates intact; avoid bulk rewriting them at cutover.
4. Behavior: monthly deck quotas have been removed even in fallback routes.
   Feature flags cannot fully undo this release's behavior changes.

Run CI-equivalent Ruff, Black, mypy, and the full pytest suite. Build the
Linux AMD64 container with `SABER_BUILD_SHA=<release-sha>`, enforce CI's 400 MB
limit, and run existing installed-schema, recovery, and feedback smoke checks.
Add refactor acceptance with redesign enabled and dev mode disabled, using a
disposable database and mocked outbound email/feedback. Verify actual image
contents, uid/gid 1001 write access, and successful restart persistence.

## 2. Prepare the exact production configuration

After Fly login, read status, releases, machine details, volume details, secret
names, and `flyctl config show -a dylnmtthws-decklab --toml`. Save the previous
configuration and exact current image digest for rollback without exporting
secret values. Confirm exactly one app machine and its existing attached volume.

Create a reviewed production config at the release checkout root, such as
`fly.production.toml`, based on the effective live config. The draft
`deploy/fly.toml` uses the wrong app name and omits later email/Linear settings.
Keeping the production config beside `Dockerfile` also avoids the config-relative
Dockerfile path problem recorded in prior deployments. Validate it with Fly CLI.

Preserve the live region, machine resources, volume mount, hybrid auth, public
mode, proxy settings, health check, stable session secret, Postgres/simulator
settings, email settings, and all Linear routing settings. In particular retain
`SABER_PUBLIC_URL=https://dylnmtthws-decklab.fly.dev`. Add:

```toml
# Add within the existing [env] table.
SABER_DECK_LAB_REDESIGN = "1"
SABER_DECK_LAB_BUILDER = "1"
SABER_DECK_LAB_RESEARCH = "1"
SABER_DECK_LAB_PLAYMAT = "1"
SABER_DECK_LAB_DEV = "0"
SABER_DECK_LAB_ASSET_DIR = "/data/deck-lab-assets"
```

Verify there are no conflicting staged secrets or flag overrides. Do not copy
local preview settings into production. Avatar uploads live in SQLite;
custom playmats live in `/data/deck-lab-assets` and require file backups too.

## 3. Build, rehearse, and prepare recovery before cutover

Build/push the candidate without updating production machines, using the clean
release checkout and reviewed config:

```bash
flyctl deploy . -a dylnmtthws-decklab -c fly.production.toml \
  --build-only --push --build-arg SABER_BUILD_SHA=<release-sha>
```

Record the registry digest, pull that exact image for installed-container and
production-copy acceptance, and promote that same digest. Dependency versions
are not fully locked, so rebuilding at cutover would create an untested artifact.
Rehearse the current production image against the migrated disposable copy as
well; confirm image rollback compatibility before opening the new UI to users.

At the maintenance window, prevent new app writes/build submissions using a
verified traffic-maintenance mechanism while retaining operator access, and
let active jobs finish. Restarts otherwise mark nonterminal builds interrupted.
Keep the write pause through backup, migration, and initial smoke checks.

Take a fresh SQLite online backup using `sabermetrics db-backup` in the current
container, archive any existing `/data/deck-lab-assets` while writes are paused,
and take a Fly volume snapshot. Download the DB/files backup off the production
volume, record checksums and table counts, and verify `PRAGMA integrity_check`.
Confirm recovery can restore both DB and files with the app stopped. The older
September 5/6 backups are historical evidence, not a cutover backup.

## 4. Replace the running release

Execute only after the candidate, production-copy rehearsal, configuration,
backup, and rollback gates pass:

```bash
flyctl deploy . -a dylnmtthws-decklab -c fly.production.toml \
  --image registry.fly.io/dylnmtthws-decklab@sha256:<verified-candidate-digest> \
  --strategy rolling --ha=false
```

This updates the existing single machine and runs idempotent startup migrations
against its existing volume. Keep Fly health checks enabled and watch startup
logs and memory. Allow a brief outage; do not promise zero downtime for this
single-machine SQLite architecture. Do not use a Fly release command for these
volume-backed migrations.

## 5. Acceptance and reopening

- Confirm exact deployed image/build SHA, healthy machine, exactly one app
  machine, original volume attachment, and SQLite integrity.
- Check HTTPS health/login, existing user sign-in/session behavior, admin access,
  redesigned navigation, and desktop/mobile rendering with all static assets.
- Confirm existing saved decks appear; import an old artifact into an editable
  document; create/edit/save/reopen/export a test deck; exercise sharing and
  ownership restrictions, Research filters/details, avatar upload, and playmat
  upload/persistence. Use designated test data and preserve existing records.
- Validate production corpus access and one end-to-end cEDH build/simulation.
  `/healthz` intentionally does not check external dependencies.
- Validate recovery/invite and feedback configuration with mocked delivery.
  Do not send real emails or create Linear issues without explicit authorization
  for those test messages; track any real delivery acceptance separately.
- Reopen writes after critical checks pass. Observe errors, latency, memory,
  volume space, and failed jobs for 15–30 minutes; record release SHA, digest,
  deployment time, backup paths, checks, and any remaining acceptance items.

## Rollback

Rollback triggers include startup/migration failure, lost access to existing
accounts or saved decks, broken core build/save flows, or sustained server errors.

For isolated UI trouble, disable the affected feature or all four rollout flags
in the production config and redeploy the same image. Because subflags were set
explicitly, changing only the master flag will not disable them. This preserves
data but does not restore quotas or all shared UI/auth behavior; new documents
may be inaccessible in the fallback UI until the feature is restored.

For a release-wide regression, redeploy the freshly captured previous image
digest with the saved previous config using the same single-machine strategy.
Prefer keeping the migrated DB/files, subject to the compatibility rehearsal.
Preserve the current auth/session-version implementation: production password
recovery has already been used, so an older pre-recovery image is not a safe target.

Only restore the pre-cutover DB and files if image rollback cannot operate on
the migrated state. Stop app writes/process first, preserve a fresh forensic
backup, and restore the matched DB/files set with correct ownership. Restoration
after reopening would discard newer writes and needs a deliberate recovery
decision. Confirm integrity, auth, decks, integrations, machine count, and volume
attachment before restoring traffic.
