"""Model-ID and pricing validation (Option A DoD criterion 1).

These tests need neither ANTHROPIC_API_KEY nor the database — they exercise the
static model tables and the boot-time validation helpers directly.
"""

import pytest

from sabermetrics.config import load_settings
from sabermetrics.errors import FatalError
from sabermetrics.reasoning.client import (
    ALLOWED_MODELS,
    KNOWN_RETIRED_MODELS,
    MODEL_PRICING,
    validate_configured_models,
    validate_models,
)


def test_static_tables_are_consistent() -> None:
    """Every allowed model has pricing and none is retired."""
    validate_models()  # raises FatalError on any inconsistency
    assert ALLOWED_MODELS <= set(MODEL_PRICING)
    assert not (ALLOWED_MODELS & KNOWN_RETIRED_MODELS)


def test_every_configured_model_is_allowed() -> None:
    """The DoD check: models named in settings.yaml are usable."""
    llm = load_settings().llm
    configured = {
        "profile_model": llm.profile_model,
        "fit_model": llm.fit_model,
        "synthesis_model": llm.synthesis_model,
        "refresh_model": llm.refresh_model,
        "template_model": llm.template_model,
    }
    validate_configured_models(configured)  # raises if any is unusable
    for name, model in configured.items():
        assert model in ALLOWED_MODELS, f"llm.{name}={model} not allowed"


def test_configured_retired_model_is_rejected() -> None:
    """A retired ID in config fails loud rather than 404-ing at call time."""
    with pytest.raises(FatalError, match="retired"):
        validate_configured_models({"profile_model": "claude-3-opus-20240229"})


def test_configured_unknown_model_is_rejected() -> None:
    """An ID with no pricing/allow entry fails loud."""
    with pytest.raises(FatalError, match="not in ALLOWED_MODELS"):
        validate_configured_models({"fit_model": "claude-made-up-9"})


def test_pricing_matches_catalog() -> None:
    """Regression guard for the mispriced cost ledger (haiku/opus were wrong).

    Cached-input must be ~0.1x input, and output rates must match the catalog.
    """
    haiku = MODEL_PRICING["claude-haiku-4-5"]
    assert haiku["input"] == 1.00 and haiku["output"] == 5.00
    opus = MODEL_PRICING["claude-opus-4-6"]
    assert opus["input"] == 5.00 and opus["output"] == 25.00
    for model, p in MODEL_PRICING.items():
        assert abs(p["cached_input"] - p["input"] * 0.1) < 1e-9, model
