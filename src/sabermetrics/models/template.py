"""Models for deck templates and slot intents."""

from pydantic import BaseModel, Field


class DeckTemplate(BaseModel):
    """Profile-derived deck composition targets.

    Replaces static TARGET_COMPOSITIONS with profile-driven derivation.
    """

    land_count: int = Field(ge=30, le=42)
    ramp_count: int = Field(ge=5, le=18)
    draw_count: int = Field(ge=3, le=15)
    removal_count: int = Field(ge=3, le=15)
    board_wipe_count: int = Field(ge=0, le=6)
    creature_density: float = Field(ge=0.0, le=1.0, default=0.4)
    differentiator_slots: int = Field(ge=10, le=45)
    avg_cmc_target: float = Field(ge=1.5, le=5.5, default=3.0)
    curve_shape: dict[int, int] = Field(default_factory=dict)

    @property
    def infrastructure_slots(self) -> int:
        """Total infrastructure slots (everything except differentiators)."""
        return 99 - self.differentiator_slots

    def to_composition(self) -> dict[str, int]:
        """Convert to legacy composition dict for backward compatibility."""
        # Protection slots carved from differentiator pool
        protection_count = min(4, max(2, self.differentiator_slots // 10))
        infra = (
            self.ramp_count + self.draw_count
            + self.removal_count + self.board_wipe_count
            + protection_count
        )
        diff_remaining = self.differentiator_slots - protection_count
        remaining = 99 - self.land_count - infra - diff_remaining
        return {
            "land": self.land_count,
            "ramp": self.ramp_count,
            "draw": self.draw_count,
            "removal": self.removal_count + self.board_wipe_count,
            "protection": protection_count,
            "wincon": max(3, diff_remaining // 5),
            "utility": diff_remaining - max(3, diff_remaining // 5),
            "other": max(0, remaining),
        }


class SlotIntent(BaseModel):
    """What a differentiator slot should aim to fill."""

    category: str
    priority: float = Field(ge=0.0, le=1.0)
    current_count: int = 0
    target_count: int = 1
    slots_to_fill: int = 0
