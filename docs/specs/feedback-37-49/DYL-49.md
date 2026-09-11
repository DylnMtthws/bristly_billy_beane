# DYL-49: Responsive Meta information density

Group: Meta and public decks. Baseline: production `1aa592f9bf396faa63e39db7e00dfb12a366d0cb`.

## Acceptance criteria

Remove Field view tile and explanatory prose. At progressively narrower widths hide columns in this priority: Trend first, Top 16 rate next, Meta Share last. Keep header/data column visibility synchronized. Color identity icons never wrap. Give commander names most room; wrap or ellipsize only when needed at tight widths without obscuring row actions/rank. Verify desktop/tablet/mobile, available/missing evidence and sort controls; hidden columns may remain sortable if useful but no broken alignment.

## Verification and delivery

Add focused behavioral regression coverage where logic changes. Coordinator independently reviews integration, runs canonical checks, and verifies the deployed QA behavior. Keep existing owner data and permissions intact. QA only; owner review is required before production.
