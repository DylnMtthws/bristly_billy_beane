# DYL-24 and DYL-29: navigation performance

Owner initiated this batch in T3. Deliver to QA for owner review; do not merge or deploy production.

Reports: Build/Profile navigation feels slow; selecting Meta takes too long.

Observed baseline on a private copy with 34,570 cards and 11,178 tournament results: Build 41 ms, Profile 14 ms, default Research 540 ms, Meta 397 ms under profiling on the Mac. The Profile server route is already quick; do not claim a password/security problem or change authentication to improve it.

Implementation scope:
- Remove Build queries for retired generated/candidate/job sections while preserving editable decks, filters and ownership.
- Reuse the default public research cohort computed by Commanders when selecting Meta. Bound the cache, invalidate on SQLite/WAL changes and time, and never share account HTML, session/CSRF state or favorites.
- Cache immutable, revision-versioned static assets so Build/Profile transitions avoid repeated stylesheet/script revalidation. Unversioned/development/error/private responses must retain their existing behavior.
- Give normal navigation immediate accessible feedback with a bounded waiting message and correct Back/modified-click behavior. Keep native links working without JavaScript.

Acceptance:
1. Default Commanders then Meta executes one expensive cohort query; filtered searches, changed data and TTL expiry return fresh correct results.
2. Two users cannot inherit each other's favorites or account information.
3. Simultaneous default loads coalesce; a failed fill is not retained.
4. Build no longer touches retired repositories; profile/account behavior is unchanged.
5. Assets use distinct URLs per release and successful matching static URLs can be cached. Private HTML and stale/missing assets never gain immutable public caching.
6. Browser behavior and before/after navigation measurements are recorded. Separate server timings, browser caching and untested networks/browsers.
7. Ruff, Black, mypy and offline regression tests pass. Changes are reviewed as a local commit with recorded evidence and deployed only to qa.decklab.studio. Public GitHub publication was rejected by automatic approval review; keep this candidate unpublished.

The coordinator has a draft implementation in this workspace. Inspect it independently, fix any defects you can demonstrate, and strengthen missing behavioral regression coverage. You may edit app.py, builder_routes.py, research_routes.py, ui/navigation.py, the navigation JS, shared base template/CSS, and focused tests/docs. Do not touch credentials, CI/deployment, dependencies, auth/admin logic or unrelated features. Do not merge, publish, contact Linear or deploy. Use only this supplied source and local tests. No other agents.

## Cursor review notes (before coordinator browser acceptance)

Draft behavior kept: Build lists only editable `deck_documents`; default Commanders/Meta share one process-local public cohort; versioned static URLs (`?v=<40-char sha>`) may be cached immutably; a `role="status"` banner provides bounded waiting feedback without intercepting native navigation.

Defects demonstrated and fixed:

- A failed cache snapshot (including a deepcopy failure after `load()` succeeded) was retained and replayed. The cache now copies before publish.
- Versioned static responses that carried `Set-Cookie`, `private`, or `no-store` still received `public, immutable`. After-request now skips those, and a WSGI wrapper downgrades cookie responses after Flask saves the session.
- Navigation JS threw when the click target had no `closest` (text nodes / non-elements). It now walks to the parent element, ignores non-elements, and clears the banner on Escape.

## Measurements

Recorded 2026-09-10. These numbers are not interchangeable.

**Owner Mac baseline (given, not reproduced here):** private copy with 34,570 cards and 11,178 tournament results. Build 41 ms, Profile 14 ms, default Research 540 ms, Meta 397 ms under profiling. Those include whatever that profiler counted on that machine.

**Server timings here (Flask test client, Linux, 1-card fixture DB, 20 GETs after warmup, no browser, no network):**

| Path | p50 (ms) | mean (ms) | max (ms) |
|---|---:|---:|---:|
| `/build` | 4.13 | 4.23 | 5.49 |
| `/profile` | 2.58 | 2.69 | 4.92 |
| `/research/` | 4.30 | 4.53 | 7.18 |
| `/research/?tab=metagame` | 4.42 | 4.45 | 4.86 |

This corpus cannot show the 540 ms / 397 ms Research/Meta gap. Cohort reuse is asserted by query count: default Commanders then Meta (and Meta then Commanders) runs `ResearchRepo.commanders` once; `q`, `window`, `sort`, `color`, and `favorites=1` each run it again.

**Browser caching:** not measured in Chrome/Safari/Firefox. Asserted from headers and a Node event harness: matching `?v=<sha>` CSS/JS is `public, max-age=31536000, immutable` (200 and 304); unversioned CSS stays Flask's `no-cache`; `/profile` and `/build` HTML have no public immutable cache; missing/stale assets are not immutable. Real repeat-visit savings on Build/Profile therefore depend on an untested browser cache.

**Untested:** the 34k-card Mac copy, production/QA hosts, real networks, Chrome/Firefox/Safari/WebKit, bfcache on device, and no-JS beyond “links still navigate because click is not `preventDefault`.” Keyboard Enter on links is assumed to fire `click` as in desktop browsers.

Do not merge or deploy production. QA only (`qa.decklab.studio`) after coordinator review.
