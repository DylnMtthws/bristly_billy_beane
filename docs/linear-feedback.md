# Issue feedback widget

Signed-in, active users can send a Bug, UX, or Suggestion report from the
bottom-right Feedback button. The compact dialog accepts 10–5,000 characters
and one optional PNG, JPEG, or WebP image through file selection, drag/drop, or
clipboard paste. Closing and reopening preserves the draft within the same
page. Leaving/reloading the page warns about an unsent draft; drafts are not
saved in browser storage. This is a one-message form, with no conversation,
automatic screenshot capture, email notifications, or Linear status sync.
An expired session offers sign-in in a new tab. Retrying obtains a fresh CSRF
token through an authenticated, uncached same-origin endpoint and preserves
the original report and submission identity.

On mobile the launcher is a 48px icon and the dialog fits the viewport, safe
area, and available height. The native dialog provides keyboard focus
containment and Escape dismissal; closing returns focus to the launcher. Image
previews have a remove control. Status messages use an accessible live region.

## Delivery and privacy

`POST /feedback/submit` requires an active session and CSRF protection before
parsing multipart uploads. Limit: 5 attempts/minute, 10/hour, and 20 new reports
per account per rolling day. A single bounded image-processing slot protects
the existing 1 GB machine; concurrent submissions receive retry guidance.
The daily report limit survives process restarts; short-term attempt limits
use the app's existing memory-backed limiter.

Images are limited to 10,000,000 bytes and 25,000,000 pixels. Their contents,
filename extension, and MIME type must agree. Animated and non-raster formats
(including SVG/GIF/HEIC) are rejected. A photo picker that supplies HEIC must
instead export JPEG, or the user can attach a PNG screenshot. Pillow applies
EXIF orientation then rebuilds pixels into a metadata-free PNG, preserving
screenshot text. If the re-encoded image exceeds 10 MB, the user is asked to
crop it. Browser validation is convenience; server validation is authoritative.

Images are temporarily buffered/spooled by the upload parser outside `/data`,
closed with the request, and never stored in the app's persistent volume or
served publicly. The server obtains a private upload destination from Linear,
validates it, forwards the image without forwarding the API key to storage,
and embeds the unsigned private asset URL in the issue. Signed upload URLs
and provider error bodies must never appear in logs. Linear retains the issue
and image under the workspace's own retention and access policies. An upload
whose issue creation never finishes can remain as an orphaned private Linear
asset; the app does not invoke Linear's destructive asset deletion endpoint.

The report contains the authenticated account's email/display name, category,
UTC time, sanitized path, a page label derived from the application route,
app/build version, browser family and major version, OS family, and viewport.
Deck/candidate/job identifiers are included when present in the path. The path
must match a user/admin application route; auth, invite, recovery, unknown and
external paths are excluded. Query strings, fragments, IPs, cookies, form
contents, console logs, and deck contents are not collected. User-supplied
description and context are fenced literal text in Linear, so Markdown cannot
inject remote image imports or mentions. A screenshot can still contain
personal information visibly; the widget asks users to review it.

Synchronous success is shown only after Linear confirms issue creation:
“Thanks—your feedback was sent.” The response contains no Linear URL,
identifier, issue content, or credentials. On a transport/provider failure,
the same report and image remain available to retry. The form is held fixed
after an uncertain delivery to preserve its identity.

`issue_feedback_receipts` stores only a user ID, request ID, payload digest,
server-generated Linear issue UUID, timestamps, delivery status and optional
unsigned private asset URL. It stores no description, email or image bytes.
These small receipts stay with the SQLite database/backup for duplicate
protection. This is not a background delivery queue. Retries reconcile Linear
by the same UUID after a timeout or restart, reuse an already-uploaded image,
and never substitute a new issue UUID. An in-progress receipt has a 3-minute
lease for restart recovery. Reusing a receipt with another user or changed
content fails. An ambiguous response never becomes an unconditional second
issue creation with a new ID.

## Linear setup

Target project:
https://linear.app/dylnmtthws/project/cedh-deck-dashboard-f33a94f793f7/overview

Issues belong to a **team** and its Triage workflow state, and are assigned to
the project. They receive no priority and no assignee, with `user-feedback`
plus the `bug`, `ux`, or `suggestion` label. The setup helper resolves the real
project/team/state UUIDs. It requires a team choice for a multi-team project
and requires Triage to be enabled; it never silently routes to Backlog.

1. In Linear, go to **Settings → Account → Security & access → Personal API
   keys**. Create a key scoped to the intended project team. Runtime needs
   Read (including reconciliation) and Create issues/upload access. A setup
   key additionally needs Write only if creating labels through the helper.
   Alternatively create the four labels in Linear first and keep the runtime
   key narrower. No Admin permission is needed.
2. Run the helper in an interactive terminal on the Mac mini. It prompts for
   the key without echoing or saving it. Do not put the key in arguments or
   paste it into chat:

   ```sh
   .venv/bin/python scripts/configure_linear_feedback.py --create-missing-labels
   ```

   Omit that flag for read-only discovery. Existing labels are reused
   case-insensitively, preferring team-specific labels. The helper prints only
   non-secret routing settings. It does not deploy, set Fly secrets, create a
   Linear project/team, or send a test issue.
3. Supply the runtime key as Fly secret `LINEAR_API_KEY` through CLI stdin
   (hidden-input staging, as with Resend), never as a CLI value argument.
   Put the helper's five non-secret settings into the deployment environment:
   `LINEAR_FEEDBACK_ENABLED=true`, `LINEAR_TEAM_ID`, `LINEAR_PROJECT_ID`,
   `LINEAR_TRIAGE_STATE_ID`, `LINEAR_LABEL_IDS` (JSON with all four label IDs).
   The enabled app fails startup if these settings are incomplete/invalid;
   disabled feedback requires no Linear configuration and renders no widget.
4. Supply the deployed commit as Docker build argument `SABER_BUILD_SHA`
   (or runtime environment variable of that name). The local default is
   `unknown`; production must supply the actual commit. Existing email,
   consumer DSN and simulator settings remain required.

The supplied project URL identifies a destination; live access, team, Triage
and labels cannot be verified without an authorized key. Do not describe the
integration as enabled in production until the actual routing and one real
image-bearing report have been verified. No new app, volume, database or
background service is needed. Linear workspace plan limits still apply.

## Verification and rollout

- Run the feedback tests and the normal lint/format/type/full-suite gates.
- Build Linux AMD64 with the 400 MB image ceiling. CI also runs
  `scripts/smoke_issue_feedback.py` against the installed container from `/tmp`
  with networking disabled and mocked delivery. This verifies Pillow,
  packaged templates/assets, CSRF, image upload and issue routing.
- `scripts/smoke_issue_feedback.py --serve` provides a fake local preview on
  port 5077. It creates a temporary database, fake credentials, and intercepts
  every Linear HTTP request. Never use its credentials outside that preview.
- Follow the session's separate push/merge approvals. Before deployment,
  take a consistent SQLite backup and deploy to the single existing machine
  and volume. Schema setup adds receipts idempotently; disabling the feature
  is a reversible rollback and does not require dropping its table.
- Acceptance: signed-out pages have no widget; signed-in users can submit
  text-only and image reports, see only confirmation, and find no Linear URLs
  in responses. In Linear verify project, team Triage state, no priority, both
  labels, useful context, and a privately accessible sanitized image.
- Exercise failure/retry with mocked delivery before production, including a
  response lost after creation, so the visible retry still creates one issue.
  Verify desktop/mobile geometry, keyboard dismissal, draft preservation,
  paste/drop preview/removal and duplicate-click suppression.
