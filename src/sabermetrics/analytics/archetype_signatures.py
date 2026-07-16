"""Macro-archetype signature scoring (Phase 1).

Scores a decklist against a library of universal archetype signatures
(``config/archetype_signatures.yaml``) built from known signature cards pooled
across all commanders. This is the card-based counterpart to
``theme_patterns.py`` (oracle-text based) and is reusable by every commander
downstream — Phase 3 assigns each of a commander's decks to a macro-archetype
cluster using :func:`classify_deck`.

Pure module: no DB, no network. Card-name matching is normalized (front face,
case-folded) so it works against both Scryfall and Archidekt card names.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "config" / "archetype_signatures.yaml"
)


class Archetype(BaseModel):
    """One macro-archetype: its signature cards and validation tag aliases."""

    name: str
    signatures: dict[str, float] = Field(default_factory=dict)
    tag_aliases: list[str] = Field(default_factory=list)
    min_score: float

    # Signature card names, pre-normalized for matching.
    normalized_signatures: dict[str, float] = Field(default_factory=dict)


class ArchetypeLibrary(BaseModel):
    """The full signature library plus its default threshold."""

    archetypes: dict[str, Archetype]
    default_min_score: float = 2.0

    # Reverse index: normalized creator-tag string -> set of archetype names.
    tag_index: dict[str, list[str]] = Field(default_factory=dict)


class DeckClassification(BaseModel):
    """Result of scoring one deck against the library."""

    scores: dict[str, float]
    labels: list[str]           # every archetype at/above its min_score
    dominant: str | None        # highest-scoring archetype clearing threshold


def normalize_name(name: str) -> str:
    """Normalize a card name for matching (front face, case-folded).

    Args:
        name: A card name, possibly a ``Front // Back`` double-faced name.

    Returns:
        Lowercased, stripped front-face name.
    """
    if not name:
        return ""
    front = name.split("//")[0]
    return front.strip().casefold()


def load_library(path: Path | None = None) -> ArchetypeLibrary:
    """Load and index the archetype signature library from YAML.

    Args:
        path: Optional override path; defaults to the packaged config.

    Returns:
        An :class:`ArchetypeLibrary` with normalized signatures and a
        tag-alias reverse index ready for scoring/validation.
    """
    config_path = path or _CONFIG_PATH
    if not config_path.exists():
        logger.warning("archetype_signatures.yaml not found at %s", config_path)
        return ArchetypeLibrary(archetypes={}, default_min_score=2.0)

    with open(config_path) as f:
        data = yaml.safe_load(f) or {}

    default_min = float(data.get("default_min_score", 2.0))
    archetypes: dict[str, Archetype] = {}
    tag_index: dict[str, list[str]] = {}

    for name, spec in (data.get("archetypes") or {}).items():
        spec = spec or {}
        signatures = {
            str(card): float(weight)
            for card, weight in (spec.get("signatures") or {}).items()
        }
        normalized = {normalize_name(card): w for card, w in signatures.items()}
        tag_aliases = [str(t) for t in (spec.get("tag_aliases") or [])]
        min_score = float(spec.get("min_score", default_min))

        archetypes[name] = Archetype(
            name=name,
            signatures=signatures,
            tag_aliases=tag_aliases,
            min_score=min_score,
            normalized_signatures=normalized,
        )

        for tag in tag_aliases:
            tag_index.setdefault(normalize_name(tag), []).append(name)

    return ArchetypeLibrary(
        archetypes=archetypes,
        default_min_score=default_min,
        tag_index=tag_index,
    )


def score_deck(
    card_names: Iterable[str], library: ArchetypeLibrary
) -> dict[str, float]:
    """Score a decklist against every archetype.

    Each archetype's score is the summed weight of its signature cards present
    in the deck. A card is counted once per archetype regardless of quantity.

    Args:
        card_names: The deck's card names (any face/case).
        library: A loaded :class:`ArchetypeLibrary`.

    Returns:
        Mapping of archetype name -> summed signature weight (0.0 if none).
    """
    present = {normalize_name(n) for n in card_names if n}
    scores: dict[str, float] = {}
    for name, arch in library.archetypes.items():
        total = sum(
            weight
            for card, weight in arch.normalized_signatures.items()
            if card in present
        )
        scores[name] = round(total, 3)
    return scores


def classify_deck(
    card_names: Iterable[str], library: ArchetypeLibrary
) -> DeckClassification:
    """Classify a deck into zero or more macro-archetypes (multi-label).

    Args:
        card_names: The deck's card names.
        library: A loaded :class:`ArchetypeLibrary`.

    Returns:
        A :class:`DeckClassification` with per-archetype scores, all archetypes
        clearing their ``min_score``, and the single highest-scoring one (or
        None if nothing clears threshold).
    """
    scores = score_deck(card_names, library)

    labels = [
        name
        for name, score in scores.items()
        if score >= library.archetypes[name].min_score
    ]
    labels.sort(key=lambda n: scores[n], reverse=True)
    dominant = labels[0] if labels else None

    return DeckClassification(scores=scores, labels=labels, dominant=dominant)


def tags_to_archetypes(
    tags: Iterable[str], library: ArchetypeLibrary
) -> set[str]:
    """Map creator-assigned tags to the archetypes they alias (validation gold).

    Args:
        tags: Creator-assigned tag strings from a deck.
        library: A loaded :class:`ArchetypeLibrary`.

    Returns:
        Set of archetype names any tag aliases (empty if none recognized).
    """
    result: set[str] = set()
    for tag in tags:
        for arch_name in library.tag_index.get(normalize_name(tag), []):
            result.add(arch_name)
    return result
