"""Cost accounting for the cEDH gateway.

Writes to the same ``cost_log`` table the Anthropic client writes to, and reads
the same trailing-30-day ceiling. That sharing is the point: a per-provider
ledger with a per-provider ceiling is two soft limits, not one hard stop.

Provider-specific detail that ``cost_log`` has no column for — provider name,
prompt version, reasoning tokens, latency, validation failures, retries — is
recorded in the existing ``metadata`` JSON column, so no migration is needed
and the admin analytics queries keep working unchanged.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from sabermetrics.cedh.model_gateway import UsageRecord
from sabermetrics.cedh.settings import ModelPricing
from sabermetrics.errors import LLMCostCeilingExceeded

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data/sabermetrics.db")


def estimate_cost(
    pricing: ModelPricing,
    *,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
) -> float:
    """Compute USD cost from token counts and configured prices.

    Reasoning tokens are billed at ``pricing.reasoning`` when the provider
    prices them separately, and at the output rate otherwise.

    ``cached_input_tokens`` is treated as a subset of ``input_tokens``, matching
    the OpenAI-compatible ``prompt_tokens_details.cached_tokens`` convention;
    the uncached remainder is floored at zero so a provider that reports them
    as disjoint cannot produce a negative charge.
    """
    uncached = max(input_tokens - cached_input_tokens, 0)
    reasoning_rate = (
        pricing.reasoning if pricing.reasoning is not None else pricing.output
    )
    total = (
        uncached * pricing.input
        + cached_input_tokens * pricing.cached_input
        + output_tokens * pricing.output
        + reasoning_tokens * reasoning_rate
    ) / 1_000_000
    return round(total, 6)


class CostLedger:
    """Reads the shared ceiling and records every gateway call."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path or DEFAULT_DB_PATH)

    # -- ceiling ----------------------------------------------------------

    def monthly_spend(self) -> float:
        """Total spend across every provider in the trailing 30 days.

        Returns 0.0 when the database or table is absent, so a fixture-only
        development run is not blocked by bookkeeping that has not been set up.
        """
        if not self.db_path.exists():
            return 0.0
        conn = sqlite3.connect(str(self.db_path))
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_log "
                "WHERE timestamp >= datetime('now', '-30 days')"
            ).fetchone()
            return float(row[0] or 0.0)
        except sqlite3.OperationalError:
            return 0.0
        finally:
            conn.close()

    def check_ceiling(self, ceiling_usd: float) -> None:
        """Raise if the trailing-30-day spend has reached the ceiling.

        Raises:
            LLMCostCeilingExceeded: The global hard stop has been hit.
        """
        spend = self.monthly_spend()
        if spend >= ceiling_usd:
            raise LLMCostCeilingExceeded(
                f"Monthly spend ${spend:.2f} exceeds ceiling " f"${ceiling_usd:.2f}"
            )

    # -- recording --------------------------------------------------------

    def record(
        self,
        usage: UsageRecord,
        *,
        user_id: str | None = None,
        deck_id: str | None = None,
    ) -> None:
        """Write one usage row. Never raises — accounting must not fail a build.

        A ledger write that takes down a generation converts a bookkeeping
        problem into a product outage, so failures are logged loudly and
        swallowed. The ceiling check above is the mechanism that bounds cost;
        this is the record of it.
        """
        if not self.db_path.exists():
            logger.warning(
                "cost ledger: %s does not exist, dropping usage row for %s",
                self.db_path,
                usage.call_type,
            )
            return
        metadata = json.dumps(
            {
                "provider": usage.provider,
                "prompt_version": usage.prompt_version,
                "mode": usage.mode,
                "reasoning_tokens": usage.reasoning_tokens,
                "latency_ms": usage.latency_ms,
                "validation_failures": usage.validation_failures,
                "retries": usage.retries,
                "evidence_hash": usage.evidence_hash,
                "cache_hit": usage.cache_hit,
            },
            sort_keys=True,
        )
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "INSERT INTO cost_log (call_type, model, input_tokens, "
                "cached_input_tokens, output_tokens, cost_usd, request_id, "
                "user_id, deck_id, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    usage.call_type,
                    usage.model_id,
                    usage.input_tokens,
                    usage.cached_input_tokens,
                    usage.output_tokens + usage.reasoning_tokens,
                    usage.cost_usd,
                    usage.request_id,
                    user_id,
                    deck_id,
                    metadata,
                ),
            )
            conn.commit()
        except sqlite3.Error:
            logger.exception("cost ledger: failed to record %s", usage.call_type)
        finally:
            conn.close()
