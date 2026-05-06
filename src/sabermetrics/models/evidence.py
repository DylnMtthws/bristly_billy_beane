"""Evidence package models for profile generation."""

from typing import List, Optional

from pydantic import BaseModel

from .card import Card, CardRuling


class RedditThread(BaseModel):
    """A Reddit thread about a commander."""

    title: str
    url: str
    upvotes: int
    created_utc: int
    summary: Optional[str] = None


class PrimerArticle(BaseModel):
    """A primer/strategy article about a commander."""

    title: str
    url: str
    source: str
    summary: Optional[str] = None


class ReferenceChunk(BaseModel):
    """A chunk of reference material (rules, etc.)."""

    id: str
    document: str
    section: Optional[str]
    tier: int
    content: str


class EvidencePackage(BaseModel):
    """Composed by EvidenceAggregator for profile generation."""

    commander: Card
    rulings: List[CardRuling]
    edhrec_data: Optional[dict] = None
    tournament_data: Optional[dict] = None
    reddit_threads: List[RedditThread]
    primer_articles: List[PrimerArticle]
    reference_chunks: List[ReferenceChunk]
    user_intent: Optional[str] = None
