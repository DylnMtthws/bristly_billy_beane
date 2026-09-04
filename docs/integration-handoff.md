# Integration handoff

What `bristly_billy_beane` (the cEDH Deck Lab) needs from the other two
repositories, stated as contracts rather than as requests. Each section says
what exists today, what is required, and what this repository does in the
meantime so that nothing here is blocked on it.

Nothing in this document asks either repository to import our code, and nothing
here imports theirs. The boundaries are a Postgres schema and two JSON
documents.

**Status at time of writing (2026-09-04).** The atomic card, deck, and tournament
facts in `mtg_v1` are live and Deck Lab reads them. The simulator remains a
separate versioned JSON boundary; any unavailable run is visible as "not
simulated" rather than being papered over.

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

### 2.1 Canonical tournament and deck facts — **published**

The ingest service publishes atomic facts. It does not publish, and Deck Lab
does not require, consumer-specific commander or inclusion aggregates.

| View | Columns Deck Lab requires |
|---|---|
| `mtg_v1.tournament` | `tournament_id`, `source`, `name`, `event_date`, `url`, `player_count`, `top_cut` |
| `mtg_v1.tournament_entry` | `entry_id`, `tournament_id`, `deck_id`, `standing`, `wins`, `losses`, `draws` |
| `mtg_v1.deck` | `deck_id`, `commander_identity`, `is_complete` |
| `mtg_v1.deck_commander` | `deck_id`, `oracle_id`, `submitted_name`, `position` |
| `mtg_v1.deck_card` | `deck_id`, `oracle_id`, `board` |
| `mtg_v1.card_any_medium` | `oracle_id`, `name` (plus the card facts used elsewhere) |

`PostgresMetaRepository` maps producer names to its internal read model:
`tournament_id` → `event_id`, `event_date` → `held_on`, `player_count` →
`size`, `url` → `source_url`, `deck_id` → `decklist_id`, and
`deck.commander_identity` → `identity_key`.

Deck Lab owns aggregation. Its exact cohort joins `tournament` →
`tournament_entry` → `deck`, requires a submitted non-null `deck_id`, and
filters `deck.commander_identity` to the requested identity,
`tournament.event_date >= since`, and
`tournament.player_count >= min_event_size`. Commander Oracle IDs and submitted
names come from `deck_commander`; a partner pair therefore remains one deck
identity with two commander rows.

From that same cohort:

- entries are distinct qualifying `entry_id` values;
- events are distinct qualifying `tournament_id` values;
- event wins are entries with `standing = 1` (not the sum of match `wins`);
- top cuts are entries with a known `top_cut` and `standing <= top_cut`;
- the inclusion denominator is the exact count of distinct submitted deck IDs;
- each numerator is the count of distinct cohort deck IDs containing a resolved,
  non-null mainboard `oracle_id`; names resolve through `card_any_medium`.

`deck.is_complete = false` does not remove a deck from the presence-based
inclusion denominator. EDHTop16 commonly marks a list incomplete when duplicate
basic lands were collapsed; removing those decks would bias the corpus by color
identity. Evidence reports both the denominator and incomplete-deck count.

The repository loads a whole evidence slice on one Postgres connection in a
read-only, repeatable-read transaction. Display limits apply only after exact
counts have been computed. Provenance identifies the canonical views and calls
the result a Deck Lab cohort aggregate.

### 2.2 Capability and absence behavior

Availability probes execute zero-row selects against every required column, so
they detect missing views, missing columns, and missing `mtg_consumer` SELECT
permission. Tournament summaries (`tournament`, `tournament_entry`, `deck`,
`deck_commander`) and card inclusion (those four plus `deck_card` and
`card_any_medium`) are separate capabilities.

- Missing tournament facts render as "no tournament evidence."
- Missing inclusion facts preserve tournament finishes and explicitly mark card
  inclusion unavailable.
- A successful zero-row cohort remains an available empty result; unavailable
  data is represented separately and is never turned into an empty list or a
  neutral value.

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

## 3. `commander_simulator` — **delivered, and the contract is now two-way**

**Status 2026-09-04.** The simulator publishes versioned JSON contracts and we
consume them. Everything below describes the contract as it stands; the
requests in earlier revisions of this section have been met.

### 3.1 What we vendor, and why both directions

`fixtures/cedh/contracts/` holds four files copied verbatim from
`commander_simulator/contracts/`:

| File | Direction | Pins |
|---|---|---|
| `cedh-simulation-request.v1.schema.json` | we send | the `POST /simulate` envelope |
| `cedh-deck-candidate.v2.schema.json` | we send | the deck document inside it |
| `cedh-simulation-result.v3.schema.json` | we receive | the result |
| `hash-golden-vectors.json` | both | `deck_sha256` and `simulation_input_sha256` |

An earlier revision vendored the **response** schema only. The request body was
described in prose here and read differently there; each suite tested its own
shape against itself and both stayed green; every real deck build failed on the
wire with `422 unsupported candidate schema_version`, which this repository
surfaced to users as "commander unsupported". A contract pinned in one
direction is not pinned. `tests/test_cedh_hash_contract.py` now validates the
documents this repository actually builds against the schemas the simulator
actually publishes.

`sabermetrics.cedh.wire` is the only place a simulator request is constructed.

### 3.2 The two hashes

Agreeing on a field's *shape* is not agreeing on its *meaning*, and that was
the second-order defect behind the first. v1 had one field, `candidate_hash`,
that this repository read as "the deck" and the simulator computed as "the deck
plus the strategy pack". Both readings were defensible. Neither could be
correct at the same time as the other.

| | `deck_sha256` | `simulation_input_sha256` |
|---|---|---|
| Answers | "is this the same deck?" | "is this the same measurement?" |
| Covers | commander oracle IDs + library oracle IDs and quantities | the deck hash, the resolved pack's id/version/content hash, simulator and card-data versions, scenario, seed, games, turn, sweep, ablations |
| Computed by | us, then **independently recomputed** by the simulator | the simulator only |
| We use it for | deck identity | cache key / reproducibility |

`deck_sha256` is ours, unchanged, and it is ADR-025 made checkable: because the
engine has no price and no collection input, a build is reproducible from the
pack alone, so a deck hash identifies a **list** and not a list-plus-requester.
Adding the strategy pack to it would have broken exactly that property.

```python
# The preimage, unchanged from what this repository has always computed.
sha256(
    b"".join(f"C:{oid}\n".encode() for oid in sorted(commander.oracle_ids))
    + b"".join(f"{c.oracle_id}:{c.quantity}\n".encode()
               for c in sorted(cards, key=lambda c: c.oracle_id))
)
```

On the wire it is `sha256:<hex>`; `DeckCandidate.deck_sha256` remains bare hex
and `DeckCandidate.deck_sha256_wire` adds the prefix.

**We compare `result.candidate.deck_sha256`, and only that, for deck
integrity.** It is the simulator's own recomputation from the list it ran, not
an echo of what we sent. `simulation_input_sha256` is carried on
`SimulationResult` and is the correct key for caching a measurement — which
makes `deck_sha256` the wrong one, since the same deck under a different pack
or seed is a different measurement.

`hash-golden-vectors.json` makes all of this executable across three
implementations (our Python, the simulator's Python, the simulator's C++): that
ordering does not affect `deck_sha256`; that changing a commander, card or
quantity does; that changing the strategy pack does not; and that changing the
strategy pack does change `simulation_input_sha256`.

### 3.3 Which simulator strategy pack we ask for

The two pack namespaces are unrelated: our `kinnan_basalt` is the simulator's
`kinnan-midrange-goldfish@1.0.0`. The mapping is **declared** in the authored
pack YAML (`simulator_pack_id` / `simulator_pack_version`), never derived from
the name. A pack without a declared mapping defaults to `derived-generic@1.0.0`,
which is the simulator's reserved id for explicit derived execution — so a pack
nobody has deliberately mapped is never run under commander-specific logic.

The pack is a request for an execution context. It travels beside the deck
hash and is not part of it.

### 3.4 Error taxonomy — what may and may not be called a deck mismatch

| Service code | Our `NotSimulated.reason` | Means |
|---|---|---|
| `deck_hash_mismatch` | `deck_mismatch` | the simulator ran a different list than we submitted |
| `contract_violation` | `contract_violation` | we and the simulator disagree about the contract |
| `unsupported` | `unsupported` | the pack/commander/snapshot cannot run here |

Only the first is a deck mismatch. An unsupported pack or a stale card export
is a fault in the simulator's execution context and says nothing about the
user's list; telling somebody their deck changed when it did not is both wrong
and something they cannot act on, and it spends the credibility of the message
for the case where it *is* true.

### 3.5 Cards we have not authored

Resolved as option 1, as preferred. The simulator accepts any candidate, runs
it, and reports coverage: `coverage.modeled_cards`, `coverage.inert_cards`,
`coverage.unauthored_cards`, `inert_by_reason`, and the specific card names in
`warnings`. We surface all of it, so the user sees "this figure was computed
with N of the 99 invisible to the model" rather than a number that looks
complete.

Note the shape: the result reports inert and unauthored cards as **counts plus
warning strings**, not as a per-card structured list. `SimulationResult.
inert_cards` therefore stays empty on the live path and `inert_card_count` /
`unauthored_card_count` carry the figures. If a structured per-card list is
wanted for display, that is a new field and a new result version, not something
to reconstruct by parsing warning text.

### 3.6 Supported commanders

The HTTP contract discovers support by attempting the request:
`HttpSimulatorClient.supported_commander_keys()` returns the empty set and an
unsupported candidate comes back as `422 unsupported`. `GET /healthz` lists the
installed packs (`strategy_packs: ["kinnan-midrange-goldfish@1.0.0", ...]`),
which is what the UI uses to say what is supported without guessing.

### 3.7 The `source_url` gap

Unchanged and still open. The simulator's `[provenance]` block records
`source_url = ""` for the list A snapshot; our Kinnan pack carries the same gap
in its `source` field. Neither of us should reconstruct one. If the Moxfield URL
turns up, both files want it.

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
