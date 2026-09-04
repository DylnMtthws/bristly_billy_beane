"""cEDH Deck Lab.

The cEDH path is deliberately separate from the legacy casual generator under
``sabermetrics.pipeline``. It owns product workflow, intent, cEDH strategy
selection, evidence retrieval, deterministic candidate construction, model
provider integration, simulator orchestration and presentation — and nothing
else. Card facts and tournament facts arrive through the repository interfaces
in :mod:`sabermetrics.cedh.repositories`, which read the ``mtg_v1`` contract
owned by the ingestion pipeline; simulation arrives through
:mod:`sabermetrics.cedh.simulator`.

Nothing in this package imports the ingestion or simulator source, calls
EDHTop16/Scryfall/TopDeck/deck-hosting sites, or depends on the legacy
``sabermetrics.ingestion`` modules.
"""
