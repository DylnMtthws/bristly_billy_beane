# D1 — corpus depth census

_Authored 2026-09-08 for the Research Assistant (`RESEARCH_ASSISTANT_SPEC.md`
§14.1). Work order: **D1** in [`upstream-dependencies.md`](upstream-dependencies.md)._

**Status: specified and executable; not yet run.** Everything the repository can
supply is below. The numbers require a live `mtg_consumer` DSN, which this
worktree does not have and should not have — see §5.

Run it with:

```sh
MTG_V1_DSN='postgresql://…?sslmode=require' python scripts/d1_census.py --json docs/d1-census-<date>.json
```

Commit the JSON beside this file. Re-running it later shows corpus drift for
free, which matters because the corpus is currently a **one-time manual import
that does not refresh** (§4).

---

## 1. What the census is for

R5 computes field statistics — inclusion rates with denominators. Every such
statistic needs a floor below which it reports a **stated absence** instead of a
number, per `popularity_is_not_quality` and `absence_is_visible`.

That floor is currently a guess. The census replaces it with a measurement.

**The census is not a go/no-go gate.** R5 ships whatever the numbers say; what
changes is how often it says "not enough recorded lists." If only eight commander
identities clear 30 decks, that is the finding, and R5 states absences for
everything else. Thresholds therefore ship as **configuration**, so the census
can set them without a code change.

## 2. Scope

**In scope** — six measures, one per thing R5 or R6 has to decide:

| Measure | What consumes it |
|---|---|
| Tournaments: count, distinct sources, date range, player-count distribution | Sets the **default evidence window** and the **event-size floor**. Archetype clustering needs event diversity, not just deck volume |
| `tournament_entry` rows, and how many have a non-null `deck_id` | The **inclusion denominator**. A standing with no list behind it can never contribute to a rate |
| Commander identities at ≥10, ≥30, ≥100 decks | The **absence threshold**. Below the floor, R5 reports absence rather than a percentage |
| Identities at ≥30 decks **and** ≥5 distinct events | A stricter floor. Thirty decks from one event is one metagame snapshot, not a trend |
| `deck.is_complete` by commander colour count | Quantifies the **D6 basic-land bias for this corpus** rather than in general |
| `card_any_medium` and `card_legality` row counts | Re-checks the verifier floors; cheap drift detection |

**Explicitly out of scope**, to keep this a census rather than a research project:

- Any write, any DDL, any `mtg_internal` access. Read-only, `mtg_v1` only.
- Card-level inclusion rates. That is R5's job, and computing them here would
  duplicate the thing the census exists to configure.
- Judging whether the corpus is "good enough." It reports; the thresholds follow.

## 3. Counting method, and why each choice changes the answer

**One snapshot.** The whole census runs inside a single
`REPEATABLE READ READ ONLY` transaction. Counting tournaments and decks in
separate transactions during a nightly import yields a ratio that never existed
at any instant.

**Group by `deck.commander_identity`, never by name.** It is a deterministic key
built from the commander `oracle_id`s **sorted** and joined with `+`
(`ingestion_pipeline_mtg/src/mtg_ingest/decks/resolve.py:175`). Sorted means a
partner pair recorded in either order is **one** identity — grouping by name
would split Thrasios/Tymna from Tymna/Thrasios into two populations that each
look half as popular as the real one.

**Unresolved commanders are counted separately, never merged.** An unresolved
commander contributes `unresolved:<normalised name>` to the identity rather than
being dropped. Those identities are excluded from every headline figure and
reported on their own line. Folding them in would inflate the identity count with
*names* rather than commanders; dropping them silently would hide a resolution
problem that is worth seeing.

**Decks are counted `DISTINCT` and joined through `tournament_entry`.** A deck
with no entry is not tournament evidence. `count(DISTINCT deck_id)` rather than
`count(*)` because `deck_commander` has one row per commander card, so a partner
pair would otherwise double every count that touches it.

**Colour count comes from the commander card(s), unnested and deduplicated**, so
a partner pair contributes the union of its colours rather than one row each.

## 4. What the repository already records

These are on record and did **not** require running the census. They are context,
not results, and each carries the caveat that makes it context.

| Figure | Value | Source | Caveat |
|---|---|---|---|
| `card` rows | 33,572 | `deployment-status-2026-09-05.md` via D1 | Not the view we read |
| `card_any_medium` rows | 34,570 | same | The view we do read. The 998-row gap is the Reserved List problem ADR-020 exists for |
| `card_printing` rows | 117,627 | same | |
| `card_legality` rows | 888,559 | same | |
| Tournaments imported | 200, EDHTop16, one manual run of 22m32s | same | **Not a recurring nightly** — see below |
| Kinnan deck cohort | n = 635 distinct decks, 42 incomplete | 2026-09-05 production acceptance | One commander. Says nothing about the tail, which is exactly what the census measures |

**The corpus does not refresh.** `NIGHTLY_ENABLED` is unset, `HEALTHCHECK_URL` is
pending, Healthchecks is not configured, and the monitored nightly has never run.
So the corpus is a one-time import that will silently age. That is a separate
half of the D1 work order and it needs **explicit owner approval** before
enabling — it is not something to switch on while running a census.

## 5. Uncertainty — read before using any number this produces

Stated plainly, because a census whose limits are unrecorded becomes a fact.

1. **No numbers exist yet.** This worktree has no `MTG_V1_DSN`, no `psql` and no
   `flyctl`. That is correct — a feature branch should not hold production
   database credentials — but it means §4 is the only measured data here, and the
   six measures in §2 are unmeasured.
2. **A census of a fixture measures the fixture.** The script has no fixture mode
   on purpose and refuses to run without a DSN (exit 2), rather than producing
   plausible numbers from `fixtures/cedh/`.
3. **`color_identity` is assumed to be a Postgres array.** The evidence is the
   `card_color_identity_gin` index in the pipeline's schema-shape test, and GIN
   indexes serve array/jsonb/tsvector columns. If it is instead a text column,
   `completeness_by_colour_count` raises and the fix is one line —
   `unnest(string_to_array(c.color_identity, ''))`. **The other seven queries do
   not depend on this.**
4. **`is_complete` is upstream's judgment, and EDHTop16 sends no card
   quantities.** The missing cards are deduplicated basics correlated with colour
   identity — mono-colour lists average 9.1 missing, five-colour 0.1. So the
   colour-count table measures *a known bias with a known direction*, not deck
   quality. This is why R5 excludes basics from every field-derived count and
   restricts land comparisons to `is_complete = true` with a stated denominator —
   behaviour that is correct **regardless of whether D6 ever lands**.
5. **200 tournaments from one source is a narrow base.** With a single `source`,
   the "distinct sources" measure will read 1, and any archetype clustering
   inherits EDHTop16's coverage biases entirely. The census will show this; it
   cannot fix it.
6. **A snapshot is not a trend.** These numbers describe one instant of a corpus
   that does not currently refresh. Re-run after the nightly is enabled before
   treating any threshold as stable.
7. **`mtg_v1.commander_identity` and `mtg_v1.commander_card_inclusion` are not
   published views** and are not part of the v1 contract; the consumer already
   probes for them and treats them as unavailable. The census therefore derives
   commander populations from `deck` + `deck_commander` + `tournament_entry`
   rather than from a convenience view that does not exist.

## 6. Handshake — what happens when the numbers land

1. Commit the JSON beside this file and fill §4 with a **Measured** table.
2. Set R5's thresholds in `config/` from the ≥10 / ≥30 / ≥100 columns. If the
   ≥30 population is very small, R5's default window widens rather than its floor
   dropping — a longer window is honest; a lower floor is not.
3. Record the event-size floor from the player-count distribution.
4. Feed the colour-count completeness table into R5's mana-base exclusions.
5. Re-run after the recurring nightly is enabled, and diff.
