"""A scripted gateway for tests and offline development.

Returns pre-built responses keyed by ``call_type``, records the requests it was
given, and produces a plausible :class:`UsageRecord` so cost accounting and
provenance display are exercised without a provider.

It validates the scripted value against the request's schema on the way out.
That matters: a fake that returns whatever it was handed will happily let a
test pass against a response the real provider's strict schema would reject.
"""

from __future__ import annotations

import time
from typing import Any, TypeVar

from pydantic import BaseModel

from sabermetrics.cedh.cost_ledger import CostLedger, estimate_cost
from sabermetrics.cedh.errors import ModelValidationError
from sabermetrics.cedh.model_gateway import (
    GenerationResult,
    StructuredRequest,
    UsageRecord,
)
from sabermetrics.cedh.settings import ModelPricing

T = TypeVar("T", bound=BaseModel)

#: A scripted answer: either a model instance, a dict, or a callable taking the
#: request and returning one of those.
Script = Any


class FixtureGateway:
    """A :class:`~sabermetrics.cedh.model_gateway.ModelGateway` with no network.

    Args:
        scripts: ``call_type`` -> scripted answer.
        pricing: Prices used to compute a synthetic cost.
        ledger: Optional real ledger, so ceiling behaviour can be tested.
        fail_call_types: Call types that should raise as if validation failed.
    """

    def __init__(
        self,
        scripts: dict[str, Script] | None = None,
        *,
        pricing: ModelPricing | None = None,
        ledger: CostLedger | None = None,
        fail_call_types: frozenset[str] = frozenset(),
        model_id: str = "fixture/deepseek-v4-flash:fixture",
        provider: str = "fixture",
    ) -> None:
        self.scripts: dict[str, Script] = scripts or {}
        self.requests: list[StructuredRequest[Any]] = []
        self.usages: list[UsageRecord] = []
        self._pricing = pricing or ModelPricing(
            input=0.25, cached_input=0.025, output=0.85
        )
        self._ledger = ledger
        self._fail = fail_call_types
        self._model_id = model_id
        self._provider = provider

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model_id(self) -> str:
        return self._model_id

    def generate(self, request: StructuredRequest[T]) -> GenerationResult[T]:
        self.requests.append(request)
        started = time.monotonic()

        if request.call_type in self._fail:
            raise ModelValidationError(
                f"{request.call_type}: scripted validation failure"
            )

        script = self.scripts.get(request.call_type)
        if script is None:
            raise KeyError(
                f"FixtureGateway has no script for call_type "
                f"{request.call_type!r}; scripted: {sorted(self.scripts)}"
            )
        if callable(script):
            script = script(request)

        # Validate through the request's own schema, so a scripted answer the
        # real provider's strict schema would reject fails here too.
        value = (
            script
            if isinstance(script, request.schema)
            else request.schema.model_validate(script)
        )

        usage = UsageRecord(
            model_id=self._model_id,
            provider=self._provider,
            prompt_version=request.prompt_version,
            call_type=request.call_type,
            mode=request.mode.value,
            input_tokens=len(request.system) + len(request.user),
            output_tokens=len(value.model_dump_json()),
            latency_ms=int((time.monotonic() - started) * 1000),
            evidence_hash=request.evidence_hash,
        )
        usage = usage.model_copy(
            update={
                "cost_usd": estimate_cost(
                    self._pricing,
                    input_tokens=usage.input_tokens,
                    cached_input_tokens=0,
                    output_tokens=usage.output_tokens,
                    reasoning_tokens=0,
                )
            }
        )
        self.usages.append(usage)
        if self._ledger is not None:
            self._ledger.record(usage)
        return GenerationResult(value=value, usage=usage)
