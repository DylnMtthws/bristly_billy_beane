# DYL-40: Shared builder toolbar and compact card autocomplete

Group: Deck workspace. Baseline: production `1aa592f9bf396faa63e39db7e00dfb12a366d0cb`.

## Acceptance criteria

Remove the left card-search sidebar and its reserved footprint/toggle for now; do not implement the future AI assistant. Keep a shared subheader toolbar in decklist and playmat, adapting view-specific controls. Put card search there with a dropdown of at most 8 ranked card-name results. Matching ignores punctuation (commas, apostrophes, hyphens etc.) consistently in backend search, while retaining existing legality/color rules. Choosing a result adds through existing destination/command behavior; zone plus buttons focus this search and select that zone. Implement keyboard up/down/Enter/Escape, accessible combobox/listbox state, dismiss on outside click, debounce and stale-response protection. Empty query clears results; show concise empty/error state. Preserve shared read-only behavior, global research search and other builder toolbar actions. Test punctuation variants, limit/ranking, stale responses where practical, and both views.

## Verification and delivery

Add focused behavioral regression coverage where logic changes. Coordinator independently reviews integration, runs canonical checks, and verifies the deployed QA behavior. Keep existing owner data and permissions intact. QA only; owner review is required before production.
