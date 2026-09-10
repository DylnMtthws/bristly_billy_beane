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
7. Ruff, Black, mypy and offline regression tests pass. Changes are reviewable in a PR and deployed only to qa.decklab.studio.

The coordinator has a draft implementation in this workspace. Inspect it independently, fix any defects you can demonstrate, and strengthen missing behavioral regression coverage. You may edit app.py, builder_routes.py, research_routes.py, ui/navigation.py, the navigation JS, shared base template/CSS, and focused tests/docs. Do not touch credentials, CI/deployment, dependencies, auth/admin logic or unrelated features. Do not merge, publish, contact Linear or deploy. Use only this supplied source and local tests. No other agents.
