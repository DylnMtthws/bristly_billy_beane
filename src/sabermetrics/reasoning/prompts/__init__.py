"""Prompt template loader (D5.2).

Loads .txt prompt templates from this directory and formats them
with named placeholders using str.format().
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent
_CACHE: dict[str, str] = {}


def load_prompt(name: str) -> str:
    """Load a prompt template by name.

    Args:
        name: Template name without extension (e.g. 'profile_synthesis').

    Returns:
        Raw template string with {placeholder} variables.

    Raises:
        FileNotFoundError: If template file doesn't exist.
    """
    if name in _CACHE:
        return _CACHE[name]

    path = _PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")

    template = path.read_text(encoding="utf-8")
    _CACHE[name] = template
    logger.debug("Loaded prompt template: %s", name)
    return template


def format_prompt(name: str, **kwargs: str) -> str:
    """Load and format a prompt template with variables.

    Args:
        name: Template name.
        **kwargs: Named placeholders to substitute.

    Returns:
        Formatted prompt string.
    """
    template = load_prompt(name)
    return template.format(**kwargs)


def list_prompts() -> list[str]:
    """List available prompt template names."""
    return [p.stem for p in _PROMPTS_DIR.glob("*.txt")]
