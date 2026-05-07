"""Output formatters for generated decks (D6.3).

Supports four output formats:
- JSON: Full structured output (GeneratedDeck model)
- Text: Human-readable stat sheet
- Moxfield: Import-compatible card list
- Archidekt: Import-compatible card list
"""

import json
import logging

from sabermetrics.models.deck import GeneratedDeck

logger = logging.getLogger(__name__)


def format_json(deck: GeneratedDeck) -> str:
    """Full JSON export of GeneratedDeck."""
    return deck.model_dump_json(indent=2)


def format_text(deck: GeneratedDeck) -> str:
    """Human-readable stat sheet."""
    lines: list[str] = []

    lines.append(f"{'=' * 60}")
    lines.append(f"  DECK: {deck.commander.name}")
    lines.append(f"{'=' * 60}")
    lines.append("")

    # Parameters
    lines.append(f"Budget: ${deck.parameters.budget_usd:.2f}")
    lines.append(f"Power Target: {deck.parameters.power_target}")
    if deck.parameters.strategy:
        lines.append(f"Strategy: {deck.parameters.strategy}")
    lines.append("")

    # Classification
    lines.append(f"Estimated Bracket: {deck.classification.estimated_bracket}")
    lines.append(f"Reasoning: {deck.classification.bracket_reasoning}")
    lines.append("")

    # Narrative
    lines.append("--- Game Plan ---")
    lines.append(deck.narrative.game_plan)
    lines.append("")

    lines.append("--- Key Synergies ---")
    for syn in deck.narrative.key_synergies:
        lines.append(f"  * {syn}")
    lines.append("")

    lines.append("--- Weaknesses ---")
    for weak in deck.narrative.weaknesses:
        lines.append(f"  * {weak}")
    lines.append("")

    lines.append("--- Play Pattern ---")
    lines.append(deck.narrative.suggested_play_pattern)
    lines.append("")

    # Composition stats
    comp = deck.composition
    lines.append("--- Composition ---")
    lines.append(f"Total Price: ${comp.total_price_usd:.2f}")
    lines.append(f"Average CMC: {comp.average_cmc:.2f}")
    lines.append(f"Ramp: {comp.component_counts.ramp}")
    lines.append(f"Draw: {comp.component_counts.draw}")
    lines.append(f"Removal: {comp.component_counts.removal}")
    lines.append(f"Board Wipes: {comp.component_counts.board_wipes}")
    lines.append(f"Tutors: {comp.component_counts.tutors}")
    lines.append(f"Win Conditions: {comp.component_counts.win_conditions}")
    lines.append("")

    # Mana curve
    lines.append("--- Mana Curve ---")
    max_count = max(comp.mana_curve) if comp.mana_curve else 1
    for i, count in enumerate(comp.mana_curve):
        label = f"{i}" if i < 7 else "7+"
        bar = "#" * int(count / max(max_count, 1) * 30)
        lines.append(f"  {label}: {bar} ({count})")
    lines.append("")

    # Card list by role
    lines.append("--- Card List ---")
    lines.append(f"Commander: {deck.commander.name}")
    lines.append("")

    # Group by role
    by_role: dict[str, list] = {}
    for dc in deck.cards:
        role = dc.slot_role
        if role not in by_role:
            by_role[role] = []
        by_role[role].append(dc)

    role_order = ["land", "ramp", "draw", "removal", "wincon", "utility", "other"]
    for role in role_order:
        cards = by_role.get(role, [])
        if not cards:
            continue
        lines.append(f"  [{role.upper()}] ({len(cards)})")
        for dc in sorted(cards, key=lambda x: x.card.name):
            price_str = f"${dc.card.current_price_usd:.2f}" if dc.card.current_price_usd else "$?.??"
            lines.append(f"    {dc.card.name} ({price_str})")
        lines.append("")

    # Generation metadata
    lines.append("--- Generation Info ---")
    lines.append(f"Time: {deck.meta.generation_time_seconds:.1f}s")
    lines.append(f"LLM Cost: ${deck.meta.llm_cost_usd:.4f}")
    lines.append(f"Cards: {len(deck.cards)} + 1 commander = {len(deck.cards) + 1}")
    lines.append("")

    return "\n".join(lines)


def format_moxfield(deck: GeneratedDeck) -> str:
    """Moxfield-importable format.

    Format: "1 Card Name" per line.
    Commander goes in a separate section.
    """
    lines: list[str] = []

    # Moxfield uses sections
    lines.append("// Commander")
    lines.append(f"1 {deck.commander.name}")
    lines.append("")

    # Main deck
    lines.append("// Deck")
    for dc in sorted(deck.cards, key=lambda x: x.card.name):
        lines.append(f"1 {dc.card.name}")

    return "\n".join(lines)


def format_archidekt(deck: GeneratedDeck) -> str:
    """Archidekt-importable format.

    Format: "1x Card Name" per line with categories.
    """
    lines: list[str] = []

    # Commander
    lines.append("// Commander")
    lines.append(f"1x {deck.commander.name}")
    lines.append("")

    # Group by role with Archidekt-style categories
    role_labels = {
        "land": "Lands",
        "ramp": "Ramp",
        "draw": "Card Draw",
        "removal": "Removal",
        "wincon": "Win Conditions",
        "utility": "Utility",
        "other": "Other",
    }

    by_role: dict[str, list] = {}
    for dc in deck.cards:
        role = dc.slot_role
        if role not in by_role:
            by_role[role] = []
        by_role[role].append(dc)

    role_order = ["land", "ramp", "draw", "removal", "wincon", "utility", "other"]
    for role in role_order:
        cards = by_role.get(role, [])
        if not cards:
            continue
        label = role_labels.get(role, role.capitalize())
        lines.append(f"// {label}")
        for dc in sorted(cards, key=lambda x: x.card.name):
            lines.append(f"1x {dc.card.name}")
        lines.append("")

    return "\n".join(lines)


def format_deck(
    deck: GeneratedDeck,
    output_format: str = "json",
) -> str:
    """Format a deck in the specified output format.

    Args:
        deck: The generated deck.
        output_format: One of "json", "text", "moxfield", "archidekt".

    Returns:
        Formatted string.

    Raises:
        ValueError: If format is not recognized.
    """
    formatters = {
        "json": format_json,
        "text": format_text,
        "moxfield": format_moxfield,
        "archidekt": format_archidekt,
    }

    formatter = formatters.get(output_format)
    if formatter is None:
        raise ValueError(
            f"Unknown format '{output_format}'. "
            f"Available: {', '.join(formatters)}"
        )

    return formatter(deck)
