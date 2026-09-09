"""Strict schemas for the two Research Assistant evaluation rigs."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

QUESTION_SCHEMA_VERSION: Literal["research-golden-questions.v1"] = (
    "research-golden-questions.v1"
)
QUESTION_DIR = Path(__file__).with_name("questions")

QuestionCategory = Literal[
    "mechanic_search",
    "rules",
    "metagame",
    "deck_local",
    "combo",
    "ambiguous",
    "out_of_scope",
    "healthy_deck",
]
Difficulty = Literal["basic", "intermediate", "hard"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoldenQuestion(_StrictModel):
    """One hand-labelled question and its expected observable behaviour."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    ask: str = Field(min_length=8)
    clarified_ask: str = Field(min_length=8)
    required_oracle_ids: list[str]
    forbidden_oracle_ids: list[str]
    expected_absences: list[str]
    category: QuestionCategory
    difficulty: Difficulty
    context_id: str | None = None
    clarification_expected: bool = False
    no_finding_expected: bool = False
    labeller: str = Field(min_length=2)
    labelled_on: date
    contested: bool = False

    @model_validator(mode="after")
    def check_semantics(self) -> GoldenQuestion:
        required = set(self.required_oracle_ids)
        forbidden = set(self.forbidden_oracle_ids)
        overlap = sorted(required & forbidden)
        if overlap:
            raise ValueError(
                f"oracle ids cannot be both required and forbidden: {overlap}"
            )
        if len(required) != len(self.required_oracle_ids):
            raise ValueError("required_oracle_ids contains duplicates")
        if len(forbidden) != len(self.forbidden_oracle_ids):
            raise ValueError("forbidden_oracle_ids contains duplicates")
        if self.category == "ambiguous" and not self.clarification_expected:
            raise ValueError("ambiguous questions must expect clarification")
        if self.category == "out_of_scope" and not self.expected_absences:
            raise ValueError("out-of-scope questions must name the expected absence")
        if self.category == "healthy_deck" and not self.no_finding_expected:
            raise ValueError(
                "healthy-deck questions must prohibit a manufactured finding"
            )
        if (
            self.category in {"deck_local", "combo", "healthy_deck"}
            and not self.context_id
        ):
            raise ValueError(f"{self.category} questions require context_id")
        return self


class GoldenQuestionFile(_StrictModel):
    schema_version: Literal["research-golden-questions.v1"]
    questions: list[GoldenQuestion] = Field(min_length=1)


class GoldenQuestionSet(_StrictModel):
    """The merged, uniqueness-checked contents of ``questions/*.yaml``."""

    schema_version: Literal["research-golden-questions.v1"] = QUESTION_SCHEMA_VERSION
    questions: list[GoldenQuestion]

    @model_validator(mode="after")
    def unique_ids(self) -> GoldenQuestionSet:
        ids = [question.id for question in self.questions]
        duplicates = sorted(
            {question_id for question_id in ids if ids.count(question_id) > 1}
        )
        if duplicates:
            raise ValueError(f"duplicate golden question ids: {duplicates}")
        return self


class CorrectnessObservation(_StrictModel):
    """Executor/narrator facts consumed by the machine correctness rig.

    The rig receives already structured facts.  It never parses prose to guess
    whether a citation, denominator, or card reference was present.
    """

    question_id: str
    returned_oracle_ids: list[str] = Field(default_factory=list)
    result_set_oracle_ids: list[str] = Field(default_factory=list)
    named_oracle_ids: list[str] = Field(default_factory=list)
    assertion_count: int = Field(default=0, ge=0)
    cited_assertion_count: int = Field(default=0, ge=0)
    bare_rate_count: int = Field(default=0, ge=0)
    absence_stated: bool = False
    clarification_requested: bool = False
    finding_count: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    latency_ms: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def cited_assertions_cannot_exceed_assertions(self) -> CorrectnessObservation:
        if self.cited_assertion_count > self.assertion_count:
            raise ValueError("cited_assertion_count cannot exceed assertion_count")
        return self


class HumanReview(_StrictModel):
    """One adjudicated sample for the separate usefulness rig."""

    question_id: str
    reviewer: str = Field(min_length=2)
    reviewed_on: date
    citation_support: bool | None = None
    rules_accurate: bool | None = None
    retrieval_relevance: float | None = Field(default=None, ge=0.0, le=1.0)
    comprehension: bool | None = None
    task_time_seconds: float | None = Field(default=None, ge=0.0)
    returned_for_second_question: bool | None = None


def load_questions(directory: Path = QUESTION_DIR) -> GoldenQuestionSet:
    """Load every YAML question file deterministically and validate it."""

    questions: list[GoldenQuestion] = []
    paths = sorted((*directory.glob("*.yaml"), *directory.glob("*.yml")))
    if not paths:
        raise ValueError(f"no golden question files found in {directory}")
    for path in paths:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        question_file = GoldenQuestionFile.model_validate(raw)
        questions.extend(question_file.questions)
    return GoldenQuestionSet(questions=questions)
