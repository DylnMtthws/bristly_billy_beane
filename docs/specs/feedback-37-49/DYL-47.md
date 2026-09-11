# DYL-47: Compact public deck commanders and author avatars

Group: Meta and public decks. Baseline: production `1aa592f9bf396faa63e39db7e00dfb12a366d0cb`.

## Acceptance criteria

In Research Decks results reduce commander-name typography and allow sensible wrapping, including partner pairs, without losing navigation or overlapping metadata. Put the existing public profile avatar (or established fallback icon) immediately left of deck author display name. Reuse public-safe avatar routes/model; never expose private account fields or local upload paths. Support missing author/avatar and imported tournament decks. Avoid per-row database queries. Test public avatar privacy/fallback and narrow card layout.

## Verification and delivery

Add focused behavioral regression coverage where logic changes. Coordinator independently reviews integration, runs canonical checks, and verifies the deployed QA behavior. Keep existing owner data and permissions intact. QA only; owner review is required before production.
