"""Configuration for the cEDH Deck Lab.

Non-secret settings live in ``config/cedh.yaml``; secrets stay in the
environment. Pricing in particular is configuration rather than constants
scattered through the code, so a provider price change is a YAML edit.

The monthly cost ceiling is deliberately **not** redefined here. It is read
from the existing ``llm.monthly_cost_ceiling_usd`` in ``config/settings.yaml``
so that one ceiling bounds spend across both the legacy Anthropic path and the
cEDH gateway, which is what a global hard stop has to mean.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from sabermetrics.cedh.model_gateway import ReasoningMode


class ModelPricing(BaseModel):
    """USD per 1M tokens. Reasoning tokens are billed as output by default."""

    input: float = 0.0
    cached_input: float = 0.0
    output: float = 0.0
    #: Set only if the provider bills reasoning tokens at a distinct rate;
    #: otherwise they are priced as output.
    reasoning: float | None = None


class ModeProfile(BaseModel):
    """Provider parameters for one reasoning mode.

    ``extra_body`` is passed through to the provider verbatim. Keeping it
    opaque is what makes the gateway provider-neutral: the mapping from
    "lowest supported reasoning" to a vendor's parameter name is config.
    """

    extra_body: dict[str, Any] = Field(default_factory=dict)
    max_output_tokens: int | None = None
    temperature: float | None = None


class ModelSettings(BaseModel):
    """Which model answers, how it is addressed, and what it costs."""

    provider: str = "deepinfra"
    base_url: str = "https://router.huggingface.co/v1"
    model_id: str = "deepseek-ai/DeepSeek-V4-Flash:deepinfra"
    credential_env: str = "HF_TOKEN"
    timeout_seconds: float = 60.0
    #: Transport retries on transient provider failures.
    max_retries: int = 2
    #: Repair attempts after a response fails schema validation.
    max_validation_retries: int = 1
    #: Reject a model id that routes dynamically (``:cheapest``) or names no
    #: provider at all. Production pins the provider; a floating route makes
    #: the recorded model id a guess.
    require_pinned_provider: bool = True
    pricing: ModelPricing = ModelPricing()
    modes: dict[str, ModeProfile] = Field(default_factory=dict)

    def mode_profile(self, mode: ReasoningMode) -> ModeProfile:
        return self.modes.get(mode.value, ModeProfile())


class EvidenceSettings(BaseModel):
    """Bounds on what may be sent to the model.

    The whole card corpus and an unbounded deck corpus are both out of the
    question; these are the numbers that make that a mechanism.
    """

    max_chunks: int = 24
    max_chars_per_chunk: int = 1200
    max_total_chars: int = 16000
    cache_ttl_hours: int = 168


class SimulatorSettings(BaseModel):
    """How, or whether, to reach the simulator."""

    #: ``fixture`` (default), ``subprocess``, ``http``, or ``off``.
    mode: str = "fixture"
    url: str = ""
    binary_path: str = ""
    cards_path: str = "fixtures/cedh/cards.json"
    fixture_dir: str = "fixtures/cedh/simulation"
    games: int = 20000
    objective_turn: int = 3
    timeout_seconds: float = 330.0


class MetaSettings(BaseModel):
    """Defaults for the metagame window."""

    window_days: int = 180
    min_event_size: int = 32


class CedhSettings(BaseModel):
    """Top-level cEDH configuration."""

    model: ModelSettings = ModelSettings()
    evidence: EvidenceSettings = EvidenceSettings()
    simulator: SimulatorSettings = SimulatorSettings()
    meta: MetaSettings = MetaSettings()
    packs_dir: str = "config/cedh_packs"
    fixtures_dir: str = "fixtures/cedh"

    @property
    def monthly_cost_ceiling_usd(self) -> float:
        """The single global ceiling, shared with the legacy Anthropic path."""
        from sabermetrics.config import settings as legacy_settings

        return legacy_settings.llm.monthly_cost_ceiling_usd


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _config_path() -> Path:
    root = _project_root() / "config" / "cedh.yaml"
    if root.exists():
        return root
    return Path.cwd() / "config" / "cedh.yaml"


def load_cedh_settings(path: Path | None = None) -> CedhSettings:
    """Load cEDH settings from YAML, with env overrides for the model.

    Environment always wins over YAML for values an operator changes
    per deployment, so a staging box can be pointed at a different model
    without editing a file that is in git.

    Args:
        path: Explicit config path; auto-discovered when omitted.

    Returns:
        Validated settings, defaults filled in for anything absent.
    """
    path = path or _config_path()
    raw: dict[str, Any] = {}
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}

    settings = CedhSettings(**raw)

    overrides = {
        "base_url": os.environ.get("CEDH_MODEL_BASE_URL"),
        "model_id": os.environ.get("CEDH_MODEL_ID"),
        "provider": os.environ.get("CEDH_MODEL_PROVIDER"),
    }
    applied = {k: v for k, v in overrides.items() if v}
    if applied:
        settings = settings.model_copy(
            update={"model": settings.model.model_copy(update=applied)}
        )

    simulator_updates: dict[str, Any] = {}
    simulator_url = os.environ.get("CEDH_SIMULATOR_URL")
    if simulator_url:
        simulator_updates.update(mode="http", url=simulator_url)
    simulator_timeout = os.environ.get("CEDH_SIMULATOR_TIMEOUT")
    if simulator_timeout:
        simulator_updates["timeout_seconds"] = float(simulator_timeout)
    if simulator_updates:
        settings = settings.model_copy(
            update={
                "simulator": settings.simulator.model_copy(update=simulator_updates)
            }
        )
    return settings


#: Module-level singleton, mirroring ``sabermetrics.config.settings``.
cedh_settings = load_cedh_settings()
