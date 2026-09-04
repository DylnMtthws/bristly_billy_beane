# Integration handoff

What `bristly_billy_beane` (the cEDH Deck Lab) needs from the other two
repositories, stated as contracts rather than as requests. Each section says
what exists today, what is required, and what this repository does in the
meantime so that nothing here is blocked on it.

Nothing in this document asks either repository to import our code, and nothing
here imports theirs. The boundaries are a Postgres schema and two JSON
documents.

**Status at time of writing (2026-09-03).** The card half of the `mtg_v1`
contract is live and we read it. The tournament half does not exist. The
simulator's JSON contract does not exist. Both absences are visible in the
product — as "no tournament evidence" and "not simulated" — rather than papered
over, and the cEDH slice ships and runs without either.

---

## 1. What this repository owns

| Owned here | Not owned here |
|---|---|
| Product workflow and UI | Scryfall / tournament ingestion |
| User intent and constraints | Physical ingestion tables |
| cEDH commander and strategy selection | Simulation mechanics |
| Evidence retrieval and ranking | Simulator strategy definitions |
| Deterministic candidate construction | Card facts, prices, legality |
| Model-provider integration | |
| Simulator orchestration | |
| Explanations and presentation | |

---

## 2. Required from `ingestion_pipeline_mtg`

### 2.1 The tournament half of `mtg_v1` — **required, does not exist**

`mtg_v1` currently publishes `card`, `card_any_medium`, `card_non_gameplay`,
`card_face` and `card_legality`. Stage 3 (decklist import) is paused, so there
is no tournament data in the contract at all.

`MetaRepository` is written against the four views below.
`PostgresMetaRepository.availability()` probes `information_schema.views` and
reports which are missing; until they exist, every read raises
`RepositoryUnavailable` and the UI states that no tournament evidence is
available. It never returns an empty list, because an empty list reads as "no
decks ran this card" and means "we have no data".

Column names below are what the adapter selects. If different names are more
natural on your side, say so and the adapter changes — the shape is what
matters.

```sql
-- One row per tournament.
CREATE VIEW mtg_v1.tournament AS SELECT
    event_id     text,   -- stable id, unique
    name         text,
    held_on      date,   -- required: the metagame window filters on it
    size         int,    -- player count; the min-event-size filter needs it
    source       text,   -- e.g. 'TopDeck.gg'
    source_url   text    -- so a claim can be traced to the event
;

-- One row per deck's finish at one event.
CREATE VIEW mtg_v1.tournament_entry AS SELECT
    entry_id     text,
    event_id     text,   -- -> mtg_v1.tournament.event_id
    identity_key text,   -- -> mtg_v1.commander_identity.identity_key
    standing     int,    -- nullable
    wins         int,
    losses       int,
    draws        int,
    decklist_id  text    -- nullable
;

-- One row per commander identity, aggregated over all events.
CREATE VIEW mtg_v1.commander_identity AS SELECT
    identity_key text,   -- see 2.2 below
    oracle_ids   uuid[], -- 1 or 2 entries; partners are one identity
    names        text[],
    entries      int,
    events       int,
    wins         int,
    top_cuts     int
;

-- Per-card inclusion, pre-aggregated per (identity, window, event-size floor).
CREATE VIEW mtg_v1.commander_card_inclusion AS SELECT
    identity_key    text,
    oracle_id       uuid,
    card_name       text,
    decks_including int,
    decks           int,  -- REQUIRED and > 0: the denominator
    since           date, -- window start this row was computed over
    min_event_size  int   -- event-size floor this row was computed over
;
```

**`decks` is not optional.** An inclusion rate without its sample size is not
usable as evidence, and `InclusionFact` will not construct without it. We
display the rate and the denominator together, always.

If pre-aggregating inclusion is the wrong shape for your pipeline, the
alternative we can work with is raw `mtg_v1.deck` + `mtg_v1.deck_card` views and
we aggregate here. Say which you prefer; we would rather not have both.

### 2.2 `identity_key` — a definition we need to agree on

We use the sorted `oracle_id`s joined by `+`, so a partner pair addressed in
either order is one identity:

```python
identity_key = "+".join(sorted(oracle_ids))
```

If you compute it differently, publish yours and we will adopt it. What must
not happen is two definitions that agree on singletons and disagree on partners.

### 2.3 The `is_paper` defect — **we are working around it, on your advice**

`mtg_v1.card` silently drops 254 Reserved List cards because `games` describes
the representative printing rather than the card. Five of them are in the
Kinnan list alone; *Tropical Island*, *Mox Diamond* and *Lotus Petal* are cEDH
staples. Per your README, we join `mtg_v1.card_any_medium` everywhere and never
`mtg_v1.card`. This is asserted in our test suite
(`test_snapshot_names_the_wide_view`) so it cannot regress silently.

**Consumer preference, matching the simulator's:** option 2, derive paper
availability across all printings. Option 1 (rename to `rep_games`) is also fine
for us — we do not read `games` at all.

No action is required for us to ship. This is recorded so the count of
consumers affected is right.

### 2.4 `card_name_index` (your migration `0004`) — wanted, not blocking

We resolve pack card names by `name = ANY(...) OR split_part(name, ' // ', 1) =
ANY(...)`, which is the same workaround the simulator's exporter writes. A
name-index view would let both consumers delete it. Until then the duplication
is two copies of one four-line predicate, which is tolerable.

### 2.5 We do not read `rep_prices`, and would rather not have to

The cEDH engine has no notion of card price (ADR-025): cEDH is proxy-normal, so
price is not a performance signal. `_CARD_COLUMNS` does not select `rep_prices`
and `CardFacts` has no field for it.

Two consequences for you, both minor and both in your favour:

* Price freshness is **not** on our critical path. If price ingestion lags, or
  is dropped, or moves behind a flag, nothing in the cEDH path notices.
* `rep_prices` is `rep_`-scoped by name, which your own §3.2 note says is the
  right convention — it is the price of one arbitrary printing, not of the
  card. We would have had to work around that; not reading it is simpler.

The legacy casual generator still uses prices, but it reads them from its own
SQLite corpus, not from `mtg_v1`.

### 2.6 Grants

Nothing to do: `ALTER DEFAULT PRIVILEGES IN SCHEMA mtg_v1 GRANT SELECT ON
TABLES TO mtg_consumer` already covers views added later. We connect as
`mtg_consumer` and never name `mtg_internal` — enforced here by
`assert_v1_only()`, which runs before every statement, and asserted by a test
that the adapter has exactly one execution path.

---

## 3. Required from `commander_simulator`

### 3.1 A JSON interface — **required, does not exist**

Today the binary reads a hand-authored `data/kinnan.deck.toml` plus
`data/cards.json` and prints a human report. A report is not a contract, and
`SubprocessSimulatorClient` deliberately does not parse one: non-JSON on stdout
becomes `not_simulated(contract_violation)`.

What we need:

```
cs --candidate <path-to-cedh-deck-candidate.v1.json> \
   --games <n> --turn <n> --format json
```

* reads the candidate document described in §3.3,
* writes **one** `cedh-simulation-result.v1` document to **stdout**,
* writes everything else (progress, the honesty header, warnings) to **stderr**,
* exits non-zero on any failure, with the reason on stderr.

### 3.2 `cedh-simulation-result.v1`

```jsonc
{
  "schema": "cedh-simulation-result.v1",
  "candidate_id": "…",            // echoed from the candidate
  "deck_sha256": "…",             // MUST equal the candidate's deck_sha256
  "simulator_version": "…",
  "games": 20000,
  "objective_turn": 3,

  // These three are REQUIRED. We will not render a probability without them.
  "metric": "goldfish_turns_to_assembly",
  "measures": "turns until a declared pattern is assembled, playing alone …",
  "does_not_measure": "deck quality, matchups, or whether the deck wins …",

  "assembly": [
    {"turn": 3, "probability": 0.0141, "ci_low": 0.0132, "ci_high": 0.0150}
  ],
  "censored_fraction": 0.2318,

  "modeled_cards": 68,
  "inert_cards": [
    {"oracle_id": "…", "name": "…", "reason": "…", "category": "interaction"}
  ],
  "known_misclassifications": ["Hullbreaker Horror is filed inert and …"],
  "unauthored_cards": ["Sylvan Library", "…"]
}
```

`metric`, `measures` and `does_not_measure` are required fields, not optional
metadata. Your run report prints the honesty header before any figure; making
those fields mandatory is how that survives the trip into a web page. Our
template renders them above the numbers and cannot render the numbers without
them.

`deck_sha256` is how we know a stored result describes the list in front of the
user. Ours is `sha256` over `C:<oracle_id>\n` for each sorted commander id then
`<oracle_id>:<quantity>\n` for each card sorted by oracle_id. Adopt it, or
publish yours and we will compute both.

`known_misclassifications` exists because of *Hullbreaker Horror*. Keep sending
it; we display it under the inert table, as you do.

### 3.3 `cedh-deck-candidate.v1` — what we send

Produced today. `fixtures/cedh/` has a real example, and
`sabermetrics cedh build --pack kinnan_basalt --out cand.json` writes one.

```jsonc
{
  "schema": "cedh-deck-candidate.v1",
  "candidate_id": "cand-…",
  "generated_at": "2026-09-03T…Z",
  "deck_sha256": "…",
  "commander": {
    "oracle_ids": ["…"],           // 1 or 2
    "names": ["Kinnan, Bonder Prodigy"],
    "color_identity": ["G", "U"]
  },
  "cards": [                        // sums to exactly 99
    {"oracle_id": "…", "name": "Sol Ring", "role": "acceleration",
     "quantity": 1, "source": "auto_include"}
  ],
  "win_packages": [ … ],
  "role_counts": { … },
  "provenance": { … },
  "notes": [ … ]
}
```

**Cards are addressed by `oracle_id`.** Names are for display. A name-keyed
handoff loses multi-faced cards at the far end, which is the failure your
exporter already works around.

### 3.4 The hard one: cards you have not authored

**This is the item most likely to be underestimated, so it is stated plainly.**

Your card model is hand-authored for one list, and 4 of its 100 cards are
already unauthored. A generator that varies the 99 will hand you cards with no
authored effect — not occasionally, but on the first build that differs from
list A.

We are not asking you to author the format. We are asking the contract to
define what happens, and we can work with either answer:

1. **Preferred.** Accept any candidate. Treat unauthored cards as inert with
   `category: "unauthored"`, run anyway, and report them in
   `unauthored_cards` and `modeled_cards`. We already surface both, so the user
   sees "this figure was computed with 22 of the 99 invisible to the model"
   rather than a number that looks complete.
2. **Acceptable.** Reject a candidate containing unauthored cards, exit
   non-zero, and name them on stderr. We render `not_simulated` with the list.
   This makes simulation available only for lists close to your authored one,
   which is a real limitation but an honest one.

What does not work is silently substituting, dropping or approximating an
unauthored card. That produces a number whose meaning nobody can state.

### 3.5 Supported commanders

A way to ask which commanders have a model:

```
cs --list-commanders --format json
→ {"commander_keys": ["<oracle_id>", …]}
```

We call `supported_commander_keys()` before offering a build, and show anything
outside the set as unsupported. Kinnan is the only one today and we do not
claim otherwise anywhere in the UI.

### 3.6 The `source_url` gap

Your `[provenance]` block records `source_url = ""` for the list A snapshot. Our
Kinnan pack carries the same gap in its `source` field, and neither of us
should reconstruct one. If the Moxfield URL turns up, both files want it.

---

## 4. Duplicate ingestion responsibilities: deprecation plan

`src/sabermetrics/ingestion/` predates the ingestion pipeline and duplicates
work that repository now owns. It is **not** deleted, because the legacy casual
generator still runs on it and the SQLite corpus it fills is what the existing
portal serves.

Current state, by module:

| Module | Duplicates | Status |
|---|---|---|
| `ingestion/scryfall.py` | `mtg_v1.card`, `card_face`, `card_legality` | **Superseded.** Legacy path only. |
| `ingestion/mtgapi.py` | rulings | Superseded once `mtg_v1` publishes rulings. |
| `ingestion/topdeck.py` | `mtg_v1.tournament*` (§2.1) | **Superseded on delivery of §2.1.** |
| `ingestion/moxfield.py`, `archidekt.py`, `deckstats.py` | decklist import | Superseded by your Stage 3. |
| `ingestion/edhrec.py` | inclusion rates | Replaced by tournament inclusion (§2.1). Not an equivalent source; EDHREC is casual play, which is the wrong population for cEDH. |
| `ingestion/spellbook.py` | combos | No `mtg_v1` equivalent requested. Legacy only. |
| `ingestion/reddit.py` | community signal | **Not carried forward.** The cEDH path takes strategy material from `config/cedh_evidence/`, curated and reviewed, rather than scraped at generation time. |
| `ingestion/reference.py` | rules corpus | Legacy only. The cEDH path grounds on curated strategy material, not the comprehensive rules. |
| `ingestion/game_knights.py` | knowledge base | Legacy only; unrelated to cEDH. |

The rule that holds today and is enforced by a test
(`test_the_cedh_path_does_not_depend_on_legacy_ingestion`): **no module under
`src/sabermetrics/cedh/` imports `sabermetrics.ingestion`,
`sabermetrics.pipeline`, `sabermetrics.reasoning` or `sabermetrics.analytics`.**
The two paths share the database file, the auth system and the cost ledger, and
nothing else.

Removal sequence, once §2.1 lands and the casual generator is retired:

1. Point the legacy `cards` table refresh at `mtg_v1` (adapter already exists).
2. Delete `ingestion/topdeck.py` and the decklist-source modules.
3. Delete `ingestion/scryfall.py` and `mtgapi.py`.
4. Drop the corresponding launchd jobs in `launchd/`.

Steps 2–4 are not scheduled. Doing them before the casual generator is retired
would break a shipped product to tidy a directory.

---

## 5. How to run this side without either repository

```bash
pip install -e '.[dev]'
sabermetrics cedh packs                        # what is supported, and in which mode
sabermetrics cedh build --pack kinnan_basalt --out candidate.json
pytest                                          # no network, no Postgres, no binary
```

Fixtures live in `fixtures/cedh/` and are regenerated by
`python scripts/build_cedh_fixtures.py`. The card corpus there is **synthetic**
and says so in its own header; the 99 card names and the pack are real.

To point at the live contract:

```bash
export MTG_V1_DSN='postgresql://mtg_consumer:…@localhost:5432/mtg'
export HF_TOKEN='hf_…'                          # optional; no model = no prose
pytest -m postgres                              # opt-in smoke tests
pytest -m model
pytest -m simulator
```
