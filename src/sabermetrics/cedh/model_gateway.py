"""Provider-neutral structured generation.

The cEDH path never imports a vendor SDK. It builds a
:class:`StructuredRequest` naming a Pydantic response model and hands it to a
:class:`ModelGateway`; an adapter turns that into whatever the provider wants.
Swapping providers is an adapter, not a diff across the generation path.

Three rules hold for every gateway implementation:

* **Structured output is required for every machine-used response.** The
  gateway returns a validated Pydantic instance or raises. There is no path
  that returns free text to a caller that will parse it.
* **Validation failure is never silently repaired.** A configurable number of
  repair attempts may be made, each counted; when they are exhausted the call
  raises :class:`~sabermetrics.cedh.errors.ModelValidationError`.
* **Every call is recorded**, whether it succeeded or not — model id, provider,
  prompt version, tokens including reasoning tokens, latency, validation
  failures, retries and estimated cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ReasoningMode(str, Enum):
    """How much reasoning a call is allowed to spend.

    The mapping from mode to provider parameters is configuration
    (``config/cedh.yaml``), not code, because it is provider-specific and
    changes without the calling code changing.
    """

    #: Non-thinking / lowest supported mode. Request classification and other
    #: mechanical normalisation. Never used for a judgement call.
    CLASSIFY = "classify"
    #: Ordinary reasoning. Explanations and summaries shown to a person.
    EXPLAIN = "explain"
    #: Elevated reasoning. Reserved for decisions documented as ambiguous;
    #: :attr:`StructuredRequest.ambiguity_note` is required to use it.
    ADJUDICATE = "adjudicate"


@dataclass(frozen=True, slots=True)
class StructuredRequest(Generic[T]):
    """One structured generation call.

    Attributes:
        prompt_version: Version of the prompt template. Recorded on the usage
            row and mixed into the cache key, so a prompt edit invalidates
            cached results instead of silently reusing them.
        system: System instruction.
        user: The user-turn content.
        schema: Pydantic model the response must validate against.
        mode: Reasoning mode.
        call_type: Label for the cost ledger, e.g. ``"cedh:intent"``.
        max_output_tokens: Hard output bound.
        temperature: Sampling temperature; defaults to deterministic.
        evidence_hash: Hash of the evidence package this call was built from,
            for cache keying and provenance. Empty when no evidence was sent.
        ambiguity_note: Why this decision is ambiguous. Required for
            :attr:`ReasoningMode.ADJUDICATE`; a mode that costs more has to say
            what bought it.
        metadata: Free-form context recorded alongside the usage row.
    """

    prompt_version: str
    system: str
    user: str
    schema: type[T]
    mode: ReasoningMode = ReasoningMode.EXPLAIN
    call_type: str = "cedh:unknown"
    max_output_tokens: int = 1200
    temperature: float = 0.0
    evidence_hash: str = ""
    ambiguity_note: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode is ReasoningMode.ADJUDICATE and not self.ambiguity_note:
            raise ValueError(
                "ReasoningMode.ADJUDICATE requires ambiguity_note naming the "
                "documented ambiguous decision that justifies elevated "
                "reasoning"
            )

    @property
    def cache_key(self) -> str:
        """Stable key over evidence and prompt version.

        Deliberately does not include the model id: a model swap that produces
        a different explanation of the same evidence is a change the operator
        makes on purpose, and pinning the cache to the model would hide it.
        """
        import hashlib

        digest = hashlib.sha256()
        for part in (
            self.prompt_version,
            self.call_type,
            self.mode.value,
            self.evidence_hash,
            self.system,
            self.user,
        ):
            digest.update(part.encode("utf-8"))
            digest.update(b"\x00")
        return digest.hexdigest()


class UsageRecord(BaseModel):
    """What one call cost and how it behaved. Written to the cost ledger."""

    model_id: str
    provider: str
    prompt_version: str
    call_type: str
    mode: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: int = 0
    validation_failures: int = 0
    retries: int = 0
    cost_usd: float = 0.0
    request_id: str = ""
    evidence_hash: str = ""
    cache_hit: bool = False

    @property
    def is_free(self) -> bool:
        return self.cost_usd == 0.0


@dataclass(frozen=True, slots=True)
class GenerationResult(Generic[T]):
    """A validated response plus the usage it cost."""

    value: T
    usage: UsageRecord


@runtime_checkable
class ModelGateway(Protocol):
    """Provider-neutral structured generation."""

    @property
    def provider(self) -> str:
        """Provider identifier recorded on every usage row."""
        ...

    @property
    def model_id(self) -> str:
        """Pinned model identifier recorded on every usage row."""
        ...

    def generate(self, request: StructuredRequest[T]) -> GenerationResult[T]:
        """Run one structured call and return a validated response.

        Raises:
            ModelValidationError: The response never validated.
            ModelProviderError: The provider failed transiently.
            LLMCostCeilingExceeded: The monthly ceiling is already reached.
        """
        ...


def json_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """Return a strict JSON Schema for ``model``.

    Providers that support strict structured output reject schemas with open
    objects, so every object gets ``additionalProperties: false`` and lists
    every property as required — Pydantic's own output leaves optional fields
    out of ``required``, which strict mode will not accept.
    """
    schema = model.model_json_schema()
    _tighten(schema)
    return schema


def _tighten(node: Any) -> None:
    """Recursively make every object in a JSON Schema strict, in place."""
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
        for value in node.values():
            _tighten(value)
    elif isinstance(node, list):
        for item in node:
            _tighten(item)
