"""The indexed knowledge layer: reads ``mtg_v1``, writes local indexes.

Sits between :mod:`sabermetrics.mechanics` (pure predicates over card text) and
:mod:`sabermetrics.assistant` (query planning and narration). Everything here is
deterministic and cacheable: retrieval, field statistics, and the materialised
tag corpus.

May import ``mechanics``, ``db``, the cEDH adapters, and numpy. May **not**
import ``pipeline``, ``reasoning``, ``ingestion``, ``analytics``, or any vendor
model SDK — those carry the casual/budget objective this path does not share,
and a vendor SDK here would make the provider a rewrite rather than an adapter
(ADR-021).

Enforced by ``tests/test_package_boundaries.py``.
"""
