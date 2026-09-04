"""Structured response models: everything the model is allowed to return.

Each model here is the schema for exactly one call, and each call is one of the
four things DeepSeek may do. What the model may *not* do is enforced by the
shapes themselves rather than by prompt wording:

* It never returns a card the pool does not contain — every card-bearing field
  is an ``oracle_id`` that the caller checks against the pack pool, and a
  hallucinated id fails that check instead of entering a deck.
* It never returns oracle text, legality, mana costs or any other card fact.
  Those come from :class:`~sabermetrics.cedh.repositories.CardRepository`.
* It never returns a score the deck builder consumes. Comparisons are prose
  for a person to read, ranked deterministically before the model sees them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IntentClassification(BaseModel):
    """Normalised free-text intent. The lowest reasoning mode.

    Mechanical normalisation only: it maps words onto an enumeration the code
    already supports. It does not choose cards and does not judge anything.
    """

    model_config = ConfigDict(extra="forbid")

    #: Which supported pack the request is asking for. The caller validates
    #: this against the registry; an unknown id is treated as "unsupported",
    #: never as licence to build something else.
    pack_id: str = ""
    #: Free-text summary of what the user asked for, for display.
    restated_intent: str = ""
    #: There are deliberately no budget or collection flags here. cEDH is
    #: proxy-normal and a new deck means acquiring or proxying cards, so the
    #: builder has no way to honour either — and a field the builder ignores is
    #: an invitation for the model to report a constraint that does nothing.
    #: Verbatim card names the user named. Resolved against the repository by
    #: the caller; unresolved names are reported, not silently dropped.
    named_cards: list[str] = Field(default_factory=list, max_length=30)
    confidence: Literal["high", "medium", "low"] = "low"


class EvidenceSummary(BaseModel):
    """A prose summary of retrieved tournament evidence.

    Sample sizes are required fields rather than something the prose may
    mention, because a metagame claim without its denominator reads as stronger
    than it is. The caller cross-checks these against the evidence package and
    rejects a summary that inflates them.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(max_length=2000)
    #: Events the summary drew on, as stated by the model.
    events_cited: int = Field(ge=0)
    #: Decks the summary drew on.
    decks_cited: int = Field(ge=0)
    #: What the evidence does not establish. Required: an evidence summary that
    #: lists no limits is a summary that has not looked for any.
    caveats: list[str] = Field(default_factory=list, max_length=8)
    #: Chunk ids the summary used, for attribution back to sources.
    chunk_ids: list[str] = Field(default_factory=list, max_length=40)


class AlternativeComparison(BaseModel):
    """One deterministic candidate compared against one alternative."""

    model_config = ConfigDict(extra="forbid")

    chosen_oracle_id: str
    alternative_oracle_id: str
    #: Why the deterministic pick is defensible, or why the alternative has a
    #: case. The model does not get to swap them: this is prose for the user.
    comparison: str = Field(max_length=1200)
    #: When the alternative is genuinely better in a stated context.
    alternative_better_when: str = ""


class DeckExplanation(BaseModel):
    """The narrative shown with a candidate."""

    model_config = ConfigDict(extra="forbid")

    game_plan: str = Field(max_length=2000)
    primary_line: str = Field(max_length=1500)
    #: Named weaknesses. Required and non-empty by convention in the prompt;
    #: an explanation with no weaknesses is marketing copy.
    weaknesses: list[str] = Field(default_factory=list, max_length=8)
    #: Cards the explanation leans on, as oracle_ids from the candidate.
    key_oracle_ids: list[str] = Field(default_factory=list, max_length=20)
    mulligan_guidance: str = ""
