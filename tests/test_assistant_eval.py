"""R0 acceptance tests for the golden set and both measuring rigs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sabermetrics.assistant.eval import (
    CorrectnessObservation,
    GoldenQuestion,
    HumanReview,
    correctness_scorecard,
    load_questions,
    usefulness_scorecard,
)

ROOT = Path(__file__).resolve().parent.parent


def test_golden_set_has_the_r0_size_and_every_category():
    question_set = load_questions()
    assert len(question_set.questions) >= 80
    assert {question.category for question in question_set.questions} == {
        "mechanic_search",
        "rules",
        "metagame",
        "deck_local",
        "combo",
        "ambiguous",
        "out_of_scope",
        "healthy_deck",
    }


def test_every_label_is_attributable_and_agent_drafts_are_contested():
    for question in load_questions().questions:
        assert question.labeller
        assert question.labelled_on
        if "agent" in question.labeller.casefold():
            assert (
                question.contested
            ), f"{question.id} presents an agent label as settled"


def test_every_labelled_oracle_id_resolves_in_the_checked_in_corpus_fixture():
    fixture = json.loads((ROOT / "fixtures/cedh/cards.json").read_text())
    known = {card["oracle_id"] for card in fixture["cards"]}
    for question in load_questions().questions:
        labelled = set(question.required_oracle_ids) | set(
            question.forbidden_oracle_ids
        )
        assert (
            labelled <= known
        ), f"{question.id} has unknown ids: {sorted(labelled - known)}"


def test_category_semantics_are_structural():
    values = {
        "id": "ambiguous-probe",
        "ask": "Find better interaction for this deck.",
        "clarified_ask": "Ask which threat and deck context the player means.",
        "required_oracle_ids": [],
        "forbidden_oracle_ids": [],
        "expected_absences": [],
        "category": "ambiguous",
        "difficulty": "basic",
        "labeller": "test",
        "labelled_on": "2026-09-08",
    }
    with pytest.raises(ValueError, match="expect clarification"):
        GoldenQuestion.model_validate(values)


def test_empty_correctness_run_is_zero_and_never_invents_perfect_coverage():
    scorecard = correctness_scorecard(load_questions())
    assert scorecard["questions_evaluated"] == 0
    assert set(scorecard["quality"].values()) == {0.0}
    for invariant in scorecard["invariants"].values():
        assert invariant == {
            "status": "not_measured",
            "numerator": 0,
            "denominator": 0,
            "value": 0.0,
        }


def test_correctness_rig_measures_recall_and_structural_failures():
    question_set = load_questions()
    question = next(q for q in question_set.questions if q.required_oracle_ids)
    required = question.required_oracle_ids[0]
    observation = CorrectnessObservation(
        question_id=question.id,
        returned_oracle_ids=[required],
        result_set_oracle_ids=[required],
        named_oracle_ids=[required, "not-in-results"],
        assertion_count=2,
        cited_assertion_count=1,
        bare_rate_count=1,
    )
    scorecard = correctness_scorecard(question_set, [observation])
    assert scorecard["quality"]["required_recall_at_50"] > 0
    assert scorecard["invariants"]["citation_coverage"]["status"] == "fail"
    assert scorecard["invariants"]["cards_named_outside_result_set"]["status"] == "fail"
    assert scorecard["invariants"]["bare_rate_count"]["status"] == "fail"


def test_empty_usefulness_run_is_explicitly_not_measured():
    scorecard = usefulness_scorecard()
    assert scorecard["reviews_total"] == 0
    assert scorecard["citation_support"]["status"] == "not_measured"
    assert scorecard["citation_support"]["value"] == 0.0
    assert scorecard["retrieval_relevance"]["status"] == "not_measured"


def test_usefulness_is_adjudicated_separately():
    review = HumanReview(
        question_id="rules-001",
        reviewer="test reviewer",
        reviewed_on="2026-09-08",
        citation_support=True,
        rules_accurate=False,
        retrieval_relevance=0.75,
        comprehension=True,
        task_time_seconds=30,
        returned_for_second_question=False,
    )
    scorecard = usefulness_scorecard([review])
    assert scorecard["citation_support"]["value"] == 1.0
    assert scorecard["rules_accuracy"]["value"] == 0.0
    assert scorecard["retrieval_relevance"]["value"] == 0.75
