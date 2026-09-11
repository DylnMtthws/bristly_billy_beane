# DYL-39: Most recently dragged zone comes to front

Group: Deck workspace. Baseline: production `1aa592f9bf396faa63e39db7e00dfb12a366d0cb`.

## Acceptance criteria

On drag interaction raise the zone above all other zones immediately and keep it in front after drop/reload. Use presentation ordering separate from semantic sidebar/list zone order if necessary; do not reorder cards/zones semantically as a side effect. Preserve safe command validation and concurrent revision handling. Test an early-created zone dragged over a newer zone, repeated alternation, and save/reload.

## Verification and delivery

Add focused behavioral regression coverage where logic changes. Coordinator independently reviews integration, runs canonical checks, and verifies the deployed QA behavior. Keep existing owner data and permissions intact. QA only; owner review is required before production.
