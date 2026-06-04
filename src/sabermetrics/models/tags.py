"""Models for card role tagging and functional categories."""


from pydantic import BaseModel, Field


class RoleTagResult(BaseModel):
    """Result of tagging a single card with roles and functional categories."""

    role_tags: list[str] = Field(default_factory=list)
    functional_categories: list[str] = Field(default_factory=list)


class TaggingStats(BaseModel):
    """Statistics from a batch role-tagging pass."""

    total_cards: int
    tagged_cards: int
    skipped_cards: int
    version: str
    duration_seconds: float
    role_distribution: dict[str, int] = Field(default_factory=dict)
    category_distribution: dict[str, int] = Field(default_factory=dict)
