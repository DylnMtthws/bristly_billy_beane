# Vendored simulator contracts

Copied verbatim from `/Users/dylan/Projects/commander_simulator/contracts/` on
2026-09-04. No simulator code is imported into this repository; these files are
the integration artifact, and they are the *only* thing shared.

| File | Direction | What it pins |
|---|---|---|
| `cedh-deck-candidate.v2.schema.json` | we send | the deck document inside the request |
| `cedh-simulation-request.v1.schema.json` | we send | the `POST /simulate` envelope |
| `cedh-simulation-result.v3.schema.json` | we receive | the result |
| `hash-golden-vectors.json` | both | `deck_sha256` and `simulation_input_sha256` |

**Both directions are vendored, deliberately.** The previous revision pinned
only the result. The request body was described in prose, so each repository
built its own reading of it, each suite tested its own shape against itself,
both stayed green, and every real build failed on the wire with
`422 unsupported candidate schema_version`. A contract pinned in one direction
is not pinned.

`hash-golden-vectors.json` is the second half of that lesson. Agreeing on the
*shape* of a field is not agreeing on its *meaning*: v1 had one field named
`candidate_hash` that this repository read as "the deck" and the simulator
computed as "the deck plus the strategy pack". The vectors make the two hashes'
meanings executable, and `tests/test_cedh_hash_contract.py` checks this
repository's ADR-025 implementation against the same file the simulator's C++
and Python tests use.

Regenerate on the simulator side with
`uv run --project export python scripts/make_hash_golden_vectors.py`, then
re-copy the four files above together. They are one artifact; do not update
one without the others.

The result schema also ships as package data at
`src/sabermetrics/cedh/contracts/cedh-simulation-result.v3.schema.json` so installed
clients can validate responses without the repository checkout. Refresh that
copy when updating the vendored contracts; a test requires byte-for-byte parity.
