# DYL-38: Playmat grid layout

Group: Deck workspace. Baseline: production `1aa592f9bf396faa63e39db7e00dfb12a366d0cb`.

## Acceptance criteria

Add a Grid icon button to the left of the existing fan/stack control on each zone. Grid automatically uses approximately square columns/rows based on card count, expands zone height and displays every card without horizontal scrolling. Support empty, one-card and large zones. Preserve card order and all move/add/remove behavior. Persist layout through the existing command and document schema, accepting grid alongside spread/fan; older documents remain valid. Existing layout control exits grid sensibly. Verify save/reload and shared rendering.

## Verification and delivery

Add focused behavioral regression coverage where logic changes. Coordinator independently reviews integration, runs canonical checks, and verifies the deployed QA behavior. Keep existing owner data and permissions intact. QA only; owner review is required before production.
