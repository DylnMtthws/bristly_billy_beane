"""No per-card LLM in the selection hot path (Option A DoD criterion 4).

The deterministic synergy optimizer selects cards. The LLM's only role in
assembly is the single batched safety vet that audits the finished deck
(SME-directed after builds 7-9: the numeric objective cannot read oracle
text). That vet is one `score_cards_batch` call after rebalancing — never a
per-card scorer inside the greedy/swap selection loop.
"""

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "sabermetrics"


def test_selection_loop_has_no_llm_calls() -> None:
    """The optimizer modules that pick cards never touch the LLM."""
    for module in ("pipeline/greedy_optimizer.py", "analytics/synergy_matrix.py"):
        src = (_SRC / module).read_text()
        for banned in ("FitScorer", "card_fit", "score_cards", "anthropic"):
            assert banned not in src, f"{module} references {banned}"


def test_builder_llm_use_is_one_batched_vet() -> None:
    """The builder's only fit-scoring entry is the batched safety vet.

    `score_card_fit` (the old per-card scorer) must not appear; the batched
    `score_cards_batch` is invoked exactly once, inside _llm_safety_check.
    """
    src = (_SRC / "pipeline" / "deck_builder.py").read_text()
    assert "score_card_fit" not in src, "per-card fit scoring is back"
    calls = re.findall(r"\.score_cards_batch\(", src)
    assert len(calls) == 1, f"expected 1 batched vet call, found {len(calls)}"
