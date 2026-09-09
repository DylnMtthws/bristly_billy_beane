# Research Assistant architecture decisions

_Accepted 2026-09-08. These decisions apply to the cEDH Research Assistant,
whose player-facing name is **Ask**._

## ADR-029 — Permit typed, cited interpretation

**Status:** Accepted

**Decision:** Ask may make a reasoned assessment of how a card fits a deck, but
only as a typed `Interpretation` assertion. Every such assertion must:

- refer to an `oracle_id` from a bounded deterministic query result;
- cite at least one Fact, Field evidence, or Measurement assertion;
- carry an explicit confidence;
- state what the cited evidence does not establish; and
- render distinctly from facts, field observations, and simulator measurements.

An interpretation without supporting citations or a non-empty
`does_not_establish` field is dropped by the renderer. The model still may not
decide legality, invent a card or oracle text, create simulator mechanics, or
select from the whole card corpus.

**Why:** A system limited to reciting facts omits much of the value of the
existing reasoning work, while free-form evaluation would blur evidence and
judgment. A separate typed tier permits useful assessment without presenting it
as corpus fact or measured outcome.

**Consequences:** Citation presence becomes structural; citation support remains
a separately adjudicated usefulness metric. An inclusion rate can support
investigation but cannot, by itself, establish card quality or improvement in a
specific list.

## ADR-030 — Put Ask beside the deterministic cEDH path

**Status:** Accepted

**Decision:** The Research Assistant lives in `sabermetrics.assistant`, beside
`sabermetrics.cedh`, with deterministic knowledge and retrieval in
`sabermetrics.substrate` and pure text-to-mechanic predicates in
`sabermetrics.mechanics`.

The model plans typed queries and narrates their structured results. It does not
retrieve, rank, score, compute, or name a card that is absent from the executor's
result set. `assistant` may borrow the provider-neutral model gateway, cost
ledger, simulator client, deck documents, and reference layer. It may not import
the legacy `pipeline`, `reasoning`, `ingestion`, or `analytics` packages, nor a
vendor model SDK. The deterministic `cedh` generation path may not import
`assistant`.

These are executable import rules in `tests/test_package_boundaries.py`.

**Why:** Putting Ask inside `cedh` would let a planning dependency leak backward
into deterministic deck construction and weaken ADR-022's guarantee that card
selection finishes before the first model call. Importing legacy analytics would
silently inherit the casual/budget objective that ADR-019 and ADR-025 rejected.

**Consequences:** Ask has no deck-generation tool. Every capability it
orchestrates remains reachable through an ordinary Deck Lab control, and a model
or provider outage can remove planning or prose but cannot change a generated
99-card list.
