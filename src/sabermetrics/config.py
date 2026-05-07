"""Configuration loader for Sabermetrics.

Loads settings from config/settings.yaml and exposes typed access.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class UserSettings(BaseModel):
    """User preference settings."""

    default_budget_usd: float = 200
    default_power_target: int = 3
    default_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "synergy": 0.35,
            "replacement_value": 0.25,
            "mana_efficiency": 0.25,
            "price_efficiency": 0.15,
        }
    )


class LLMSettings(BaseModel):
    """LLM model and cost settings."""

    profile_model: str = "claude-sonnet-4-6"
    fit_model: str = "claude-haiku-4-5"
    synthesis_model: str = "claude-sonnet-4-6"
    refresh_model: str = "claude-haiku-4-5"
    max_candidates_for_llm_fit: int = 50
    prompt_caching: bool = True
    monthly_cost_ceiling_usd: float = 5.0


class EmbeddingSettings(BaseModel):
    """Embedding model settings."""

    model: str = "sentence-transformers/all-MiniLM-L6-v2"
    device: str = "cpu"
    cache_dir: str = "./data/embedding_cache"


class PipelineSettings(BaseModel):
    """Filter pipeline settings."""

    hard_filter_target: int = 3000
    embedding_filter_target: int = 200
    structural_filter_target: int = 200
    candidates_per_slot: int = 5


class RefreshSettings(BaseModel):
    """Data refresh schedule flags."""

    scryfall_daily: bool = True
    topdeck_weekly: bool = True
    decklist_sources_weekly: bool = True
    edhrec_weekly: bool = True
    mtgapi_rulings_monthly: bool = True
    set_refresh_quarterly: bool = True


class OutputSettings(BaseModel):
    """Output format settings."""

    deck_format: str = "json"
    include_alternatives: bool = True
    alternatives_per_slot: int = 3


class KnowledgeBaseSettings(BaseModel):
    """Knowledge base build settings."""

    game_knights_archidekt_owner: str = "GameKnights"
    game_knights_fallback_deck_ids: list[str] = Field(default_factory=list)
    edhrec_articles: list[dict[str, str]] = Field(default_factory=list)


class Settings(BaseModel):
    """Top-level settings container."""

    user: UserSettings = Field(default_factory=UserSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    embeddings: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    refresh: RefreshSettings = Field(default_factory=RefreshSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    knowledge_base: KnowledgeBaseSettings = Field(default_factory=KnowledgeBaseSettings)


def _find_config_path() -> Path:
    """Locate settings.yaml by searching up from this file's location."""
    # Try project root (two levels up from src/sabermetrics/)
    src_dir = Path(__file__).resolve().parent
    project_root = src_dir.parent.parent
    config_path = project_root / "config" / "settings.yaml"
    if config_path.exists():
        return config_path
    # Fallback: current working directory
    cwd_path = Path.cwd() / "config" / "settings.yaml"
    if cwd_path.exists():
        return cwd_path
    return config_path  # Return expected path even if missing


def load_settings(config_path: Path | None = None) -> Settings:
    """Load settings from YAML file.

    Args:
        config_path: Explicit path to settings.yaml. If None, auto-discovers.

    Returns:
        Validated Settings object with defaults for any missing values.
    """
    if config_path is None:
        config_path = _find_config_path()

    if not config_path.exists():
        return Settings()

    with open(config_path) as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    return Settings(**raw)


# Module-level singleton for easy access
settings = load_settings()
