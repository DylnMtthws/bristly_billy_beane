"""Tests for Phase 3 reference layer (A3.1-A3.4)."""

import tempfile
from pathlib import Path

import yaml

from sabermetrics.reference_layer.chunker import DocumentChunker, Chunk
from sabermetrics.reference_layer.retriever import ReferenceQuery, RetrievedChunk


def test_chunker_comprehensive_rules() -> None:
    """Comprehensive rules chunking splits by section number."""
    chunker = DocumentChunker()

    # Create a small mock rules file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(
            "100. General\n"
            "100.1 These rules apply to all games of Magic.\n"
            "100.2 Players sit in a circle.\n\n"
            "101. The Magic Golden Rules\n"
            "101.1 Whenever a card contradicts the rules, the card wins.\n"
            "101.2 When a rule or effect says something can happen, "
            "and another says it can't, the 'can't' wins.\n\n"
            "702. Keyword Abilities\n"
            "702.1 Most abilities are keyword abilities.\n"
            "702.2 Flying\n"
            "702.2a Flying is an evasion ability.\n"
            "702.21 Ward\n"
            "702.21a Ward is a triggered ability.\n"
        )
        rules_path = Path(f.name)

    chunks = chunker.chunk_comprehensive_rules(rules_path)
    assert len(chunks) > 0
    assert all(isinstance(c, Chunk) for c in chunks)
    assert all(c.document == "comprehensive_rules" for c in chunks)
    assert all(c.tier == 1 for c in chunks)

    # At least some chunks should have CR section labels
    sections = [c.section for c in chunks if c.section and c.section.startswith("CR")]
    assert len(sections) > 0

    rules_path.unlink()


def test_chunker_commander_rules() -> None:
    """Commander rules chunking splits by paragraphs."""
    chunker = DocumentChunker()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(
            "Commander is a format where each player has a commander.\n\n"
            "The commander must be a legendary creature or planeswalker.\n\n"
            "Decks must be exactly 100 cards including the commander.\n\n"
            "Color identity is determined by mana symbols on the card.\n"
        )
        rules_path = Path(f.name)

    chunks = chunker.chunk_commander_rules(rules_path)
    assert len(chunks) > 0
    assert all(c.document == "commander_rules" for c in chunks)
    assert all(c.tier == 1 for c in chunks)

    rules_path.unlink()


def test_chunker_article() -> None:
    """Article chunking respects paragraph boundaries."""
    chunker = DocumentChunker()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        # Write enough content to trigger multiple chunks
        for i in range(20):
            f.write(f"Paragraph {i}: " + "word " * 100 + "\n\n")
        path = Path(f.name)

    chunks = chunker.chunk_article(path, tier=3)
    assert len(chunks) > 1  # Should create multiple chunks
    assert all(c.tier == 3 for c in chunks)

    path.unlink()


def test_chunker_game_changers() -> None:
    """Game changers YAML creates a reference chunk."""
    chunker = DocumentChunker()
    gc_path = Path(__file__).resolve().parent.parent / "config" / "game_changers.yaml"
    chunks = chunker.chunk_game_changers(gc_path)
    assert len(chunks) == 1
    assert chunks[0].document == "game_changers"
    assert chunks[0].tier == 2
    assert "Dockside Extortionist" in chunks[0].content


def test_synergy_rules_valid() -> None:
    """synergy_rules.yaml validates against expected schema (A3.4)."""
    config_path = Path(__file__).resolve().parent.parent / "config" / "synergy_rules.yaml"
    with open(config_path) as f:
        data = yaml.safe_load(f)

    rules = data.get("rules", [])
    assert len(rules) >= 30, f"Expected >=30 synergy rules, got {len(rules)}"

    for rule in rules:
        assert "id" in rule, f"Rule missing id: {rule}"
        assert "trigger" in rule, f"Rule {rule['id']} missing trigger"
        assert "payoff" in rule, f"Rule {rule['id']} missing payoff"
        assert "strength" in rule, f"Rule {rule['id']} missing strength"
        assert "description" in rule, f"Rule {rule['id']} missing description"
        assert 0.0 <= rule["strength"] <= 1.0, (
            f"Rule {rule['id']} strength {rule['strength']} out of range"
        )


def test_reference_query_model() -> None:
    """ReferenceQuery can be created with filters."""
    query = ReferenceQuery(
        query_text="color identity in commander",
        tier_filter=[1, 2],
        document_filter=["comprehensive_rules"],
        top_k=5,
    )
    assert query.query_text == "color identity in commander"
    assert query.top_k == 5


def test_retrieved_chunk_model() -> None:
    """RetrievedChunk can be created."""
    chunk = RetrievedChunk(
        id="test-id",
        document="comprehensive_rules",
        section="CR 903",
        tier=1,
        content="Commander format rules...",
        similarity_score=0.85,
    )
    assert chunk.similarity_score == 0.85
    assert chunk.tier == 1
