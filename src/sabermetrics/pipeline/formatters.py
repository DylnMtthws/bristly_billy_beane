"""Output formatters for generated decks (D6.3).

Supports four output formats:
- JSON: Full structured output (GeneratedDeck model)
- Text: Human-readable stat sheet
- Moxfield: Import-compatible card list
- Archidekt: Import-compatible card list
"""

import logging

from sabermetrics.models.deck import GeneratedDeck

logger = logging.getLogger(__name__)


def _classify_card_type(type_line: str) -> str:
    """Map a type_line to its primary card type for display grouping."""
    tl = type_line.lower()
    if "land" in tl:
        return "Land"
    if "creature" in tl:
        return "Creature"
    if "instant" in tl:
        return "Instant"
    if "sorcery" in tl:
        return "Sorcery"
    if "artifact" in tl:
        return "Artifact"
    if "enchantment" in tl:
        return "Enchantment"
    if "planeswalker" in tl:
        return "Planeswalker"
    if "battle" in tl:
        return "Battle"
    return "Other"


def _group_by_card_type(
    deck: GeneratedDeck,
) -> dict[str, list[tuple[str, int, float | None]]]:
    """Group deck cards by card type, aggregating duplicates (basic lands).

    Returns:
        Dict mapping type name to list of (card_name, quantity, price_usd).
    """
    from collections import Counter

    name_counts: Counter[str] = Counter()
    name_info: dict[str, tuple[str, float | None]] = {}

    for dc in deck.cards:
        name = dc.card.name
        name_counts[name] += 1
        if name not in name_info:
            name_info[name] = (
                dc.card.type_line or "",
                dc.card.current_price_usd,
            )

    by_type: dict[str, list[tuple[str, int, float | None]]] = {}
    for name, qty in name_counts.items():
        type_line, price = name_info[name]
        card_type = _classify_card_type(type_line)
        if card_type not in by_type:
            by_type[card_type] = []
        by_type[card_type].append((name, qty, price))

    return by_type


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

    # Card list by card type (default view)
    lines.append("--- Card List (by Type) ---")
    lines.append(f"Commander: {deck.commander.name}")
    lines.append("")

    by_type = _group_by_card_type(deck)
    type_order = [
        "Creature", "Instant", "Sorcery", "Artifact", "Enchantment",
        "Planeswalker", "Battle", "Land",
    ]
    for card_type in type_order:
        entries = by_type.get(card_type, [])
        if not entries:
            continue
        # Count total including duplicates (basics)
        total = sum(qty for _, qty, _ in entries)
        lines.append(f"  [{card_type}] ({total})")
        for name, qty, price in sorted(entries, key=lambda x: x[0]):
            price_str = f"${price:.2f}" if price else "$0.00"
            qty_str = f"{qty}x " if qty > 1 else ""
            lines.append(f"    {qty_str}{name} ({price_str})")
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

    Format: "N Card Name" per line (aggregates duplicates like basic lands).
    Commander goes in a separate section.
    """
    from collections import Counter

    lines: list[str] = []

    lines.append("// Commander")
    lines.append(f"1 {deck.commander.name}")
    lines.append("")

    # Aggregate card counts
    counts: Counter[str] = Counter()
    for dc in deck.cards:
        counts[dc.card.name] += 1

    lines.append("// Deck")
    for name in sorted(counts):
        lines.append(f"{counts[name]} {name}")

    return "\n".join(lines)


def format_archidekt(deck: GeneratedDeck) -> str:
    """Archidekt-importable format.

    Format: "Nx Card Name" per line with categories.
    Aggregates duplicates (basic lands).
    """

    lines: list[str] = []

    lines.append("// Commander")
    lines.append(f"1x {deck.commander.name}")
    lines.append("")

    # Group by type for Archidekt
    by_type = _group_by_card_type(deck)
    type_order = [
        "Creature", "Instant", "Sorcery", "Artifact", "Enchantment",
        "Planeswalker", "Battle", "Land",
    ]
    for card_type in type_order:
        entries = by_type.get(card_type, [])
        if not entries:
            continue
        lines.append(f"// {card_type}")
        for name, qty, _ in sorted(entries, key=lambda x: x[0]):
            lines.append(f"{qty}x {name}")
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
