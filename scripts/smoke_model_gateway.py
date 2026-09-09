"""D0 acceptance — verify one live structured model call end to end.

Run from the repository root, against a real credential::

    HF_TOKEN='hf_…' python scripts/smoke_model_gateway.py
    HF_TOKEN='hf_…' python scripts/smoke_model_gateway.py --db /data/sabermetrics.db

What it proves, in the order D0's acceptance criteria state them:

1. The credential named by ``model.credential_env`` is present.
2. The gateway constructs, which runs ``_assert_pinned`` — so a
   ``:cheapest``-style dynamic route fails here rather than silently making the
   recorded model id a guess.
3. One live call returns a **validated Pydantic instance**. The gateway raises
   rather than partially accepting, so reaching the assertion means structured
   output worked.
4. A ``cost_log`` row lands with provider, model id, prompt version and non-zero
   tokens.
5. The monthly ceiling **raises** rather than being logged and ignored.

It is deliberately the cheapest possible real call: ``CLASSIFY`` mode, a
two-field schema, a handful of output tokens. Running it costs a fraction of a
cent and is the only way to replace §11's unvalidated arithmetic with a measured
number.

**This writes one row to the cost ledger.** Point ``--db`` at a scratch database
unless you specifically want the production ledger to record the check.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pydantic import BaseModel, Field

from sabermetrics.cedh.cost_ledger import CostLedger
from sabermetrics.cedh.errors import CedhError
from sabermetrics.cedh.model_gateway import ReasoningMode, StructuredRequest
from sabermetrics.cedh.settings import load_cedh_settings

# One ceiling across both the legacy Anthropic path and this gateway
# (ADR-024), so the exception lives in the shared module, not cedh's.
from sabermetrics.errors import LLMCostCeilingExceeded


#: The smallest schema that still exercises structured output: one enum-ish
#: string and one bounded int. A single free-text field would validate even if
#: the provider ignored the schema entirely.
class SmokeAnswer(BaseModel):
    """Deliberately trivial. The content does not matter; the shape does."""

    colour: str = Field(description="One of: white, blue, black, red, green")
    confidence: int = Field(ge=0, le=10)


SYSTEM = (
    "You answer with structured data only. You are being used to verify a "
    "provider integration, not to reason about anything."
)
USER = (
    "Name the colour of the Magic: the Gathering mana symbol {U} and give a "
    "confidence from 0 to 10."
)


def _fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/sabermetrics.db"),
        help="Cost ledger to write the usage row to.",
    )
    parser.add_argument(
        "--skip-ceiling-check",
        action="store_true",
        help="Skip step 5 (the ceiling check makes no model call).",
    )
    args = parser.parse_args()

    settings = load_cedh_settings()
    env_name = settings.model.credential_env

    # -- 1. credential ----------------------------------------------------
    if not os.environ.get(env_name, "").strip():
        return _fail(
            f"{env_name} is not set. config/cedh.yaml names it as "
            f"model.credential_env. Set it, or change credential_env if the "
            f"provider changed."
        )
    print(f"1. credential   {env_name} is set")
    print(f"   provider     {settings.model.provider}")
    print(f"   base_url     {settings.model.base_url}")
    print(f"   model_id     {settings.model.model_id}")

    if not args.db.exists():
        return _fail(
            f"{args.db} does not exist, so the ledger write in step 4 would be "
            f"dropped with a warning. Pass --db, or run scripts/setup_db.py."
        )

    # -- 2. gateway construction runs the provider pin assertion -----------
    from sabermetrics.cedh.provider_deepseek import build_gateway

    try:
        gateway = build_gateway(settings, db_path=str(args.db))
    except CedhError as exc:
        return _fail(f"gateway did not construct: {type(exc).__name__}: {exc}")
    print(f"2. pin          ok — {gateway.provider}:{gateway.model_id}")

    # -- 3. one live structured call --------------------------------------
    request = StructuredRequest(
        prompt_version="smoke.v1",
        system=SYSTEM,
        user=USER,
        schema=SmokeAnswer,
        mode=ReasoningMode.CLASSIFY,
        call_type="cedh:smoke",
        max_output_tokens=128,
    )
    before = _ledger_count(args.db)
    try:
        result = gateway.generate(request)
    except LLMCostCeilingExceeded as exc:
        return _fail(
            f"the monthly ceiling is already reached, so no call was made: "
            f"{exc}. That is the ceiling working; raise it or wait to smoke."
        )
    except CedhError as exc:
        return _fail(f"live call failed: {type(exc).__name__}: {exc}")

    assert isinstance(result.value, SmokeAnswer)
    usage = result.usage
    print(f"3. structured   ok — validated {type(result.value).__name__}")
    print(
        f"   answer       colour={result.value.colour!r} confidence={result.value.confidence}"
    )
    print(
        f"   tokens       in={usage.input_tokens} cached={usage.cached_input_tokens} "
        f"out={usage.output_tokens} reasoning={usage.reasoning_tokens}"
    )
    print(f"   cost         ${usage.cost_usd:.6f}   latency {usage.latency_ms} ms")
    print(
        f"   retries      {usage.retries}   validation_failures {usage.validation_failures}"
    )

    if usage.input_tokens == 0 and usage.output_tokens == 0:
        return _fail(
            "the provider reported zero tokens. The call succeeded but the "
            "usage accounting did not, so every cost figure downstream would "
            "read zero."
        )

    # -- 4. the ledger row ------------------------------------------------
    after = _ledger_count(args.db)
    if after != before + 1:
        return _fail(
            f"expected one new cost_log row in {args.db}, saw {after - before}. "
            f"CostLedger.record swallows failures by design, so a missing row "
            f"here is silent in production."
        )
    row = _latest_row(args.db)
    print(f"4. ledger       ok — {args.db} row id {row['id']}")
    print(f"   recorded     call_type={row['call_type']!r} model={row['model']!r}")
    print(f"   cost_usd     {row['cost_usd']}")

    # -- 5. the ceiling raises, rather than being logged and ignored -------
    if not args.skip_ceiling_check:
        ledger = CostLedger(args.db)
        spend = ledger.monthly_spend()
        try:
            ledger.check_ceiling(0.0)
        except LLMCostCeilingExceeded as exc:
            print(f"5. ceiling      ok — raises when exceeded ({exc})")
        else:
            return _fail(
                "check_ceiling(0.0) did not raise even though this month's "
                f"spend is ${spend:.4f}. The ceiling is advisory, not a hard "
                "stop, which violates the cost charter."
            )

    print("\nD0 acceptance: PASS")
    print(
        "Remaining for D0: confirm settings.llm.monthly_cost_ceiling_usd is the "
        "value you intend (currently read from config/settings.yaml), and that "
        "a provider outage renders as a stated absence rather than silence."
    )
    return 0


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _ledger_count(db: Path) -> int:
    with _connect(db) as conn:
        return int(conn.execute("SELECT count(*) FROM cost_log").fetchone()[0])


def _latest_row(db: Path) -> sqlite3.Row:
    with _connect(db) as conn:
        return conn.execute(
            "SELECT id, call_type, model, cost_usd FROM cost_log "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()


if __name__ == "__main__":
    raise SystemExit(main())
