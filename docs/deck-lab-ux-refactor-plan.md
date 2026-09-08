# Deck Lab UX refactor plan

Status: proposed; planning only. Based on the attached **Deck Lab UX Mockups.html**, sections 1a–6a, and the repository inspected on 2026-09-05. Mockup text, annotations, and inline styles were inspected; no rendered browser comparison has been performed.

## 0. Production isolation — required before implementation

**User constraint: the refactor must not affect the currently deployed site.** All development, migrations, previews, and testing stay isolated. Production release is a separate, explicitly approved operation; the rollout steps later in this document are preparation, not authorization to deploy.

Repository inspection on 2026-09-05 found that `.github/workflows/ci.yml` tests and builds local CI images on main pushes and pull requests, with no deploy or image-publish step. `deploy/fly.toml` targets app `decklab`, volume `decklab_data`, and database `/data/sabermetrics.db`. These identify protected production targets. External deployment hooks and the live release configuration have not been inspected; do not infer their absence from repository CI.

Before editing application code:

1. Create a dedicated refactor branch in a separate worktree, with its own virtual environment and local runtime directory. Do not alter the checkout or environment used by a running site, merge into main, or restart any existing service. Check for launchd/local serving paths before starting a preview.
2. Use an explicit development `SABER_DB_PATH` outside the existing `data/` directory, synthetic users, fixture card/tournament data, a distinct session secret, and a localhost-only port separate from any live service. Do not inherit production `.env` files or credentials. Audit the effective non-secret configuration before startup because normal app startup can create or migrate schemas.
3. Run migrations only on disposable databases and isolated copies. No production database/volume mount, migration, backfill, restore, upload, or write is permitted during development. Prefer fixtures; use a sanitized, consistent backup copy only if migration rehearsal needs real data. Never copy a live SQLite file directly while it is being written.
4. Mock email, Linear, model, simulator, and refresh integrations. Do not send test invitations/issues or run generation/load tests against production services. Use a dedicated test corpus for integration coverage; even read-only research benchmarking must not consume production database capacity.
5. Keep production `deploy/fly.toml`, secrets, DNS, domain routing, scheduled jobs, and deployed image untouched. Local builds must not publish or overwrite release tags. Run CI without production credentials; review external hooks before any remote push or PR, then use only a non-deploying refactor branch.
6. Default new feature flags to off. They support later controlled release but do not replace separate databases, services, or environments. Do not deploy incomplete code to production merely because its UI flag is off.

**Development exit gate:** document the worktree/branch, isolated DB/assets paths, localhost address, mock integrations, and CI trigger audit. Add a preview startup check that refuses known production targets or missing explicit development paths. All stages below must satisfy this gate.

If a hosted preview is needed, prepare a separate config with a distinct app, volume, hostname, database, secrets, and test integrations. Do not reuse `decklab` or `decklab_data`. Local preview is the default; provisioning or publishing a hosted preview is a separate action.

**Release gate:** first complete the implementation, local/staging verification, migration rehearsal, and a reviewable release/rollback checklist. Then obtain explicit user approval before merging to a deployment branch, deploying to `decklab`, applying any production migration, or changing production configuration. Record the actual current release/image and take a consistent backup as part of that separately authorized release. Until approval, the existing site continues on its current code, data, and configuration.

## 1. Outcome and scope

Turn the existing generated-deck viewer into a persistent deckbuilding workspace, surrounded by two primary destinations: **Research** and **Build**. Implement desktop and mobile designs, including admin and feedback. Keep Flask, Jinja, SQLite application state, the read-only tournament corpus boundary, and existing generation/simulation services.

This is a product and data-model refactor, not just a stylesheet replacement. Deliver it in independently reviewable stages, with the editable Table builder preceding Playmat so the highest-risk interactions sit on a proven persistence layer.

### Design interpretations

- Section 5a resolves earlier “delete Home” annotations: replace the dashboard with an explanatory Home behind the wordmark. Sign-in without a valid explicit destination lands on Build when the user owns a deck, otherwise Home. Preserve safe return URLs.
- Research and Build are the desktop player navigation. Account contains Profile, Sign out, and Admin for admins. The mobile drawer exposes Research, Build, role-gated Admin, identity, and monthly quota. Home remains available through the wordmark.
- Fold Favorites into Research/Build filters and cEDH Lab into the new-deck flow. Preserve existing candidate evidence, simulation reports, and generation workflows as reachable detail views.
- Desktop defaults to Playmat once it ships; phones get Table only, with Text/Grid/Spoiler and no playmat picker. Proposed initial breakpoint: below 768px is Table only; validate intermediate widths before fixing the breakpoint.
- Follow later mobile annotations for layering: launchers sit below sheets/dialogs and disappear while these are open. Position feedback 20px above the safe-area edge normally, 92px when a pinned action bar is present.
- Counts, legality badges, card selections, card mana values, sample sizes, invite expiries, and update timestamps in the mockup are illustrative. Populate from actual data. Calculate main-deck and commander counts rather than hardcoding “99 + 1,” especially for paired commanders.
- No card prices or budget controls in the redesigned player experience. Retain operational spend reporting within Admin Analytics.
- Keep manual editing independent of generation usage limits. Preserve current server-side generation quota behavior and label it accurately; do not silently redefine a monthly generation quota as a saved-deck limit.

## 2. Existing implementation and gaps

Paths below are relative to the repository root.

| Area | Reusable implementation | Required change |
| --- | --- | --- |
| Shell | `ui/templates/base.html`, `admin_base.html`, `ui/auth.py` under `src/sabermetrics/` | Deck Lab identity, two-destination nav, drawer/account menu, workspace shell, sign-in routing |
| Home/Build | `ui/routes.py`, `templates/home.html`, `decks.html`, favorites routes | New Home, unified library including cEDH candidates, edited timestamps, zone summaries, new-deck flow |
| Research | `ui/explore_filters.py`, `templates/explore.html`, `profile_view.html`; `cedh/repositories.py`, `adapters_postgres.py` | All-card search, tournament comparison queries, reverse lookup, scoped metrics and detail tabs |
| Builder | `templates/deck_view.html`, `.visual-grid`, `.visual-stack`, `.visual-spoiler` in `static/style.css` | Editable document, named zones, autosave, common state, Table controls, Playmat interactions |
| Generated output | `db.DecksRepo`, `db.CedhCandidatesRepo`, `ui/cedh_routes.py`, build jobs | Non-destructive conversion into editable documents; retain original evidence and provenance |
| Admin | `ui/admin_routes.py`, `db.AdminAnalyticsRepo`, admin templates | Activity events/deltas, actionable alerts, filterable users, async quota edits, persistent invite result |
| Feedback | `ui/issue_feedback.py`, `feedback_linear.py`, `feedback_images.py`, `static/issue-feedback.*` | New presentation, Bug/Idea mapping, optional context consent, screenshot capture/fallback, overlay coordination |

The current deck page renders composition, charts, images, and ratings; its routes do not offer card/zone editing. The current metadata protocol loads a commander evidence slice, rather than a paginated cross-commander research table. Existing `top_cuts` explicitly means a known event cut, so it cannot simply be relabeled “Top 16.”

## 3. Architecture and persistence

### Server and client boundaries

Keep Jinja for pages and initial data. Introduce focused `research_routes.py` and `builder_routes.py`, domain services, and repositories instead of expanding the large `ui/routes.py` and inline deck-view script. Register through `ui/app.py`.

Use browser ES modules for a single deck store, save queue, commands, selectors, and separate Table/Playmat renderers. Avoid a framework migration as a prerequisite. Reuse and adapt existing visual CSS, but extract its hardcoded palette and layout assumptions. Build shared controls for buttons, tabs, chips, menus, sheets, dialogs, card images, status, and empty states.

### Proposed document model

| Entity | Core fields and rules |
| --- | --- |
| Deck document | ID, owner, title, format, source kind/ID, created/edited times, revision; original generated result remains immutable |
| Deck entry | Stable entry ID, deck ID, oracle ID, optional printing ID, quantity, commander/mainboard designation, zone ID, order |
| Zone | ID, deck ID, user name, order; protected Unsorted fallback; the Commander section is derived from commander designation |
| Deck presentation | Per-deck surface, snap/outlines/dimming; zone positions, spread/fan mode, manual card placement where applicable |
| View preferences | View, display mode, group, sort, density, collapsed sections; keep per-user/device choices distinct from shared deck content |
| Activity event | Actor, action, subject, timestamp, minimal event metadata for admin counts and timeline |
| Share grant | Deck ID, revocable opaque read-only token digest, creation/revocation metadata |

Use oracle identity for legality/deduplication and printing identity for art. A card's role/category is metadata, not its user-defined zone. Every mainboard entry belongs to exactly one zone. Deleting a zone atomically moves its entries to Unsorted; it never deletes cards. Support repeated basic lands and card-specific quantity exceptions. Permit incomplete drafts and report validation issues accurately.

Add a versioned, idempotent SQLite migration following the repository's setup/migration conventions. Enforce foreign keys on the new repository's connections and test against existing tables before considering any global change. Add owner/deck indexes and uniqueness constraints.

Create documents from legacy decks and cEDH candidates with a unique source mapping so repeated conversion cannot duplicate them. Copy commanders into the command section and all other cards into Unsorted. Preserve source IDs, owners, favorites, ratings, timestamps, and original reports. Do not infer user zones from generated role labels. Backfill or lazily convert through the same service; preview counts on a copied database before selecting rollout strategy.

### Mutation contract

- Proposed endpoints: document GET, create deck, batch commands, create/revoke share, text export, playmat upload; all authenticated mutations require ownership and CSRF checks.
- Commands cover rename, commander assignment, add/remove/update quantity, move/reorder entries, create/rename/delete zones, and presentation updates. Batch additions commit once.
- Each mutation carries the expected document revision and an idempotency key. Apply atomically and return the canonical updated revision, counts, and validation result. Reject stale revisions with a conflict response; never silently overwrite a second tab's edits.
- Optimistic local changes feed a serialized, debounced queue. Show Saving, Saved, Retry, and Conflict states. Only a server acknowledgment earns “Saved.” Retain pending edits locally for reconnect/reload recovery and clear user-scoped recovery data on sign-out.
- Normal in-app navigation waits for pending writes. Failed/offline saves remain visibly recoverable; the mockup's “leaving is never destructive” requires this behavior, not just removal of the unsaved-change dialog.
- Recompute curve, color, counts, and legality from the same content state in both views. Changing view/group/sort must never reassign cards or alter membership. Invalidate source simulation relevance after content edits; link to the original result as applying to the original list.

## 4. Research data and behavior

Add a read-only Research service over card/corpus repositories. Extend the product's `mtg_v1` adapter only where the published facts support the query. Audit available columns before committing to upstream contract changes; do not query private ingestion tables.

Implement URL-backed search, tab, filters, window, minimum event size, sort, and pagination. Cards searches the card corpus; Commanders combines commander identity and available tournament metrics; Metagame presents the field aggregate for the same scope. Use a compact overall field summary plus ranked identities for the otherwise unspecified Metagame tab. Cards and Compare have no full mockup: use the same shell and a simple side-by-side commander comparison with matching windows.

Required filters include color identity/mode, mana value, favorites, meta-share range, archetype, and “plays this card.” All-card advanced search includes name, oracle text, type line, color/mode, mana-value comparator, rarity, and role. Reuse safe query building; provide a raw Scryfall-syntax escape hatch via an explicit parser/provider adapter, with unsupported syntax explained rather than ignored.

Define metric contracts before rendering:

- **Meta share:** qualifying entries for an identity divided by all qualifying entries in the selected cohort.
- **Top 16 rate:** qualifying known finishes at positions 1–16 divided by entries with known finishes for that identity. Display finish coverage; never substitute event top-cut counts.
- **Inclusion:** lists containing a card divided by eligible recorded lists with usable card data. State this denominator separately from tournament entries and disclose missing-list coverage.
- **Trend:** percentage-point change against the immediately preceding equal-length window with the same event filter. Show unavailable when the comparison cannot be computed.
- **Counts/averages:** distinguish tournament entries, recorded lists, and events. Define average deck MV consistently, including land treatment, and expose sample size.

Use one snapshot and scope per response. Cache by snapshot, window, event threshold, query, filters, and sort. Render unavailable data distinctly from zero and show actual refresh time. Fixtures stay clearly identified.

Commander detail includes image/identity, scoped metrics, favorites, Compare, Commonly played category filters, evidence-backed Winning lines, Recent lists, and Rulings. Lines retain source citations and limitations. Card detail exposes actual card facts, rulings where available, and relevant build entry points.

“Start a build” initializes a commander draft. A row + adds into an explicitly selected deck/active zone, or creates a clearly identified commander draft if none is selected. “Add top 40 to a new deck” creates one draft in one transaction, uses deterministic inclusion ordering, excludes commanders/duplicates, and reports cards skipped by current legality/identity rules or missing data. Never copy the mockup's sample cards as seed content.

## 5. Screen implementation

### Shared shell and Home — 1a, 3a, 5a, 6a

Apply Chivo body type, Azeret Mono numeric type, tabular numerals, dark surfaces (`#0a0d16`, `#12172a`, `#1b2238`), border `#262e4a`, text `#e8eaf2`, muted `#98a0bd`, brand `#e94560`, and lighter brand text `#ff8093`. Brand fills use near-black labels. Measure contrast and focus states in the final implementation.

Home uses the two primary actions, Research/Build explanations, three-step guide, and recent work. Mobile wording describes zones/list displays and omits desktop-only playmat promises. Use real counts and timestamps or omit unsupported claims.

Provide desktop account navigation and a mobile drawer with 48px rows. Builder uses a full-width workspace shell with Build breadcrumb/back action, accessible Home destination, title, save state, validation, view controls, and account menu.

### Build library — 3a and 6a

Unify saved documents and convertible existing output. Add title/commander search, All/Favorites/Recently edited, sort by edited time, zone chips, accurate validation summary, favorite buttons, and Open builder. New deck supports commander, strategy pack through existing jobs, and empty draft. Mobile uses a single card column and pinned New deck action. Include first-deck, no-match, generation pending, and failed-job states.

### Table builder and add cards — 2a and 6a

Implement editable quantities, move/remove controls, collapsible zones, zone creation, independent Group and Sort, density, Text/Grid/Spoiler, hover/focus preview, and deck statistics. Reuse one ordered selection across display modes. Add bulk select/move and keyboard-accessible alternatives to all dragging.

Simple add-card search is debounced name typeahead. Advanced widens the desktop rail; on mobile it opens a sheet. Lock legal search scope to commander identity when commanders exist; an empty draft first prompts commander selection or clearly indicates an unrestricted draft scope. Show active destination and support multi-select/add once. Cancel stale searches. Handle loading, empty results, unavailable images, and failed additions.

On phones, zone chips navigate/filter sections and a pinned Add cards button remains reachable. Card taps expose preview and quantity/move/remove actions without hover. Grid cards use responsive sizing and readable fallback names; spoiler includes relevant oracle text.

### Playmat and picker — 2a

Build a pannable DOM board using Pointer Events, with zoom/fit, Space-drag pan, card/zone dragging, labeled targets, visible source gaps, active zone, double-click addition, spread/fan stacks, zone menus, and persisted layout. Convert pointer coordinates through zoom/pan correctly. Distinguish moving cards within zones from moving zones on the board. Respect text-input focus before applying shortcuts.

Zone menus implement spread/fan, rename, select all, mana-value sort, and safe deletion into Unsorted. Right rail shows curve, color, and zone counts. Feedback remains clear of zoom controls.

Ship five CSS surfaces: Slate Grid, Felt Weave, Deep Field, Graph Paper, Void. Picker saves per-deck surface, snap, outlines, and outside-zone dimming. Cancel discards preview changes. Upload supports user art with a proposed 10 MB/25-megapixel limit, server-side image decoding/re-encoding, dimensions validation, and persistent storage on the app volume. Use the feedback image-handling patterns where applicable. Provide darkening beneath cards and cleanup of replaced assets.

Export produces a deterministic plain-text decklist. Share creates a revocable read-only link; shared views contain no account, feedback, private generation intent, or mutation controls. This is the proposed behavior for the otherwise unspecified Share button.

### Admin — 1a and 6a

Use Overview/Users/Analytics and Player app navigation, with mobile drawer equivalents. Analytics groups the existing feedback, popular-commanders, cost, and export capabilities so existing functionality remains reachable.

Overview shows decks built, verdicts, cards added, and active builders since the admin's previous visit, with an explicitly equal preceding comparison interval. Capture the previous visit before updating it. New events power cards-added and recent activity; do not fabricate historical events. First visit or incomplete history gets a stated period and unavailable deltas. Every tile and attention item links to its corresponding filtered destination.

Attention items derive from pending-invite age, actual unsupported simulation results, and card-feedback aggregates. Nightly refresh appears only when backed by an ingested refresh status/event.

Users gets search, status counts, invited-first default ordering, fewer desktop columns, mobile cards, explicit status glyphs, and current-user marking. Quota edits save asynchronously with Saving/Saved/Error, retaining current role/status protections.

Invitation results show delivery status, recipient, real expiry, Copy link, and Resend. Existing invite sending consumes the raw token inside `_deliver_invite`; refactor it to return a structured result. Retain the result in the current page until dismissed, not a transient flash. Do not introduce plaintext token storage solely to redisplay it after reload: older invited rows can issue a fresh link through Resend with explicit rotation semantics.

### Feedback — 2a and 6a

Reuse the existing Linear submission path and upload validation. Replace category selector with Bug/Idea; map Idea to the existing suggestion category while preserving historical UX feedback. Add optional details and an attachment/context checkbox whose choice the server honors. Capture a screenshot with preview when supported; provide manual upload when capture is unavailable. Explicitly handle cross-origin art/capture failures.

Keep identity disclosures accurate, separate optional diagnostics from required authenticated attribution, and preserve retry/session-expiry/duplicate-submission handling. Apply low-opacity brand tint, solid top edge, mobile sheet sizing, and shared overlay rules. No new Linear integration is needed for the redesign.

## 6. Delivery sequence and completion gates

| Stage | Work | Exit gate |
| --- | --- | --- |
| 0. Isolation | Dedicated worktree/branch/runtime; explicit disposable DB; fixtures and mocked integrations; startup and CI target checks | Production-isolation gate in section 0 is documented and satisfied before application edits |
| 1. Foundation | Record baseline routes/screens; tokens, reusable controls, player/admin/workspace shells, overlay rules | Desktop and 390px shell matches design; keyboard drawer/menu behavior works; auth regressions pass |
| 2. Documents | Schema/repositories, source conversion, commands, revisions, validation, events, recovery | Migration is repeatable on a DB copy; ownership enforced; retry/conflict cannot lose or duplicate cards |
| 3. Table vertical slice | Create deck → search/add → zones/quantities → save/reload → export; all three displays and mobile sheet | All displays yield identical membership/counts; mobile edit workflow works without hover/drag |
| 4. Library and Home | Unified Build, favorites/recent/filter/sort, new-deck generation handoff, Home, login destination | Existing generated decks/candidates remain reachable; favorites and safe return URLs preserved |
| 5. Research | Scoped aggregation/search APIs, three tabs, details, filters, compare, single/bulk add | Metrics match fixtures with explicit denominators; missing data is distinct; Research feeds saved drafts |
| 6. Playmat | Board interactions, layouts, picker/upload, share | Table↔Playmat is lossless; reload restores layout; share is read-only and revocable; phones load Table |
| 7. Admin and feedback | Activity/deltas/attention, responsive users/invites/quotas, feedback redesign | Real expiry and delivery state shown; async errors recover; checkbox controls actual attachments/context |
| 8. Release preparation | Legacy redirects tested in isolation, visual/accessibility review, migration rehearsal, proposed small-beta rollout | Full critical journeys pass; backup/rollback documented; no required action remains a decorative placeholder; production cutover waits for separate explicit approval |

Dependencies: stage 2 is required for 3–6 and new admin activity; stage 5's repository/query work can start after the metric definitions while Table is built. Prioritize this sequence over beginning with freeform drag-and-drop.

## 7. Verification and rollout

All checks below run in the isolated development/test environment. Production rollout is conditional on the release gate in section 0; no live-site test writes or deployments are part of implementation.

- Extend existing auth, favorites, quota, build-job, cEDH, admin, invitation, and feedback suites. Keep backend generation/simulation contract tests unchanged unless an intentional behavior change requires adjustment.
- Add focused tests for transactional zone deletion, quantity exceptions, paired commanders, source conversion idempotence, preservation of ratings/favorites, stale revisions, repeated mutation IDs, owner isolation, and share revocation.
- Verify research with explicit fixture denominators, missing finishes/lists, equal-window trend comparisons, partner identities, pagination and filters, deterministic top-40 additions, and parameterized queries.
- Add browser journeys for new deck through reload/export; research-to-draft; Table/Grid/Spoiler/Playmat consistency; failed save/reconnect/two-tab conflict; keyboard zone move; mobile add sheet; invite and quota edits; feedback with diagnostics on/off.
- Capture visual comparisons at 1440px, 1200px, 1000px, and 390×844, plus 320px and an intermediate tablet width. Home must scroll as a document; only the desktop playmat intentionally pans horizontally. Validate 44px actions, 48px drawer rows, focus traps/return, Escape, screen-reader labels, reduced motion, safe areas, and launcher/bar overlap.
- Check actual 100-card decks, long card/zone names, multiface cards, image failures, no tournament data, empty library, quota exhaustion, slow requests, and unavailable generation/simulation services. Lazy-load art and avoid unbounded corpus queries or excessive DOM work during dragging.
- Introduce separate rollout flags for new library/builder, Research, and Playmat. Rehearse the additive migration on a SQLite backup and compare owner/card/favorite totals. Retain old output/report routes and data during the beta.
- Redirect old Explore/Favorites/cEDH landing URLs to equivalent new destinations while retaining candidate reports. A rollback must retain newly edited documents: disable Playmat or new navigation while keeping Table access available, rather than reverting to code that cannot read new work.

## 8. Assumptions to validate during implementation

These have recommended defaults above and do not block the plan: exact tablet breakpoint; whether sharing is accessible without sign-in; raw Scryfall syntax provider and supported operations; final quota copy; top-16 coverage and archetype classification availability; and the scope of Compare/Metagame views not fully drawn in the attachment. Verify corpus capabilities before promising complete research metrics. No simulator or solver redesign is part of this work.
