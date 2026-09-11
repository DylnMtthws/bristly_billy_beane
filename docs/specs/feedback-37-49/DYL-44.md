# DYL-44: Meta color matching parity

Group: Meta and public decks. Baseline: production `1aa592f9bf396faa63e39db7e00dfb12a366d0cb`.

## Acceptance criteria

Replace Meta color dropdown choices with Include, Exclude, Exactly, matching Cards/Commanders semantics: include contains all selected colors, exclude contains none of selected colors, exactly equals selected identity. Empty selection leaves filter unrestricted. Support old bookmarked all/any/exact modes without silently changing any behavior; canonical new UI uses include/exclude/exactly. Apply in database queries, pagination and async cache keys. Preserve fast default snapshot/cohort cache path; do not make an unfiltered Meta visit an expensive query. Test each semantic mode on mono/multicolor/colorless and default cache eligibility.

## Verification and delivery

Add focused behavioral regression coverage where logic changes. Coordinator independently reviews integration, runs canonical checks, and verifies the deployed QA behavior. Keep existing owner data and permissions intact. QA only; owner review is required before production.
