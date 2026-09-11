# DYL-37: Zone creation beside sidebar header

Group: Deck workspace. Baseline: production `1aa592f9bf396faa63e39db7e00dfb12a366d0cb`.

## Acceptance criteria

Add an accessible Add zone button immediately beside the right sidebar Zones heading. Reuse the existing create-zone command/name validation and persistence. New zone appears in list and playmat without reload; no duplicate creation on one click. Hide editing controls for shared/read-only decks.

## Verification and delivery

Add focused behavioral regression coverage where logic changes. Coordinator independently reviews integration, runs canonical checks, and verifies the deployed QA behavior. Keep existing owner data and permissions intact. QA only; owner review is required before production.
