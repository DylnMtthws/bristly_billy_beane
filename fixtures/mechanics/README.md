# Mechanic tag fixtures

`tag_cards.json` holds the real oracle text of every card named as a fixture by
the shipped tag library. It is what makes a tag's precision claim a
**measurement** rather than an assertion: `TagDefinition.__post_init__` can only
check that a definition *claims* a figure above the 0.95 floor, and
`sabermetrics tags verify` recomputes that figure from these cards.

## Provenance, and why it says Scryfall

The snapshot's `source_view` is `scryfall:oracle_cards`, never
`mtg_v1.card_any_medium`. Production tag builds read `mtg_v1` as `mtg_consumer`
(ADR-020); this file exists so the test suite needs no database. Labelling it
with the production view would let a tag row built from it be mistaken for one
built from the real corpus, which is exactly the kind of silent substitution
`absence_is_visible` exists to prevent.

The card *text* is fetched, not typed. `manual_data_entry` is forbidden for the
card corpus and that rule is intact here: what is hand-made is the **label** —
which cards a tag should and should not match — and hand-labelling fixtures is
the plan's explicit instruction (§4/A1, "≥20 hand-labeled fixtures"). The text
those labels are checked against comes from a named snapshot.

## Regenerating

```sh
# after adding or renaming any fixture in a tag definition
python scripts/fetch_tag_corpus.py \
    --names-from-tags --out fixtures/mechanics/tag_cards.json
```

The script exits non-zero and names any fixture it could not resolve. It reads
the fixture names out of the shipped registry, so the file cannot drift from the
definitions without `tests/test_mechanic_tags.py` failing on an unresolved name.

For authoring — where you want every card, not just the labelled ones — build
the full development corpus instead. It is gitignored, ~17 MB, and is what
`scripts/tagdev.py` reads by default:

```sh
python scripts/fetch_tag_corpus.py --out .research-dev/corpus.jsonl
```
