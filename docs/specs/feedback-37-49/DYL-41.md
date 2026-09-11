# DYL-41: Decklist selection alignment

Group: Deck workspace. Baseline: production `1aa592f9bf396faa63e39db7e00dfb12a366d0cb`.

## Acceptance criteria

Remove misplaced top-right select-all checkbox. In each zone heading place the zone select-all checkbox directly above and aligned with card-row checkboxes; shift zone name and collapse arrow right. Preserve checked/indeterminate state and existing bulk operations. Verify expanded/collapsed, empty and multiple zones and narrow screens.

## Verification and delivery

Add focused behavioral regression coverage where logic changes. Coordinator independently reviews integration, runs canonical checks, and verifies the deployed QA behavior. Keep existing owner data and permissions intact. QA only; owner review is required before production.
