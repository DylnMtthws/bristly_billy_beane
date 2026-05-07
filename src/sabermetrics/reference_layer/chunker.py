"""Document chunker for reference material (D3.4).

Splits documents into ~500-token chunks with semantic boundary respect:
- Comprehensive Rules: chunk by section number (CR 100, CR 101, etc.)
- Articles: chunk by paragraph clusters with overlap
- Each chunk gets metadata: document, section, tier
"""

import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """A chunk of reference text with metadata."""

    id: str
    document: str
    section: str | None
    tier: int
    content: str


class DocumentChunker:
    """Splits reference documents into semantically coherent chunks."""

    # Approximate tokens per chunk target
    TARGET_CHUNK_TOKENS: int = 500
    # Approximate chars per token (conservative estimate)
    CHARS_PER_TOKEN: float = 4.0

    def chunk_comprehensive_rules(self, rules_path: Path) -> list[Chunk]:
        """Chunk Comprehensive Rules by section number.

        Sections are identified by patterns like "100.", "100.1", "702.21a".
        Tier 1 = highest priority reference material.

        Args:
            rules_path: Path to comprehensive_rules.txt.

        Returns:
            List of Chunks with section metadata.
        """
        if not rules_path.exists():
            logger.warning("Rules file not found: %s", rules_path)
            return []

        text = rules_path.read_text(encoding="utf-8", errors="replace")
        chunks: list[Chunk] = []

        # Split by top-level section numbers (e.g., "100. General", "702. Keyword Abilities")
        # Pattern: line starting with a number followed by a period
        section_pattern = re.compile(r"^(\d{3})\.\s", re.MULTILINE)
        sections = section_pattern.split(text)

        # sections alternates: [preamble, "100", content, "101", content, ...]
        current_section = "preamble"
        for i, part in enumerate(sections):
            if re.fullmatch(r"\d{3}", part):
                current_section = part
                continue

            if not part.strip():
                continue

            section_label = f"CR {current_section}" if current_section != "preamble" else "preamble"

            # Further split large sections into sub-chunks
            sub_chunks = self._split_by_size(part, section_label)
            for sub_content, sub_section in sub_chunks:
                chunks.append(
                    Chunk(
                        id=str(uuid.uuid4()),
                        document="comprehensive_rules",
                        section=sub_section,
                        tier=1,
                        content=sub_content.strip(),
                    )
                )

        logger.info(
            "Chunked Comprehensive Rules into %d chunks", len(chunks)
        )
        return chunks

    def chunk_commander_rules(self, rules_path: Path) -> list[Chunk]:
        """Chunk Commander-specific rules.

        Tier 1 = highest priority (Commander is our target format).

        Args:
            rules_path: Path to commander_rules.txt.

        Returns:
            List of Chunks.
        """
        if not rules_path.exists():
            logger.warning("Commander rules file not found: %s", rules_path)
            return []

        text = rules_path.read_text(encoding="utf-8", errors="replace")
        chunks: list[Chunk] = []

        # Split by paragraphs
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        # Group paragraphs into chunks of appropriate size
        current_content: list[str] = []
        current_length = 0
        target_chars = int(self.TARGET_CHUNK_TOKENS * self.CHARS_PER_TOKEN)

        for para in paragraphs:
            current_content.append(para)
            current_length += len(para)

            if current_length >= target_chars:
                chunks.append(
                    Chunk(
                        id=str(uuid.uuid4()),
                        document="commander_rules",
                        section=None,
                        tier=1,
                        content="\n\n".join(current_content),
                    )
                )
                current_content = []
                current_length = 0

        # Remaining content
        if current_content:
            chunks.append(
                Chunk(
                    id=str(uuid.uuid4()),
                    document="commander_rules",
                    section=None,
                    tier=1,
                    content="\n\n".join(current_content),
                )
            )

        logger.info("Chunked Commander rules into %d chunks", len(chunks))
        return chunks

    def chunk_article(
        self, article_path: Path, tier: int = 3
    ) -> list[Chunk]:
        """Chunk a strategic article by paragraph clusters with overlap.

        Args:
            article_path: Path to the article text file.
            tier: Reference tier (default 3 for articles).

        Returns:
            List of Chunks.
        """
        if not article_path.exists():
            logger.warning("Article file not found: %s", article_path)
            return []

        text = article_path.read_text(encoding="utf-8", errors="replace")
        # Derive document name from filename
        doc_name = article_path.stem

        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks: list[Chunk] = []
        target_chars = int(self.TARGET_CHUNK_TOKENS * self.CHARS_PER_TOKEN)

        # Sliding window with 1-paragraph overlap
        current_content: list[str] = []
        current_length = 0

        for para in paragraphs:
            current_content.append(para)
            current_length += len(para)

            if current_length >= target_chars:
                chunks.append(
                    Chunk(
                        id=str(uuid.uuid4()),
                        document=doc_name,
                        section=None,
                        tier=tier,
                        content="\n\n".join(current_content),
                    )
                )
                # Keep last paragraph as overlap for context continuity
                if current_content:
                    overlap = current_content[-1]
                    current_content = [overlap]
                    current_length = len(overlap)
                else:
                    current_content = []
                    current_length = 0

        if current_content:
            chunks.append(
                Chunk(
                    id=str(uuid.uuid4()),
                    document=doc_name,
                    section=None,
                    tier=tier,
                    content="\n\n".join(current_content),
                )
            )

        logger.info(
            "Chunked article '%s' into %d chunks", doc_name, len(chunks)
        )
        return chunks

    def chunk_game_changers(self, yaml_path: Path) -> list[Chunk]:
        """Create reference chunk from game changers list.

        Args:
            yaml_path: Path to game_changers.yaml.

        Returns:
            Single Chunk containing the game changer list.
        """
        import yaml

        if not yaml_path.exists():
            return []

        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}

        changers = data.get("game_changers", [])
        if not changers:
            return []

        lines = ["WotC Official Game Changer Cards (Bracket Framework):", ""]
        for gc in changers:
            name = gc.get("card_name", "")
            bracket = gc.get("bracket_threshold", "?")
            rationale = gc.get("rationale", "")
            lines.append(f"- {name} (bracket {bracket}): {rationale}")

        return [
            Chunk(
                id=str(uuid.uuid4()),
                document="game_changers",
                section=None,
                tier=2,
                content="\n".join(lines),
            )
        ]

    def _split_by_size(
        self, text: str, section_label: str
    ) -> list[tuple[str, str]]:
        """Split text into chunks of approximately TARGET_CHUNK_TOKENS tokens.

        Returns:
            List of (content, section) tuples.
        """
        target_chars = int(self.TARGET_CHUNK_TOKENS * self.CHARS_PER_TOKEN)

        if len(text) <= target_chars:
            return [(text, section_label)]

        # Split by sub-section patterns (e.g., "100.1", "702.21a")
        sub_pattern = re.compile(r"^(\d{3}\.\d+\w?)\s", re.MULTILINE)
        parts = sub_pattern.split(text)

        result: list[tuple[str, str]] = []
        current_text = ""
        current_section = section_label

        for i, part in enumerate(parts):
            if sub_pattern.match(part + " "):
                current_section = f"CR {part}"
                continue

            current_text += part
            if len(current_text) >= target_chars:
                result.append((current_text, current_section))
                current_text = ""

        if current_text.strip():
            result.append((current_text, current_section))

        return result if result else [(text, section_label)]
