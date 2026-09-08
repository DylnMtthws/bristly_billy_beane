# Review feedback fixes and release Deck Lab

Feedback fixes arrive as draft GitHub pull requests. Cursor proposes the patch;
the separate Dispatcher verifies it against the original base and sends the exact
candidate, test evidence and browser captures to an isolated Codex/Astra reviewer.
Blocking findings permit at most two repair rounds within the original deadline.
A stale or non-passing review cannot publish a candidate.

## Accept a fix

1. Open the draft PR and follow its private Tailscale preview link. Connect to the
   tailnet and use `preview@example.test` / `local-preview-only`, a synthetic account.
   Each preview identifies its candidate and expires after eight hours. Request a
   fresh preview/review if expired; it never silently switches to another patch.
2. Read the test evidence, Astra outcome and advisory limitations. Current browser
   fixtures cover one/two commanders at desktop and mobile sizes; other behaviors
   require their own evidence. A passing AI review is not proof of complete coverage.
3. Try the reported behavior. Mark the PR ready and merge when satisfied. The
   required `test`, `container` and `review` checks must pass against the current base.
   Automated branches also require a publisher-authored `dispatcher/astra` status
   on their exact head commit. Changing the patch requires new evidence and review.

The owner currently creates and accepts PRs through the same GitHub identity, so
manual merge is the acceptance action. GitHub does not allow approving your own PR.
The Dispatcher has no merge or deployment code, and neither coding nor reviewing
containers receive a GitHub publisher or Fly credential.

## Publish the live version

Merging does not deploy. Wait for the successful **CI** push run on `main` to finish.
Its container job retains a `release-SHA-ATTEMPT` artifact for 14 days.

1. Open **Actions → Deploy production → Run workflow** on `main`.
2. Enter the numeric ID of that passing CI run (the number in its GitHub URL).
3. Review the preparation job's summary showing the commit and image identity.
4. Approve the pending **production** environment deployment.

Only the configured release owner can start promotion. The environment permits
`main` and requires the owner's approval before releasing its Fly credential.
Preparation rejects stale main commits, unsuccessful or foreign CI runs and changed
archives/configuration; it runs installed-image smoke checks without production
credentials. Promotion pushes that same image to Fly and deploys its immutable
digest, without rebuilding it.

The workflow requires a 90-day app-scoped Fly deploy token saved as `FLY_API_TOKEN`
in the repository's **production environment**, not as a broad repository secret.
Rotate it before expiry. Token provisioning alone does not run a deployment.

## Production safeguards and first release

The controller checks the configured single machine and existing `/data` volume,
records the current image, makes an integrity-checked SQLite backup and waits for
a new completed Fly volume snapshot. It preserves one machine and the same volume.
After deployment it verifies the image digest and `/healthz` build identity.

Ordinary promotion currently permits UI assets/templates, tests and documentation.
Backend, authentication, schema, dependency or configuration changes require a
separate reviewed migration/recovery procedure. The first release has a narrow
bootstrap exception tied to the configured live base SHA: it permits this release
infrastructure, the OCI revision label and the exact health response addition.
It also accepts the exact previously reviewed workspace exclusion blobs and root
planning documentation already on main. It does not permit arbitrary backend changes. Once live advances from that base,
the exception cannot apply again.

This app has one SQLite writer; a rolling replacement can briefly interrupt service.
Backups remain in `/data/release-backups` and snapshots remain with Fly. Monitor the
1 GB volume and remove obsolete backups through an explicit maintenance procedure;
this workflow does not automatically delete database backups.

## When something stops

Missing auth, expired previews, repeated review blockers and exhausted repair
bounds stop automation for operator attention. Refresh evidence on a new base if
main moved. Automatic feedback intake remains disabled until deliberately enabled
in the Dispatcher's project policy; installing the worker does not approve tickets.

A failed release retains `production-receipt-ATTEMPT` metadata, including the stage
reached and available rollback image/backup/snapshot identities. Inspect actual
machine health before retrying: a failed check may occur after the new image starts.
Use the recorded previous immutable image for an explicitly approved rollback.
Database restore is a separate decision because it can discard writes made after
backup. Never mount a second writer on the production volume or restore a preview
fixture database into production. No deployment or rollback happens automatically.
