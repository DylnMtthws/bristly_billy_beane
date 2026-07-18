"""No per-card LLM in the selection hot path (Option A DoD criterion 4).

The deterministic synergy optimizer selects cards. The LLM's only role in
assembly is the single batched safety vet that audits the finished deck
(SME-directed after builds 7-9: the numeric objective cannot read oracle
text). That vet is one `score_cards_batch` call after rebalancing — never a
per-card scorer inside the greedy/swap selection loop.
"""

from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "sabermetrics"


def test_selection_loop_has_no_llm_calls() -> None:
    """The optimizer modules that pick cards never touch the LLM."""
    for module in ("pipeline/greedy_optimizer.py", "analytics/synergy_matrix.py"):
        src = (_SRC / module).read_text()
        for banned in ("FitScorer", "card_fit", "score_cards", "anthropic"):
            assert banned not in src, f"{module} references {banned}"


def test_builder_llm_use_is_confined_to_the_safety_vet() -> None:
    """All batched fit-scoring lives inside _llm_safety_check.

    `score_card_fit` (the old per-card scorer) must not appear anywhere; the
    batched `score_cards_batch` may be called only from the safety vet (the
    initial audit plus the re-vet of its own swap-ins), never from a
    selection-stage method.
    """
    import ast

    path = _SRC / "pipeline" / "deck_builder.py"
    src = path.read_text()
    assert "score_card_fit" not in src, "per-card fit scoring is back"

    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        calls = [
            n for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "score_cards_batch"
        ]
        if calls and node.name != "_llm_safety_check":
            offenders.append(node.name)
    assert not offenders, f"score_cards_batch called outside the vet: {offenders}"
