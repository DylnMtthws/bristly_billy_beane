# Research loading: owner-initiated QA batch

## Problem and intended experience

The QA candidate f323c7a eliminates repeat work when switching Commanders to Meta,
but the first Research request still spends 6–7 seconds computing the public
cohort on the existing 0.5-CPU QA container. Its 30-second cache is invalidated
by every SQLite/WAL write, including unrelated account activity. Owner asked to
reduce real query cost and remove the blank-page wait. Deliver to QA only.

Users should see Research navigation/controls promptly, existing results remain
visible while filters load, and prepared default results are reused across visits,
logins and app restarts. Never display old results as matching newly selected
filters. Announce updating status; replace results only after successful response.

## Implementation and ownership

Coordinator owns optimization of `src/sabermetrics/research.py` and its new query
equivalence tests. Cursor must NOT edit that file. Preserve its `commanders()`
interface and exact metrics, filters, window boundaries, sort, pagination and
zero-data behavior. The coordinator will supply the optimized query at integration.

Cursor owns a bounded persistent public default-cohort snapshot, background
refresh and progressive Research page loading; implement and test this directly.
Allowed: new research cache module, ui/navigation.py, ui/app.py wiring,
ui/research_routes.py, research template/fragments, research JS/CSS, focused tests,
this spec. Additive cache metadata/triggers may live in the new cache module.
Do not change auth/admin logic, dependencies, CI/deployment, other features,
provider integrations or automatic agent controls. Do not publish, deploy, send
messages, access outside the supplied workspace, or launch other agents.

## Public snapshot contract

- Default 90-day, page-1, unfiltered Commanders/Meta public cohort only. Never
  persist session data, HTML, favorites, emails, CSRF, user IDs or private decks.
  Apply current user's favorites to a copied public result on each request.
- Replace coarse DB/WAL stamps with a durable research-corpus revision. Changes
  to cards, tournament_results and research_commander_pairs invalidate default
  results; unrelated users/deck_documents/favorites writes do not. Additive
  SQLite metadata plus triggers are acceptable. Bulk source imports must remain
  atomic; use a single counter row, not an unbounded change log.
- Key snapshots by DB identity, corpus revision, calculation version and date
  window. Day rollover requires refresh even without writes. Persist atomically
  under the existing writable app data directory, bounded to one snapshot.
- Warm in background at app initialization without delaying app health/startup.
  One refresh at a time; repeated requests must not enqueue unlimited threads.
  Recheck revision/date after calculating before publishing a successful result.
  Avoid busy polling. Bounded retry/backoff, log failure class without secrets.
- Serve last successful snapshot during refresh for at most 15 minutes after it
  became stale. Show truthful updated/refresh status. Never relabel stale data as
  fresh. Past that limit, show loading/error/retry until usable results exist.
  Preserve good disk snapshot on failure; recover from malformed/missing files.
  A persisted compatible fresh snapshot must survive process restart.
- Keep DB reads off Flask request context inside worker; use thread-local SQLite
  connections and safe lifecycle. No network jobs, new services, Redis or cost.

## Progressive page and filtering

- If default snapshot is absent, return navigation, tabs and controls immediately
  with accessible results placeholders; fetch/poll boundedly until ready. Do not
  run the expensive default query synchronously on this initial HTML request.
- Enhance GET search/filter/sort/window/pagination and tab transitions with
  authenticated, private/no-store fragment responses. Keep current results
  visible but clearly marked as previous while loading; show busy state and
  polite status. Prevent stale responses winning races (sequence or abort).
- Update URL/history correctly only for successful transitions; Back/Forward,
  deep links, modified clicks, keyboard use and native no-JS fallback must work.
  No-JS may use an explicit full-results link/request that computes synchronously.
  Do not force a SPA rewrite or cache authenticated HTML publicly.
- Preserve favorites, Build deck forms/CSRF, mobile filter controls, sort keyboard
  behavior, image fallbacks, current query/filters and headings after DOM updates.
  Use event delegation or explicit idempotent binding after fragment insertion.
- On failure, keep useful previous results, clearly distinguish them from current
  requested filters, offer retry, and do not leave an infinite spinner. Bound each
  fetch/wait (about 15 seconds); handle login expiry as an auth state, not results.
- Respect reduced motion; no layout overflow at 430px. No speculative prefetch
  in this batch: first reduce and reuse work rather than generate extra queries.

## Verification and acceptance

Focused tests must prove revision changes versus unrelated writes, date rollover,
restart reuse, single refresh, snapshot corruption/failure handling and staleness
limit. Prove initial HTML does not call the slow loader inline; preserve isolation
between two users, auth and CSRF, and private fragment caching headers. Include
behavioral JS tests for races, failure/retry, history and no-JS fallback where
feasible. Update old tests when intentionally changed route contract requires it;
do not bypass production behavior using TESTING-only code to satisfy tests.

Run focused tests and canonical Ruff/Black/mypy checks. Coordinator independently
reviews and runs full regression and native image smokes, then real Chromium and
WebKit QA checks. Record first visit, return after login, restart and filtered
loads separately. Targets: default results within 1 second when prepared, shell
within 500ms when unavailable; warm tab switches under 200ms where network permits.
These are measured acceptance targets, not a promise or a reason to fake timings.

Production remains 12993b2. QA retains the owner's account and decks. Source stays
unpublished in the local repository. Only the owner can approve production after
reviewing the final exact QA candidate. This batch extends DYL-24/DYL-29; do not
mark them Done. Report completed work, tests and any remaining limitations.

## Coordinator integration notes

- Query optimization materializes commander identity sets once, avoiding repeated
  correlated scans while retaining representative printing and pair semantics.
  24 query/filter/window/pagination cases matched the previous query exactly on
  the representative corpus. Native QA default query: 5,726ms before, 1,172ms
  after, before snapshot reuse; small sample, not an end-to-end guarantee.
- Revision tracking also includes card_prices because representative-printing
  selection depends on the existing priced candidate view. Request freshness
  checks are read-only and do not contend for SQLite's writer lock.
- A persistent first-invalidation timestamp prevents repeated corpus changes
  extending the 15-minute stale allowance. A lightweight 30-second monitor
  checks date/corpus changes. Cached data is scoped by DB-specific filename.
- Review restored all Card filters, bounded polling across the complete wait,
  corrected retry URLs/pending history, handled expired auth and malformed
  fragments, and added an actual updated timestamp and no-JS fallback.
