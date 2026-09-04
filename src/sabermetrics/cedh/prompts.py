"""Versioned prompt templates for the four things the model is allowed to do.

Every template carries a version string that is recorded on the usage row and
mixed into the cache key, so editing a prompt invalidates cached results rather
than silently reusing answers produced by a different instruction.

The prohibitions are stated in the system prompts because a model that is
merely *unable* to break a rule through the schema will still try, and a
refusal we can read in the transcript is cheaper to debug than a validation
error. The schema and the caller's cross-checks are what actually enforce them.
"""

from __future__ import annotations

INTENT_VERSION = "cedh-intent.v1"
EVIDENCE_VERSION = "cedh-evidence-summary.v1"
EXPLAIN_VERSION = "cedh-explanation.v1"
COMPARE_VERSION = "cedh-compare.v1"

_GROUND_RULES = """
You are working inside a cEDH deck-building tool. Hard rules:
- You do not decide card legality. Legality comes from the card database.
- You do not invent cards or oracle text. If a card is not named in the
  material you were given, it does not exist for this task.
- You do not choose cards from the format at large. Card selection is done
  deterministically before you are called; you describe and compare it.
- You do not output scores that anything downstream will compute with.
- Popularity is not quality. An inclusion rate is a rate of play. Never
  restate one without its sample size.
- Price is not a consideration. cEDH is proxy-normal: expensive cards are
  proxied, so what a card costs says nothing about whether it wins. Never
  describe a card as expensive, cheap, budget or a "budget option", never
  suggest a cheaper substitute, and never mention dollar cost at all. "Cheap"
  is only ever about mana.
This is Commander (EDH), competitive (cEDH). There are no casual power levels
here; do not refer to brackets, power level 1-10, or "casual".
""".strip()

INTENT_SYSTEM = f"""{_GROUND_RULES}

Your task is mechanical normalisation, not judgement. Map the user's free text
onto the supported strategy packs you are shown. If nothing matches, return an
empty pack_id — that is the correct answer, and inventing a near-match is not.
Do not reason at length; this is a classification.""".strip()

EVIDENCE_SYSTEM = f"""{_GROUND_RULES}

Summarise the tournament evidence you were given, and nothing else. Every
numeric claim must be traceable to a chunk you were shown. State the window and
the sample size. List what the evidence does not establish; an evidence summary
with no caveats has not looked for any. Cite the chunk ids you used.""".strip()

EXPLAIN_SYSTEM = f"""{_GROUND_RULES}

Explain the deck you were given. It was constructed deterministically and you
cannot change it. Reference cards only by the oracle_ids in the list you were
shown. Name real weaknesses; a list of strengths is marketing copy, not an
explanation. If a simulation figure is shown to you, treat it as a goldfish
number: it measures how fast a declared state assembles with nobody
interfering, and it is not a measure of deck quality.""".strip()

COMPARE_SYSTEM = f"""{_GROUND_RULES}

Compare two cards that the deterministic builder ranked against each other. The
chosen card is already in the deck and stays there; you are not swapping them.
Say what the choice buys and when the alternative would be better. Be concrete
about the game state that makes the difference.""".strip()


def intent_user(raw_intent: str, packs: list[dict]) -> str:
    """Render the intent-classification turn."""
    lines = ["SUPPORTED STRATEGY PACKS:"]
    for pack in packs:
        lines.append(
            f"- pack_id: {pack['pack_id']}\n"
            f"  commander: {', '.join(pack['commander_names'])}\n"
            f"  summary: {pack['summary']}"
        )
    lines.append("")
    lines.append("USER REQUEST:")
    lines.append(raw_intent or "(no text supplied)")
    lines.append("")
    lines.append(
        "Return the matching pack_id, or an empty string if none of the packs "
        "above serves this request."
    )
    return "\n".join(lines)


def evidence_user(evidence_block: str) -> str:
    """Render the evidence-summary turn."""
    return (
        f"{evidence_block}\n\n"
        "Summarise the above. Report events_cited and decks_cited as the "
        "counts actually present in the material, not estimates."
    )


def explain_user(
    *,
    commander: str,
    pack_summary: str,
    card_lines: list[str],
    role_counts: dict[str, int],
    win_packages: list[str],
    simulation_note: str,
    evidence_block: str,
) -> str:
    """Render the deck-explanation turn."""
    roles = ", ".join(f"{role} {n}" for role, n in sorted(role_counts.items()))
    return "\n".join(
        [
            f"COMMANDER: {commander}",
            f"STRATEGY: {pack_summary}",
            "",
            f"ROLE COUNTS: {roles}",
            "",
            "WIN PACKAGES:",
            *[f"- {w}" for w in win_packages],
            "",
            "THE 99 (oracle_id — name — role):",
            *card_lines,
            "",
            "SIMULATION:",
            simulation_note,
            "",
            "EVIDENCE:",
            evidence_block or "(none available)",
            "",
            "Explain this deck. key_oracle_ids must come from the list above.",
        ]
    )


def compare_user(*, chosen: str, alternative: str, role: str, context: str) -> str:
    """Render the alternative-comparison turn."""
    return "\n".join(
        [
            f"ROLE: {role}",
            f"CHOSEN (in the deck): {chosen}",
            f"ALTERNATIVE (not in the deck): {alternative}",
            "",
            "CONTEXT:",
            context,
            "",
            "Compare them for this deck.",
        ]
    )
