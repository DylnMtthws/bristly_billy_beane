"""Run the Research Assistant's correctness or usefulness scorecard."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from sabermetrics.assistant.eval.models import (
    CorrectnessObservation,
    HumanReview,
    load_questions,
)
from sabermetrics.assistant.eval.scoring import (
    correctness_scorecard,
    usefulness_scorecard,
)

T = TypeVar("T", bound=BaseModel)


def _load_jsonl(path: Path | None, model: type[T]) -> list[T]:
    if path is None:
        return []
    rows: list[T] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            rows.append(model.model_validate_json(line))
        except Exception as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("substrate", "end-to-end", "usefulness"),
        default="substrate",
    )
    parser.add_argument(
        "--input", type=Path, help="optional JSONL observations/reviews"
    )
    parser.add_argument("--questions", type=Path, help="override questions directory")
    args = parser.parse_args()

    if args.mode == "usefulness":
        scorecard = usefulness_scorecard(_load_jsonl(args.input, HumanReview))
    else:
        questions = (
            load_questions(args.questions) if args.questions else load_questions()
        )
        scorecard = correctness_scorecard(
            questions,
            _load_jsonl(args.input, CorrectnessObservation),
            mode=args.mode,
        )
    print(json.dumps(scorecard, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
