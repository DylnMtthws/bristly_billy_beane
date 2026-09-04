"""DeepSeek-on-DeepInfra adapter, spoken over the OpenAI-compatible API.

Uses ``httpx`` directly rather than a vendor SDK: the wire format is the
contract, and one HTTP call is less coupling than a client library whose types
would leak into the gateway interface.

Defaults, all overridable by config or environment:

===================  ==========================================
Base URL             ``https://router.huggingface.co/v1``
Model                ``deepseek-ai/DeepSeek-V4-Flash:deepinfra``
Credential           ``HF_TOKEN``
===================  ==========================================

The provider suffix on the model id is a **pin**. A dynamic ``:cheapest`` route
would mean the model id recorded on every usage row is a guess about which
model answered, which makes the cost and quality record unfalsifiable; the
adapter refuses to start with one unless ``require_pinned_provider`` is off.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Self, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from sabermetrics.cedh.cost_ledger import CostLedger, estimate_cost
from sabermetrics.cedh.errors import (
    ModelConfigurationError,
    ModelProviderError,
    ModelValidationError,
)
from sabermetrics.cedh.model_gateway import (
    GenerationResult,
    StructuredRequest,
    UsageRecord,
    json_schema_for,
)
from sabermetrics.cedh.settings import CedhSettings, load_cedh_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

#: Model-id suffixes that route dynamically instead of pinning a provider.
UNPINNED_SUFFIXES = (":cheapest", ":fastest", ":auto")

_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def _strip_fences(text: str) -> str:
    """Remove a ```json fence if the model wrapped its JSON in one."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    if body.rstrip().endswith("```"):
        body = body.rstrip()[: -len("```")]
    return body.strip()


class DeepSeekGateway:
    """A :class:`~sabermetrics.cedh.model_gateway.ModelGateway` over DeepInfra.

    Args:
        settings: cEDH settings; loaded from ``config/cedh.yaml`` when omitted.
        db_path: SQLite database for the shared cost ledger.
        client: Injected ``httpx.Client``, for smoke tests against a stub.
        user_id: Attribution for ledger rows.
        deck_id: Attribution for ledger rows.
    """

    def __init__(
        self,
        settings: CedhSettings | None = None,
        *,
        db_path: str | None = None,
        client: httpx.Client | None = None,
        user_id: str | None = None,
        deck_id: str | None = None,
    ) -> None:
        self._settings = settings or load_cedh_settings()
        model = self._settings.model
        self._model_id = model.model_id
        self._provider = model.provider
        self._ledger = CostLedger(db_path)
        self._user_id = user_id
        self._deck_id = deck_id

        if model.require_pinned_provider:
            self._assert_pinned(self._model_id)

        token = os.environ.get(model.credential_env, "").strip()
        if not token:
            raise ModelConfigurationError(
                f"{model.credential_env} is not set. The cEDH model gateway "
                f"needs a credential for {model.base_url}."
            )
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=model.base_url.rstrip("/"),
            timeout=model.timeout_seconds,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )

    @staticmethod
    def _assert_pinned(model_id: str) -> None:
        lowered = model_id.lower()
        if any(lowered.endswith(suffix) for suffix in UNPINNED_SUFFIXES):
            raise ModelConfigurationError(
                f"model id {model_id!r} routes dynamically. Production pins a "
                "provider so the recorded model id is a fact rather than a "
                "guess; set require_pinned_provider: false to override."
            )
        if ":" not in model_id:
            raise ModelConfigurationError(
                f"model id {model_id!r} names no provider. Pin one, e.g. "
                "'deepseek-ai/DeepSeek-V4-Flash:deepinfra'."
            )

    # -- ModelGateway -----------------------------------------------------

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model_id(self) -> str:
        return self._model_id

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def generate(self, request: StructuredRequest[T]) -> GenerationResult[T]:
        """Run one structured call, validating before returning.

        Raises:
            LLMCostCeilingExceeded: The global monthly ceiling is reached.
            ModelProviderError: The provider failed after transport retries.
            ModelValidationError: The response never validated against the
                schema, after the configured repair attempts.
        """
        self._ledger.check_ceiling(self._settings.monthly_cost_ceiling_usd)

        model = self._settings.model
        profile = model.mode_profile(request.mode)
        body = self._body(request, profile)

        messages: list[dict[str, Any]] = list(body["messages"])
        totals = {
            "input": 0,
            "cached": 0,
            "output": 0,
            "reasoning": 0,
            "retries": 0,
            "validation_failures": 0,
        }
        started = time.monotonic()
        request_id = ""
        last_error = ""

        for attempt in range(model.max_validation_retries + 1):
            payload = dict(body, messages=messages)
            response, transport_retries = self._post(payload)
            totals["retries"] += transport_retries
            usage = response.get("usage") or {}
            totals["input"] += int(usage.get("prompt_tokens") or 0)
            totals["cached"] += int(
                (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
            )
            completion = int(usage.get("completion_tokens") or 0)
            reasoning = int(
                (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
                or 0
            )
            # completion_tokens includes reasoning tokens in the OpenAI-
            # compatible schema; splitting them keeps the two priceable
            # separately without double-charging the total.
            totals["output"] += max(completion - reasoning, 0)
            totals["reasoning"] += reasoning
            request_id = response.get("id") or request_id

            content = self._content(response)
            try:
                value = request.schema.model_validate_json(_strip_fences(content))
            except (ValidationError, ValueError) as exc:
                totals["validation_failures"] += 1
                last_error = str(exc)
                logger.warning(
                    "cEDH gateway: %s response failed validation "
                    "(attempt %d/%d): %s",
                    request.call_type,
                    attempt + 1,
                    model.max_validation_retries + 1,
                    last_error,
                )
                if attempt >= model.max_validation_retries:
                    break
                messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "That response did not validate against the "
                            "required schema. Errors:\n"
                            f"{last_error}\n\n"
                            "Reply with JSON matching the schema exactly. No "
                            "prose, no code fence."
                        ),
                    },
                ]
                continue

            record = self._usage(request, totals, started, request_id)
            self._ledger.record(record, user_id=self._user_id, deck_id=self._deck_id)
            return GenerationResult(value=value, usage=record)

        record = self._usage(request, totals, started, request_id)
        self._ledger.record(record, user_id=self._user_id, deck_id=self._deck_id)
        raise ModelValidationError(
            f"{request.call_type}: response never validated against "
            f"{request.schema.__name__} after "
            f"{totals['validation_failures']} attempt(s). Last error: "
            f"{last_error}"
        )

    # -- internals --------------------------------------------------------

    def _body(self, request: StructuredRequest[T], profile: Any) -> dict[str, Any]:
        max_tokens = profile.max_output_tokens or request.max_output_tokens
        temperature = (
            profile.temperature
            if profile.temperature is not None
            else request.temperature
        )
        body: dict[str, Any] = {
            "model": self._model_id,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema.__name__,
                    "strict": True,
                    "schema": json_schema_for(request.schema),
                },
            },
        }
        body.update(profile.extra_body)
        return body

    def _post(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """POST with bounded backoff. Returns the body and the retry count."""
        model = self._settings.model
        last: Exception | None = None
        for attempt in range(model.max_retries + 1):
            try:
                response = self._client.post("/chat/completions", json=payload)
                if response.status_code in _RETRYABLE_STATUS:
                    last = ModelProviderError(
                        f"provider returned {response.status_code}: "
                        f"{response.text[:400]}"
                    )
                elif response.status_code >= 400:
                    raise ModelProviderError(
                        f"provider returned {response.status_code}: "
                        f"{response.text[:400]}"
                    )
                else:
                    return response.json(), attempt
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
            if attempt < model.max_retries:
                time.sleep(2**attempt)
        raise ModelProviderError(
            f"model provider call failed after {model.max_retries + 1} "
            f"attempts: {last}"
        )

    @staticmethod
    def _content(response: dict[str, Any]) -> str:
        choices = response.get("choices") or []
        if not choices:
            raise ModelProviderError(
                "provider returned no choices: " f"{json.dumps(response)[:400]}"
            )
        message = choices[0].get("message") or {}
        return str(message.get("content") or "")

    def _usage(
        self,
        request: StructuredRequest[T],
        totals: dict[str, int],
        started: float,
        request_id: str,
    ) -> UsageRecord:
        cost = estimate_cost(
            self._settings.model.pricing,
            input_tokens=totals["input"],
            cached_input_tokens=totals["cached"],
            output_tokens=totals["output"],
            reasoning_tokens=totals["reasoning"],
        )
        return UsageRecord(
            model_id=self._model_id,
            provider=self._provider,
            prompt_version=request.prompt_version,
            call_type=request.call_type,
            mode=request.mode.value,
            input_tokens=totals["input"],
            cached_input_tokens=totals["cached"],
            output_tokens=totals["output"],
            reasoning_tokens=totals["reasoning"],
            latency_ms=int((time.monotonic() - started) * 1000),
            validation_failures=totals["validation_failures"],
            retries=totals["retries"],
            cost_usd=cost,
            request_id=request_id,
            evidence_hash=request.evidence_hash,
        )


def build_gateway(
    settings: CedhSettings | None = None,
    *,
    db_path: str | None = None,
    user_id: str | None = None,
    deck_id: str | None = None,
) -> DeepSeekGateway:
    """Construct the configured production gateway.

    A factory rather than a singleton: attribution (``user_id``/``deck_id``) is
    per-request, and a process-wide instance would either lose it or need a
    context variable to carry it.
    """
    return DeepSeekGateway(settings, db_path=db_path, user_id=user_id, deck_id=deck_id)
