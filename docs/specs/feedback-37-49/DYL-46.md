# DYL-46: Slider values beside their positions

Group: Research controls. Baseline: production `1aa592f9bf396faa63e39db7e00dfb12a366d0cb`.

## Acceptance criteria

Remove standalone 0–10+ selection text above numeric sliders. Keep baseline 0,5,10+ ticks and add small muted live selected-value labels positioned along the scale near each nonbaseline thumb value (e.g. 1 between 0 and 5). Avoid duplicate overlapping labels at baseline endpoints or coincident thumbs; handle close thumbs clearly. Keep accessible exact values, minimum <= maximum, step=1, Clear behavior and 10+ unbounded semantics. Apply Cards and Commanders numeric filters consistently. Verify mouse, touch, keyboard, Clear, reload/query state and narrow layouts.

## Verification and delivery

Add focused behavioral regression coverage where logic changes. Coordinator independently reviews integration, runs canonical checks, and verifies the deployed QA behavior. Keep existing owner data and permissions intact. QA only; owner review is required before production.
