# QA review: DYL-37–49

## Deck workspace — DYL-37–41

Open a deck from https://qa.decklab.studio/build. The card-search sidebar is replaced with a toolbar autocomplete shared by Decklist and Playmat. Try a name without punctuation, use the arrow keys and Enter to add it, and use Escape to dismiss results. The dropdown shows at most eight matches.

Create a zone beside the sidebar Zones heading. In Playmat, use its Grid button to arrange cards, drag an older zone over a newer one, and reload to check layout and front-to-back order. In Decklist, the zone select-all checkbox aligns with the individual card checkboxes.

## Research controls — DYL-42,43,45,46,48

Visit https://qa.decklab.studio/research/. Tabs, numeric ranges, Apply and commander Build buttons use a quieter rose accent. Filter headings are clearer. Numeric slider selections appear as small labels along the scale, and the tab selection slides immediately without delaying the request. Reduced-motion preferences are respected. Failed tab requests leave the selection aligned with the displayed results and offer retry.

## Meta and public decks — DYL-44,47,49

Meta uses Include, Exclude and Exactly. The Field view tile is removed. Narrowing the screen hides Trend, then Top 16 rate, then Meta Share; color identities stay on one line. Research Decks uses smaller wrapping commander names and places the author's avatar beside the display name.

## Validation and release boundary

Cursor implemented three scoped groups; the coordinator reviewed and integrated them. Independent Python 3.11 validation passed 1,483 tests (31 optional skips), Ruff, Black and mypy. Chromium and WebKit local checks covered builder alignment, autocomplete rendering, grid persistence and responsive Research layouts. Deployed QA receipts and live acceptance results are maintained in the infrastructure repository.

This candidate is for QA review. Production promotion requires the owner's explicit approval of the QA candidate.
