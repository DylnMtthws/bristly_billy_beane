"""Golden questions and evaluation scorecards for the Ask assistant.

R0 intentionally ships the measuring instruments before retrieval, planning,
or narration exist.  An empty run is therefore a supported state: quality
metrics are numeric zeroes and structural invariants are ``not_measured`` over
zero assertions.  In particular, zero assertions never become an invented
100% citation-coverage result.
"""

from sabermetrics.assistant.eval.models import (
    CorrectnessObservation,
    GoldenQuestion,
    GoldenQuestionSet,
    HumanReview,
    load_questions,
)
from sabermetrics.assistant.eval.scoring import (
    correctness_scorecard,
    usefulness_scorecard,
)

__all__ = [
    "CorrectnessObservation",
    "GoldenQuestion",
    "GoldenQuestionSet",
    "HumanReview",
    "correctness_scorecard",
    "load_questions",
    "usefulness_scorecard",
]
