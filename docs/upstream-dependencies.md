# Upstream dependencies for the Research Assistant

_Authored 2026-09-06 from the state of all four repositories. Each item is a
self-contained work order: a separate session can pick one up with no context
from the conversation that produced it._

Written in the style of `docs/integration-handoff.md`: each item says what
exists today, what is required, what this branch does in the meantime, and
exactly how integration happens the moment it lands. **Nothing here blocks the
start of Track A.** Phases A0, A1, A2 and A4 can be built in full against what
exists today.

---

## 0. Summary

| # | Repo | Item | Blocks | Priority |
|---|---|---|---|---|
| **D0** | deployment | **No model credential in production** (`HF_TOKEN` unset) | every LLM path, A5 onward | **P0** |
| **D1** | `ingestion_pipeline_mtg` | Corpus depth census + recurring nightly | shapes A3 thresholds | P1 |
| **D2** | `bristly_billy_beane` | Resolve the two-corpus fork | A2, A3 integration | **P1** |
| **D3** | `commander_simulator` | Structured per-card inert/unauthored list (`result.v4`) | A6 narration honesty | **P1** |
| **D4** | `ingestion_pipeline_mtg` | Combo corpus (Commander Spellbook) | A4 `combo_lookup`, A8 candidates | **P1** |
| **D5** | `ingestion_pipeline_mtg` | Card rulings in `mtg_v1` | A4 `rules_lookup` completeness | P2 |
| **D6** | `ingestion_pipeline_mtg` | Stage 4 Moxfield adapter — real quantities | A3/A8 mana-base analysis | P2 |
| **D7** | `commander_simulator` | Pack discovery via `/healthz` at scale | A4 `sim_study` | P2 |
| **D8** | `ingestion_pipeline_mtg` | `card_name_index` published as a view | minor dedup | P3 |

`edh_solver` is **not** a dependency for A0–A8. It is Milestone 0 with one
synthetic vertical and uncalibrated opponent clocks. No work order is opened
against it.

---

## D0 — There is no model provider credential in production

**Repo:** deployment / `bristly_billy_beane` · **Priority: P0** · **Blocks: every LLM path**

> **Session brief:** *"The Fly app `dylnmtthws-decklab` has no model provider
> credential set. `config/cedh.yaml` names `HF_TOKEN` as `model.credential_env`
> and `deployment-status-2026-09-05.md` records that HF_TOKEN was never
> provided. Decide the provider and model, provision the credential, and verify
> one live structured call end to end. See D0 in
> `bristly_billy_beane-research-assistant/docs/upstream-dependencies.md`."*

### What exists today

`config/cedh.yaml` sets `model.credential_env: "HF_TOKEN"`, and
`provider_deepseek.py` reads `os.environ.get(model.credential_env, "").strip()`.

The recorded production secrets on `dylnmtthws-decklab` are
`CEDH_SIMULATOR_URL`, `MTG_V1_DSN` and `SABER_SECRET_KEY`. `HF_TOKEN` is not
among them, and the deployment record states plainly: *"HF_TOKEN was not
provided or set."*

**So the model gateway has no credential in the live deployment.** Every
`ModelGateway.generate` call in production either fails or is skipped. The
2026-09-05 acceptance run produced a complete Kinnan candidate with real
tournament evidence and a real simulation — which is the deterministic path
working exactly as ADR-022 intends, since selection finishes before the first
model call. The prose layer is a separate question and appears to have been dark
throughout.

**First task for whoever picks this up: establish what actually happened on that
acceptance run** — did the explanation degrade visibly, silently, or was it not
attempted? The answer determines whether there is also a missing-absence bug
here, because a model outage that renders as nothing rather than as a stated
absence violates `absence_is_visible`.

### Why this is P0 for Track A

The Research Assistant is an LLM feature. Phases A5 through A8 cannot be
integration-tested against production without a credential, and the cost ledger
has never recorded a real production call, so the entire cost model in
`RESEARCH_ASSISTANT_PLAN.md` §6 is unvalidated arithmetic.

It is also the natural moment to settle the provider question, since the owner
has stated a preference for a cheap or free model and DeepSeek as the incumbent
favourite.

### Requirement

1. Determine why production has run without this and whether the absence was
   visible. Fix it if it was not.
2. Decide provider and pinned model id. Production **pins** the provider — the
   model id must end `:deepinfra`; dynamic `:cheapest` routing is refused by
   constraint, because it makes the recorded model id a guess.
3. Provision the credential as a Fly secret under the name
   `model.credential_env` declares. If the provider is no longer HuggingFace,
   **rename `credential_env` to match** rather than putting a DeepInfra token in
   a variable called `HF_TOKEN`.
4. Verify one live structured call end to end, and confirm a row lands in
   `cost_log` with non-zero tokens and a computed cost.
5. Confirm the global `monthly_cost_ceiling_usd` is set to a real value and that
   exceeding it raises `LLMCostCeilingExceeded` rather than being logged and
   ignored.

### Acceptance

A live production call returns a validated Pydantic response, records a
`cost_log` row with provider, model id, prompt version and tokens, and a
deliberately induced provider outage renders as a **stated absence**, not as
silence.

---

## D1 — Corpus depth and the recurring nightly

**Repo:** `ingestion_pipeline_mtg` · **Priority: P1** · **Shapes A3; does not block it**

> **CORRECTION, 2026-09-06.** An earlier revision of this item stated the corpus
> was empty and rated this P0. That was read from `PROJECT_BRIEF.md` §7, which
> is **stale**. `deployment-backups/deployment-status-2026-09-05.md` records the
> cloud migration as executed and accepted the same day. The corpus exists, is
> populated, and passed `verify-corpus`. The item is restated below and
> downgraded.

> **Session brief:** *"In `ingestion_pipeline_mtg`, run the corpus-depth census
> in D1 of `bristly_billy_beane-research-assistant/docs/upstream-dependencies.md`
> against the live Neon database as `mtg_consumer`, report the numbers, and then
> complete the deferred nightly enablement (Healthchecks setup, HEALTHCHECK_URL,
> NIGHTLY_ENABLED, one monitored run, post-run verify-corpus)."*

### What exists today — measured, not assumed

The hosted database is live on Neon and populated. Published-image
`verify-corpus` exited zero in 8.29 s; all six migration checksums passed;
consumer SELECT on `mtg_v1` succeeds and `mtg_internal` is permission-denied.

| View | Actual rows | Verifier floor |
|---|---:|---:|
| `card` | 33,572 | 33,000 |
| `card_any_medium` | 34,570 | 34,000 |
| `card_printing` | 117,627 | 117,000 |
| `card_legality` | 888,559 | 880,000 |

A 200-tournament EDHTop16 import ran (22 min 32 s). Production acceptance
rendered real tournament evidence for Kinnan at **n = 635 distinct decks with 42
incomplete** — so the deck corpus has genuine depth for at least the commanders
that were imported.

**What has genuinely not happened:** the *recurring* nightly. `NIGHTLY_ENABLED`
is unset, `HEALTHCHECK_URL` is pending, Healthchecks is not set up, and the
manual hosted nightly, its success ping and post-nightly `verify-corpus` have
not run. The corpus is therefore a **one-time manual import that does not
refresh**, and it will silently age.

### What Track A actually needs from this

Not a corpus — that exists. **A census**, so A3's coverage thresholds are set
from measurement rather than from a guess:

| Measure | What Track A does with it |
|---|---|
| Distinct tournaments, and their date range | archetype clustering needs event diversity; the date range sets the default window |
| `tournament_entry` rows with non-null `deck_id` | the inclusion denominator |
| Distinct commander identities with ≥30 recorded decks | below ~30, a field rate is noise and A3 must report an absence |
| Distinct commander identities with ≥100 recorded decks | the set for which archetype clustering is meaningful |
| Distribution of `deck.is_complete` by commander color identity | quantifies the D6 basic-land bias for *this* corpus rather than in general |

**Report the actual numbers whatever they are.** If only 8 commanders clear 30
decks, that is the finding, and A3 sets its threshold accordingly and states
absences rather than computing percentages over n = 6. The census is a handful
of read-only queries as `mtg_consumer` and should take under an hour.

### Then: complete the nightly enablement

1. Set up the Healthchecks account and check; set `HEALTHCHECK_URL`.
2. **Obtain explicit owner approval**, then set `NIGHTLY_ENABLED=true`.
3. Dispatch one monitored nightly; confirm the success ping.
4. Run `verify-corpus` again and diff the corpus snapshot hash.

### What this branch does in the meantime

A3 builds against the live corpus directly — it is there. Fixture-based tests
remain for CI, which has no database. Coverage thresholds ship as configuration
so the census can set them without a code change.

### Integration handshake

Set `MTG_V1_DSN`, run the A3 materializer, and compare the fixture-derived and
live-derived scorecards on the golden set. **The A3 acceptance test asserts that
coverage thresholds fire correctly on live data** — that a thin-sample commander
returns a stated absence rather than a number.

---

## D2 — Resolve the two-corpus fork

**Repo:** `bristly_billy_beane` (branch `deck-lab-refactor`) · **Priority: P1**

> **Session brief:** *"In the `deck-lab-refactor` worktree, migrate
> `src/sabermetrics/research.py` off the legacy SQLite corpus tables
> (`commander_candidates`, `tournament_results`, `deck_cards`, `cards`) onto
> `mtg_v1` through a repository interface in the style of
> `cedh/repositories.py`. See D2 in
> `bristly_billy_beane-research-assistant/docs/upstream-dependencies.md`."*

### What exists today, and why it is a problem

The product currently reads **two different corpora** for the same facts:

| Surface | Reads | Filled by |
|---|---|---|
| cEDH path (`cedh/adapters_postgres.py`) | `mtg_v1` over TLS, as `mtg_consumer` | `ingestion_pipeline_mtg` |
| Deck Lab Research (`research.py`) | local SQLite `commander_candidates`, `tournament_results`, `deck_cards`, `cards`, `card_rulings` | `ingestion/topdeck.py` — **superseded** |

`docs/integration-handoff.md` §4 already classifies `ingestion/topdeck.py` as
*"Superseded on delivery of §2.1"*, and §2.1 has been delivered. The Deck Lab
research surface is running on the deprecated path.

Consequences if left alone: the assistant's card search and the assistant's
field statistics would disagree with each other, snapshot hashes would be
meaningless (two corpora, one hash field), and every citation would be
ambiguous about which corpus it referred to.

### Requirement

1. Track A's `substrate/` reads **`mtg_v1` only** for card facts and tournament
   facts, through a repository interface, with `assert_v1_only` applied and
   `card_any_medium` used rather than `card`.
2. Local SQLite holds **derived indexes only** — mechanic tags, embeddings,
   field statistics, plan memory — every artifact keyed by `oracle_id` plus the
   `corpus_snapshot` hash it was derived from.
3. `research.py`'s browse surface migrates onto the same interface. The `LIKE
   '%q%'` card search is replaced by the A2 retrieval substrate rather than
   ported.
4. The legacy tables and `ingestion/topdeck.py` stay in place for the legacy
   casual generator, unchanged and untouched.

### Acceptance

- No Track A module reads `commander_candidates` or `tournament_results`.
- A test asserts the Research surface and the cEDH surface resolve the same
  commander to the same `oracle_id` and the same recorded-deck count.
- Every derived artifact carries the `corpus_snapshot` hash it was built from,
  and a stale artifact is detected rather than served.

### Note on ordering

This can be done **before** D1. Against an empty corpus the queries return
honest absences, which is exactly the behaviour that needs testing anyway.

---

## D3 — Structured per-card inert and unauthored lists

**Repo:** `commander_simulator` · **Priority: P1** · **Blocks: A6 honesty**

> **Session brief:** *"Add `cedh-simulation-result.v4` to
> `commander_simulator/contracts/`, adding structured per-card `inert_cards` and
> `unauthored_cards` arrays alongside the existing counts. v3 stays accepted.
> See D3 in
> `bristly_billy_beane-research-assistant/docs/upstream-dependencies.md`."*

### What exists today

`docs/integration-handoff.md` §3.5: the result reports inert and unauthored
cards as **counts plus warning strings**, not as a per-card structured list.
`SimulationResult.inert_cards` stays empty on the live path;
`inert_card_count` / `unauthored_card_count` carry the figures. The handoff is
explicit that a structured list "is a new field and a new result version, not
something to reconstruct by parsing warning text."

### Why Track A needs it

Phase A6's citation contract forbids rendering a simulator number without its
inert-set disclosure. With counts only, the assistant can say *"31 of 99 cards
were invisible to this model"* but cannot say **which** — and "which" is the
part a deck builder can act on. Telling someone their Consecrated Sphinx was
measured at −0.01% because the model has no opponents is useful. Telling them
31 unnamed cards were invisible is not.

Parsing the warning strings is the obvious workaround and it is the one the
handoff correctly rules out: a display that breaks when a warning is reworded is
a contract that was never agreed.

### Requirement

`cedh-simulation-result.v4` adding, beside the existing counts:

- `coverage.inert_cards[]` — `{name, oracle_id, reason, reason_category}`, the
  category from the existing closed set
- `coverage.unauthored_cards[]` — `{name, oracle_id, missing_effect_kind}`
- `coverage.misclassified_cards[]` — the *Hullbreaker Horror* case, structured.
  A known misclassification that is printed in the run report but absent from
  the machine contract is visible to a human and invisible to a consumer.

Counts remain and must equal the array lengths — asserted by a test, so the two
representations cannot drift.

### Acceptance

- v4 published in `contracts/`; v3 remains accepted and unchanged.
- Golden vectors extended.
- Counts equal array lengths, asserted.
- The Deck Lab's vendored copies under `fixtures/cedh/contracts/` updated with
  digests checked by CI, per the existing vendoring discipline.

### What this branch does in the meantime

A6 renders the counts and states that the per-card list is unavailable — a
stated absence, per `absence_is_visible`. It does not parse warning text.

---

## D4 — Combo corpus

**Repo:** `ingestion_pipeline_mtg` · **Priority: P1** · **Decision required first**

> **Session brief:** *"Ingest Commander Spellbook into `mtg_internal` and
> publish `mtg_v1.combo` and `mtg_v1.combo_piece` views. See D4 in
> `bristly_billy_beane-research-assistant/docs/upstream-dependencies.md`."*

### What exists today

Commander Spellbook is listed as a source in `CLAUDE.md` and there is a legacy
`ingestion/spellbook.py` writing to local SQLite. `docs/integration-handoff.md`
§4 records it as *"No `mtg_v1` equivalent requested. Legacy only."*

### Why Track A wants it

*"What two-card lines am I one piece away from"* is a pure graph query, entirely
deterministic, and among the most valuable questions a cEDH builder asks. It
feeds A4's `combo_lookup` and A8's candidate generation, and it needs no model
at all — which makes it exactly the kind of substrate capability this plan is
betting on.

### The decision, before the work

Adding a source to the ingestion pipeline is a contract addition and a
maintenance commitment. Three options:

1. **Ingest into `mtg_v1`** — correct long-term, consistent with every other
   fact having provenance and a snapshot hash. Most work.
2. **Local-only in Track A's substrate** — faster, but re-creates exactly the
   duplicate-ingestion problem D2 exists to close.
3. **Defer** — drop the combo category from the golden set for Phase 1 and
   revisit after G2.

**Recommendation: option 1 if the combo category matters to you, option 3 if
you are unsure.** Option 2 is the one to avoid; it is a shortcut that costs more
later than the work it saves now.

### Requirement (if option 1)

- `mtg_v1.combo` — `combo_id`, `source`, `source_url`, `produces[]`,
  `prerequisites`, `steps`, `color_identity`, provenance and snapshot fields
- `mtg_v1.combo_piece` — `combo_id`, `oracle_id`, `position`
- Pieces resolve by `oracle_id`, consistent with the EDHTop16 finding that
  oracle IDs resolve cleanly where names do not
- Unresolved pieces are retained with a null `oracle_id`, never dropped — the
  same rule `deck_card` already follows

### Acceptance

Combos resolve to `oracle_id` at a reported rate, with the unresolved set
countable and inspectable rather than silently absent.

---

## D5 — Card rulings in `mtg_v1`

**Repo:** `ingestion_pipeline_mtg` · **Priority: P2** · Not blocking

### What exists today

Rulings live only in the legacy SQLite `card_rulings` table, filled by
`ingestion/mtgapi.py`. `docs/integration-handoff.md` §4 marks that module
*"Superseded once `mtg_v1` publishes rulings"* — which it does not yet.

### Requirement

`mtg_v1.card_ruling` — `oracle_id`, `published_at`, `text`, `source`, plus
snapshot provenance. Joined by `oracle_id`, not by printing, so rulings persist
across reprints (this is already ADR-014's rule on the consumer side).

### What this branch does in the meantime

A4's `rules_lookup` grounds on the Comprehensive Rules through the existing
`reference_layer/`, which is built and needs only reactivating, and reads
rulings from the legacy SQLite table where present — flagged in the citation as
a legacy source so the provenance is not overstated.

---

## D6 — Stage 4 Moxfield adapter: real card quantities

**Repo:** `ingestion_pipeline_mtg` · **Priority: P2** · Affects A3/A8 accuracy

### The measured problem

From `STATE.md`, and this is a finding rather than a defect: **EDHTop16 sends no
card quantities.** `Entry.maindeck` is a *set* of distinct cards, so duplicate
copies collapse to one. The evidence is a clean gradient over 311 real
decklists:

| Commander color identity | Avg. missing cards | Arrive complete |
|---|---|---|
| Mono-colored | 9.1 | 3% |
| Five-color | 0.1 | 88% |

Those are deduplicated basic lands.

### Why Track A cares, specifically

For singleton non-basic cards, presence equals quantity, so **the mechanic tag
profiles in A3 are unaffected** — "how many free spells does this list play" is
correct today.

**Mana-base analysis is not.** A3's `deck_diff` and A8's candidate generation
would otherwise want to say *"you are three blue sources light by turn two
relative to the field"*, and the field's basic-land counts are missing in a way
that is **correlated with color identity**. A naive comparison would
systematically report mono-colored decks as land-light. That is a bias with a
direction, which is the worst kind, because it looks like a finding.

### Requirement

Stage 4 as already planned in `PLAN.md` — the Moxfield adapter, which carries
real quantities and complete lists.

### What this branch does in the meantime — and this is required regardless

A3 **excludes basic lands from every field-derived count** and reports
`is_complete` alongside every deck-level statistic. Any mana-base comparison
either restricts to `is_complete = true` decks and states the reduced
denominator, or is not offered. This is not a temporary workaround: it is
correct behaviour that should survive Stage 4.

---

## D7 — Pack discovery at scale

**Repo:** `commander_simulator` · **Priority: P2**

### What exists today

`GET /healthz` lists installed packs (`strategy_packs: ["kinnan-midrange-goldfish@1.0.0"]`),
and `HttpSimulatorClient.supported_commander_keys()` returns the empty set —
support is discovered by attempting a request and reading a `422`.

That is fine for one pack. At the 15–25 packs Track B targets, Track A needs to
know what is supported *before* planning a `sim_study` step, so the planner can
choose a different approach rather than burning a round trip on a refusal.

### Requirement

`/healthz` (or a new `GET /packs`) returns, per installed pack: `pack_id`,
`version`, `supported_commander_oracle_ids[]`, `scenario`, and the pack's
declared assembly objectives. Cheap, cacheable, no simulation.

### What this branch does in the meantime

A4's `sim_study` attempts the request and maps `422 unsupported` to
`NotSimulated(commander_unsupported)`, which is already the correct behaviour
and is already implemented on the consumer side.

---

## D8 — `card_name_index` as a published view

**Repo:** `ingestion_pipeline_mtg` · **Priority: P3**

`docs/integration-handoff.md` §2.4 records this as wanted, not blocking. Two
consumers (Deck Lab and the simulator's exporter) each carry the same four-line
`name = ANY(...) OR split_part(name, ' // ', 1) = ANY(...)` workaround.
Migration 0004 created the underlying index; it is not published as a `mtg_v1`
view.

Track A resolves by `oracle_id` almost everywhere, so this affects only
user-typed card-name lookup. Low value, low cost, mentioned for completeness.

---

## Suggested parallel session plan

| Session | Item | Repo | Rough size |
|---|---|---|---|
| 1 | **D0** — model credential and provider decision | deployment / `bristly_billy_beane` | ~1 day, plus a decision |
| 2 | **D1** — corpus census, then nightly enablement | `ingestion_pipeline_mtg` | ~1 hr census; ~2 days enablement |
| 3 | **D2** — corpus fork | `bristly_billy_beane` @ `deck-lab-refactor` | ~3 days |
| 4 | **D3** — result v4 | `commander_simulator` | ~2 days |
| 5 | **D4** — combos *(after the decision)* | `ingestion_pipeline_mtg` | ~1 week |
| 6 | **B0 + B1** — pack targets and verb gap | `commander_simulator` @ `strategy-packs` | ~1.5 weeks |

Sessions 1–4 are independent of each other and of Track A's A0–A2. Session 6 is
the sizing gate for the entire simulator track and is the cheapest way to learn
what that track actually costs.

**Start D0 first.** It is the only P0, it is a day of work, and until it is done
no LLM feature in this product has ever executed in production — which means the
cost model, the ceiling behaviour and the provider choice are all unvalidated.

**Then run D1's census before anything else in Track A's A3.** It is under an
hour of read-only queries and it converts A3's coverage thresholds from a guess
into a measurement.
