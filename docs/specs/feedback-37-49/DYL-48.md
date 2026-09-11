# DYL-48: Research tab slide transition

Group: Research controls. Baseline: production `1aa592f9bf396faa63e39db7e00dfb12a366d0cb`.

## Acceptance criteria

Add a short directional sliding indicator/transition when switching Cards/Commanders/Meta/Decks, visibly acknowledging selection immediately during existing async load. Preserve existing progressive fetch/cache/history/race handling, failure recovery and focus. Do not add artificial loading delay or animate filter changes as tab switches. Respect prefers-reduced-motion with immediate/static feedback. Rapid switching/back-forward must leave correct selected tab/content and no lingering busy/animation state.

## Verification and delivery

Add focused behavioral regression coverage where logic changes. Coordinator independently reviews integration, runs canonical checks, and verifies the deployed QA behavior. Keep existing owner data and permissions intact. QA only; owner review is required before production.
