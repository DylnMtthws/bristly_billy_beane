# Feedback batch DYL-37–49

Three explicitly owner-requested Cursor sessions implement the individually numbered specifications.

1. Deck workspace: DYL-37–41. Own builder template/JS, relevant zone/document/search backend and focused tests. Use a new scoped `feedback-workspace.css` imported by the existing stylesheet to minimize overlap.
2. Research controls: DYL-42,43,45,46,48. Own bound-range partial and research JS. Add scoped `feedback-controls.css`. In research_fragment.html change only filter-heading classes/semantics and commander Build button styling if necessary; avoid Meta/Decks result markup and Meta dropdown.
3. Meta/public decks: DYL-44,47,49. Own Meta/Decks template sections, associated query/cache/avatar plumbing and tests. Add scoped `feedback-meta-decks.css`.

All sessions have source only. No secrets, production data, external source fetching, infrastructure edits, commits or deployment. Coordinator merges shared stylesheet imports and template/backend hunks carefully. Preserve prior commander-profile speed optimizations and disabled automation.
