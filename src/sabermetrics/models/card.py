"""Core card data model."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class Card(BaseModel):
    """Represents a Magic card from Scryfall."""

    id: str
    oracle_id: str
    name: str
    mana_cost: Optional[str] = None
    cmc: float
    type_line: str
    oracle_text: Optional[str] = None
    color_identity: List[str]
    keywords: List[str] = Field(default_factory=list)
    is_legal_commander: bool
    is_legal_in_99: bool
    set_code: str
    rarity: str
    image_uri: Optional[str] = None
    last_updated: datetime

    # Derived/joined fields (populated when needed)
    current_price_usd: Optional[float] = None
    rulings: List["CardRuling"] = Field(default_factory=list)
    edhrec_inclusion_pct: Optional[float] = None


class CardRuling(BaseModel):
    """A single ruling for a card."""

    ruling_date: Optional[datetime] = None
    ruling_text: str
    source: str = "mtgapi"
