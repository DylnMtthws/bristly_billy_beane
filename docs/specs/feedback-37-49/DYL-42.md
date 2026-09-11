# DYL-42: Quiet research control accents

Group: Research controls. Baseline: production `1aa592f9bf396faa63e39db7e00dfb12a366d0cb`.

## Acceptance criteria

Mute the reddish accent on research Cards/Commanders/Meta/Decks segmented selection, numeric slider tracks/thumbs, and options Apply controls. Use scoped theme tokens/selectors with restrained low-saturation fills/borders rather than globally changing destructive or central primary actions. Retain readable contrast, visible selected/hover/focus states and equal tab sizing across research views.

## Verification and delivery

Add focused behavioral regression coverage where logic changes. Coordinator independently reviews integration, runs canonical checks, and verifies the deployed QA behavior. Keep existing owner data and permissions intact. QA only; owner review is required before production.
