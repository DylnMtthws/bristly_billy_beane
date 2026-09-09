"""Pure score construction for the correctness and usefulness rigs."""

from __future__ import annotations

from collections.abc import Iterable
from math import ceil
from statistics import median
from typing import Any

from sabermetrics.assistant.eval.models import (
    CorrectnessObservation,
    GoldenQuestionSet,
    HumanReview,
)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    """Nearest-rank percentile, with zero as the explicit empty state."""

    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _invariant(*, numerator: int, denominator: int, expected: float) -> dict[str, Any]:
    if denominator == 0:
        return {
            "status": "not_measured",
            "numerator": 0,
            "denominator": 0,
            "value": 0.0,
        }
    value = _rate(numerator, denominator)
    return {
        "status": "pass" if value == expected else "fail",
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
    }


def correctness_scorecard(
    question_set: GoldenQuestionSet,
    observations: Iterable[CorrectnessObservation] = (),
    *,
    mode: str = "substrate",
    recall_k: int = 50,
) -> dict[str, Any]:
    """Score structured run observations without treating absence as success."""

    by_id = {question.id: question for question in question_set.questions}
    rows = list(observations)
    unknown = sorted({row.question_id for row in rows} - set(by_id))
    if unknown:
        raise ValueError(f"observations reference unknown question ids: {unknown}")
    duplicate_ids = sorted(
        {
            row.question_id
            for row in rows
            if sum(r.question_id == row.question_id for r in rows) > 1
        }
    )
    if duplicate_ids:
        raise ValueError(f"duplicate observations: {duplicate_ids}")

    recall_scores: list[float] = []
    forbidden_hits = 0
    forbidden_opportunities = 0
    absence_hits = 0
    absence_opportunities = 0
    manufactured = 0
    healthy_opportunities = 0
    clarification_correct = 0
    for row in rows:
        question = by_id[row.question_id]
        required = set(question.required_oracle_ids)
        if required:
            returned = set(row.returned_oracle_ids[:recall_k])
            recall_scores.append(len(required & returned) / len(required))
        forbidden = set(question.forbidden_oracle_ids)
        if forbidden:
            forbidden_opportunities += 1
            forbidden_hits += bool(forbidden & set(row.returned_oracle_ids))
        if question.expected_absences:
            absence_opportunities += 1
            absence_hits += row.absence_stated
        if question.no_finding_expected:
            healthy_opportunities += 1
            manufactured += row.finding_count > 0
        clarification_correct += (
            row.clarification_requested == question.clarification_expected
        )

    assertion_count = sum(row.assertion_count for row in rows)
    cited_count = sum(row.cited_assertion_count for row in rows)
    outside_count = sum(
        len(set(row.named_oracle_ids) - set(row.result_set_oracle_ids)) for row in rows
    )
    bare_rate_count = sum(row.bare_rate_count for row in rows)

    return {
        "schema_version": "research-correctness-scorecard.v1",
        "mode": mode,
        "questions_total": len(question_set.questions),
        "questions_evaluated": len(rows),
        "quality": {
            f"required_recall_at_{recall_k}": (
                sum(recall_scores) / len(recall_scores) if recall_scores else 0.0
            ),
            "forbidden_oracle_id_hit_rate": _rate(
                forbidden_hits, forbidden_opportunities
            ),
            "absence_stated_rate": _rate(absence_hits, absence_opportunities),
            "manufactured_finding_rate": _rate(manufactured, healthy_opportunities),
            "clarification_appropriateness": _rate(clarification_correct, len(rows)),
        },
        "invariants": {
            "citation_coverage": _invariant(
                numerator=cited_count, denominator=assertion_count, expected=1.0
            ),
            "cards_named_outside_result_set": _invariant(
                numerator=outside_count,
                denominator=assertion_count,
                expected=0.0,
            ),
            "bare_rate_count": _invariant(
                numerator=bare_rate_count,
                denominator=assertion_count,
                expected=0.0,
            ),
        },
        "operational": {
            "cost_usd_p50": median([row.cost_usd for row in rows]) if rows else 0.0,
            "cost_usd_p95": _percentile([row.cost_usd for row in rows], 0.95),
            "latency_ms_p50": median([row.latency_ms for row in rows]) if rows else 0.0,
            "latency_ms_p95": _percentile([row.latency_ms for row in rows], 0.95),
        },
    }


def _review_rate(values: Iterable[bool | None]) -> dict[str, Any]:
    measured = [value for value in values if value is not None]
    if not measured:
        return {"status": "not_measured", "samples": 0, "value": 0.0}
    return {
        "status": "measured",
        "samples": len(measured),
        "value": sum(measured) / len(measured),
    }


def usefulness_scorecard(reviews: Iterable[HumanReview] = ()) -> dict[str, Any]:
    """Report human-adjudicated usefulness separately from correctness."""

    rows = list(reviews)
    relevance = [
        r.retrieval_relevance for r in rows if r.retrieval_relevance is not None
    ]
    task_times = [r.task_time_seconds for r in rows if r.task_time_seconds is not None]
    return {
        "schema_version": "research-usefulness-scorecard.v1",
        "reviews_total": len(rows),
        "citation_support": _review_rate(r.citation_support for r in rows),
        "rules_accuracy": _review_rate(r.rules_accurate for r in rows),
        "retrieval_relevance": {
            "status": "measured" if relevance else "not_measured",
            "samples": len(relevance),
            "value": sum(relevance) / len(relevance) if relevance else 0.0,
        },
        "comprehension": _review_rate(r.comprehension for r in rows),
        "task_time_seconds_p50": {
            "status": "measured" if task_times else "not_measured",
            "samples": len(task_times),
            "value": median(task_times) if task_times else 0.0,
        },
        "return_rate": _review_rate(r.returned_for_second_question for r in rows),
    }
