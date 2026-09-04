"""Core card data model."""

from datetime import datetime

from pydantic import BaseModel, Field


class Card(BaseModel):
    """Represents a Magic card from Scryfall."""

    id: str
    oracle_id: str
    name: str
    mana_cost: str | None = None
    cmc: float
    type_line: str
    oracle_text: str | None = None
    color_identity: list[str]
    keywords: list[str] = Field(default_factory=list)
    is_legal_commander: bool
    is_legal_in_99: bool
    set_code: str
    rarity: str
    image_uri: str | None = None
    last_updated: datetime

    # Derived/joined fields (populated when needed)
    current_price_usd: float | None = None
    rulings: list["CardRuling"] = Field(default_factory=list)
    edhrec_inclusion_pct: float | None = None


class CardRuling(BaseModel):
    """A single ruling for a card."""

    ruling_date: datetime | None = None
    ruling_text: str
    source: str = "mtgapi"
