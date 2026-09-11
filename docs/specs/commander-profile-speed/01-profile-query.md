# Commander profile navigation speed

## Problem

Opening a commander profile from the Commanders catalog blocks on `ResearchRepo.commander_detail`. The route currently performs several independent aggregate queries and an expensive representative-list CTE that repeatedly scans the selected commander's recorded cohort and its deck cards to calculate card frequencies and overlap before it can render the page.

## Goal

Keep the existing commander profile content and its data semantics, while making navigation from the catalog responsive for commanders with a large recorded cohort. The request must not wait on an unbounded per-deck scoring pass when a cheaper, semantically equivalent selection is possible.

## Requirements

1. Profile response work must remain local and read-only. Do not add network requests, background model calls, or change Research authentication or caching headers.
2. Preserve the existing metrics, window handling, partner identities, representative-list completeness rules, card grouping, and empty-state behavior.
3. Replace or reshape the representative-list selection so it is bounded and index-friendly. Prefer a single candidate selection query followed by loading only the chosen deck's cards. Avoid calculating every card's cross-deck frequency for every profile request.
4. Consolidate redundant aggregate reads where safe. Do not change metric denominators or the visible profile fields.
5. Add focused regression coverage for a large commander cohort. The test must establish that query work remains bounded (for example, via SQL tracing/query-plan assertions or a test dataset where the former all-cohort deck-card scoring query cannot be used) and must preserve the selected representative-list and metric semantics for normal fixtures.
6. Keep edits scoped to the profile data path and its focused tests. Do not alter deployment, credentials, production data, or unrelated Research catalog behavior.

## Acceptance

- A commander profile opened from `/research/?tab=commanders` still renders the same visible metric and representative-list fields for existing fixtures.
- Commander pairs and commanders without recorded lists retain their current behavior.
- The selected recorded example remains deterministic.
- The heavy all-cohort card-frequency/overlap query is absent from the request path, or is bounded so its work cannot grow with the commander's full historical deck-card corpus.
- Focused tests pass.
