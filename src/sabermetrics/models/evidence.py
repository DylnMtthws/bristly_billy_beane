"""Evidence package models for profile generation."""

from pydantic import BaseModel

from .card import Card, CardRuling


class RedditThread(BaseModel):
    """A Reddit thread about a commander."""

    title: str
    url: str
    upvotes: int
    created_utc: int
    summary: str | None = None


class PrimerArticle(BaseModel):
    """A primer/strategy article about a commander."""

    title: str
    url: str
    source: str
    summary: str | None = None


class ReferenceChunk(BaseModel):
    """A chunk of reference material (rules, etc.)."""

    id: str
    document: str
    section: str | None
    tier: int
    content: str


class EvidencePackage(BaseModel):
    """Composed by EvidenceAggregator for profile generation."""

    commander: Card
    rulings: list[CardRuling]
    edhrec_data: dict | None = None
    tournament_data: dict | None = None
    reddit_threads: list[RedditThread]
    primer_articles: list[PrimerArticle]
    reference_chunks: list[ReferenceChunk]
    user_intent: str | None = None
    referenced_keywords: list[str] = []
    referenced_mechanics: list[str] = []
