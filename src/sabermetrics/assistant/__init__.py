"""Query IR, executor, planner, narrator, threads, and the eval harness.

Sits **beside** :mod:`sabermetrics.cedh`, not inside it. It borrows the model
gateway, the cost ledger and the simulator client; it does not join the
deterministic generation path, and ``cedh/`` may not import it (ADR-030).

Two rules define what the model is allowed to be:

* **The model plans and narrates. It does not retrieve, rank, score or
  compute.** Every card in every answer is an ``oracle_id`` that came out of a
  query result set, so corpus-wide hallucination is prevented by construction
  rather than by prompt wording.
* **Every rendered assertion carries its tier and its citations.** Fact, field
  evidence, measurement, interpretation — see ADR-029. An interpretation with
  no supporting citation is dropped by the renderer.

May import ``mechanics``, ``substrate``, ``cedh.model_gateway``,
``cedh.cost_ledger``, ``cedh.simulator``, ``deck_documents`` and
``reference_layer``. May **not** import ``pipeline``, ``reasoning``,
``ingestion``, ``analytics``, or any vendor model SDK.

Enforced by ``tests/test_package_boundaries.py``.
"""
