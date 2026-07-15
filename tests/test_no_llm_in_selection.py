"""No per-card LLM in the selection hot path (Option A DoD criterion 4).

The deterministic synergy optimizer selects cards; the LLM is a narrator/auditor
only (profile synthesis + deck narrative). The end-to-end proof that a build
makes zero card_fit calls lives in test_end_to_end_build.py.
"""

from pathlib import Path


def test_selection_path_has_no_card_fit_calls() -> None:
    """Source guard: the builder no longer wires FitScorer / card_fit."""
    src = (
        Path(__file__).resolve().parent.parent
        / "src" / "sabermetrics" / "pipeline" / "deck_builder.py"
    ).read_text()
    for banned in ("FitScorer", "_llm_safety_check", "card_fit", "score_cards"):
        assert banned not in src, f"selection path still references {banned}"
