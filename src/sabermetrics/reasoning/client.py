"""Anthropic API client wrapper (D5.1).

Singleton wrapper for all LLM calls. Handles:
- Prompt caching with cache_breakpoints
- Cost tracking to cost_log table
- Monthly cost ceiling enforcement
- Exponential backoff retry
- Model name validation
"""

import logging
import os
import sqlite3
import time
from pathlib import Path

import anthropic
from pydantic import BaseModel

from sabermetrics.errors import (
    FatalError,
    LLMCostCeilingExceeded,
    RecoverableError,
)

logger = logging.getLogger(__name__)

# ADR-011: allowed models. All three are current, active model IDs (verified
# against the Anthropic model catalog). Keep this set in sync with the models
# referenced in config/settings.yaml — validate_configured_models() enforces it.
ALLOWED_MODELS = {
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
}

# Model IDs that have been retired and will 404. Presence of any of these in
# ALLOWED_MODELS or config is a hard error — this is the "fail loud when a model
# goes stale" guard.
KNOWN_RETIRED_MODELS = {
    "claude-3-7-sonnet-20250219",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-sonnet-20240620",
    "claude-3-sonnet-20240229",
    "claude-2.1",
    "claude-2.0",
}

# Pricing per 1M tokens. Verified against the Anthropic model catalog:
#   Haiku 4.5  = $1.00 in / $5.00 out   (cached input ~0.1x)
#   Sonnet 4.6 = $3.00 in / $15.00 out
#   Opus 4.6   = $5.00 in / $25.00 out
# Cached-input rate is ~0.1x the input rate for every current model.
MODEL_PRICING = {
    "claude-haiku-4-5": {
        "input": 1.00,
        "cached_input": 0.10,
        "output": 5.00,
    },
    "claude-sonnet-4-6": {
        "input": 3.00,
        "cached_input": 0.30,
        "output": 15.00,
    },
    "claude-opus-4-6": {
        "input": 5.00,
        "cached_input": 0.50,
        "output": 25.00,
    },
}


def validate_models() -> None:
    """Validate the static model tables at import/boot time.

    Guarantees that every allowed model has a pricing entry and that no allowed
    model is a known-retired ID. Raises FatalError on any inconsistency so a
    stale table fails loud rather than silently mis-pricing or 404-ing later.

    Raises:
        FatalError: If ALLOWED_MODELS and MODEL_PRICING are inconsistent, or an
            allowed model is retired.
    """
    missing_pricing = ALLOWED_MODELS - set(MODEL_PRICING)
    if missing_pricing:
        raise FatalError(
            f"Allowed models missing from MODEL_PRICING: {sorted(missing_pricing)}"
        )
    retired = ALLOWED_MODELS & KNOWN_RETIRED_MODELS
    if retired:
        raise FatalError(
            f"Allowed models are retired (will 404): {sorted(retired)}"
        )


def validate_configured_models(configured: dict[str, str]) -> None:
    """Validate that every model named in config is usable.

    Args:
        configured: Mapping of setting name -> model ID (e.g. from LLMSettings).

    Raises:
        FatalError: If any configured model is not in ALLOWED_MODELS or is a
            known-retired ID.
    """
    for setting_name, model in configured.items():
        if model in KNOWN_RETIRED_MODELS:
            raise FatalError(
                f"Configured model '{model}' (llm.{setting_name}) is retired "
                f"and will 404. Update config/settings.yaml."
            )
        if model not in ALLOWED_MODELS:
            raise FatalError(
                f"Configured model '{model}' (llm.{setting_name}) is not in "
                f"ALLOWED_MODELS {sorted(ALLOWED_MODELS)}. Add it (with pricing) "
                f"or fix config/settings.yaml."
            )


# Validate the static model tables at import time (pure, no API key required).
validate_models()


class CallResult(BaseModel):
    """Result of an Anthropic API call."""

    content: str
    model: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: float
    request_id: str


class AnthropicClient:
    """Singleton wrapper for Anthropic API. All LLM calls MUST go through this.

    Usage:
        client = AnthropicClient.get_instance(db_path)
        result = client.call_with_cache(model="claude-haiku-4-5", ...)
    """

    _instance: "AnthropicClient | None" = None

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        # Fail loud at construction if the model tables or configured models
        # are inconsistent, rather than 404-ing on the first real API call.
        validate_models()
        from sabermetrics.config import settings

        validate_configured_models({
            "profile_model": settings.llm.profile_model,
            "fit_model": settings.llm.fit_model,
            "synthesis_model": settings.llm.synthesis_model,
            "refresh_model": settings.llm.refresh_model,
            "template_model": settings.llm.template_model,
        })
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise FatalError(
                "ANTHROPIC_API_KEY not set. Export it or add to .env"
            )
        self._client = anthropic.Anthropic(api_key=api_key)

    @classmethod
    def get_instance(cls, db_path: Path | None = None) -> "AnthropicClient":
        """Get or create the singleton client instance."""
        if cls._instance is None:
            if db_path is None:
                db_path = Path("data/sabermetrics.db")
            cls._instance = cls(db_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (for testing)."""
        cls._instance = None

    def call_with_cache(
        self,
        model: str,
        system: str,
        messages: list[dict],
        cache_breakpoints: list[int] | None = None,
        max_tokens: int = 4000,
        temperature: float = 0.0,
        call_type: str = "unknown",
    ) -> CallResult:
        """Make an Anthropic API call with prompt caching.

        Args:
            model: Model name (must be in ALLOWED_MODELS).
            system: System prompt.
            messages: List of message dicts with 'role' and 'content'.
            cache_breakpoints: Indices of messages to mark as cacheable.
            max_tokens: Maximum output tokens.
            temperature: Sampling temperature.
            call_type: Label for cost tracking (e.g. 'profile', 'fit').

        Returns:
            CallResult with response content and metadata.

        Raises:
            FatalError: Cost ceiling exceeded or invalid model.
            RecoverableError: Transient API failure after retries.
        """
        if model not in ALLOWED_MODELS:
            raise FatalError(f"Model '{model}' not in allowed models: {ALLOWED_MODELS}")

        # Check cost ceiling
        from sabermetrics.config import settings
        monthly_ceiling = settings.llm.monthly_cost_ceiling_usd
        current_spend = self.get_monthly_spend()
        if current_spend >= monthly_ceiling:
            raise LLMCostCeilingExceeded(
                f"Monthly spend ${current_spend:.2f} exceeds "
                f"ceiling ${monthly_ceiling:.2f}"
            )

        # Build system prompt with cache control
        system_content = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        # Build messages with cache breakpoints
        api_messages = []
        for i, msg in enumerate(messages):
            content = msg.get("content", "")
            if isinstance(content, str):
                content_blocks = [{"type": "text", "text": content}]
            else:
                content_blocks = content

            # Add cache control at breakpoints
            if cache_breakpoints and i in cache_breakpoints:
                if content_blocks:
                    content_blocks[-1]["cache_control"] = {"type": "ephemeral"}

            api_messages.append({
                "role": msg.get("role", "user"),
                "content": content_blocks,
            })

        # Retry with exponential backoff
        last_error = None
        for attempt in range(3):
            try:
                response = self._client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system_content,
                    messages=api_messages,
                )

                # Extract usage
                usage = response.usage
                input_tokens = usage.input_tokens
                output_tokens = usage.output_tokens
                cached_input = getattr(
                    usage, "cache_read_input_tokens", 0
                ) or 0

                # Compute cost
                cost = self.estimate_cost(
                    model, input_tokens, cached_input, output_tokens
                )

                # Extract content text
                content_text = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        content_text += block.text

                request_id = response.id or ""

                result = CallResult(
                    content=content_text,
                    model=model,
                    input_tokens=input_tokens,
                    cached_input_tokens=cached_input,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                    request_id=request_id,
                )

                # Log cost
                self._log_cost(result, call_type)

                logger.info(
                    "API call: model=%s type=%s input=%d cached=%d "
                    "output=%d cost=$%.4f",
                    model, call_type, input_tokens, cached_input,
                    output_tokens, cost,
                )

                return result

            except anthropic.RateLimitError as e:
                last_error = e
                wait = 2 ** attempt * 2
                logger.warning(
                    "Rate limited (attempt %d/3), waiting %ds: %s",
                    attempt + 1, wait, e,
                )
                time.sleep(wait)

            except anthropic.APIConnectionError as e:
                last_error = e
                wait = 2 ** attempt * 2
                logger.warning(
                    "Connection error (attempt %d/3), waiting %ds: %s",
                    attempt + 1, wait, e,
                )
                time.sleep(wait)

            except anthropic.APIStatusError as e:
                if e.status_code >= 500:
                    last_error = e
                    wait = 2 ** attempt * 2
                    logger.warning(
                        "Server error %d (attempt %d/3), waiting %ds",
                        e.status_code, attempt + 1, wait,
                    )
                    time.sleep(wait)
                else:
                    raise FatalError(f"API error: {e}") from e

        raise RecoverableError(
            f"API call failed after 3 retries: {last_error}"
        )

    def estimate_cost(
        self,
        model: str,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Compute cost in USD given token counts."""
        pricing = MODEL_PRICING.get(model, MODEL_PRICING["claude-haiku-4-5"])

        uncached_input = input_tokens - cached_input_tokens
        cost = (
            uncached_input * pricing["input"] / 1_000_000
            + cached_input_tokens * pricing["cached_input"] / 1_000_000
            + output_tokens * pricing["output"] / 1_000_000
        )
        return round(cost, 6)

    def get_monthly_spend(self) -> float:
        """Query cost_log for spend in last 30 days."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_log "
                "WHERE timestamp >= datetime('now', '-30 days')"
            )
            return cursor.fetchone()[0]
        finally:
            conn.close()

    def _log_cost(self, result: CallResult, call_type: str) -> None:
        """Log API call cost to the cost_log table."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "INSERT INTO cost_log "
                "(call_type, model, input_tokens, cached_input_tokens, "
                "output_tokens, cost_usd, request_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    call_type,
                    result.model,
                    result.input_tokens,
                    result.cached_input_tokens,
                    result.output_tokens,
                    result.cost_usd,
                    result.request_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
