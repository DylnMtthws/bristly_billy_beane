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

    def chunk_mechanics_article(self, article_path: Path) -> list[Chunk]:
        """Chunk a WotC set mechanics article by mechanic sections.

        Mechanics articles describe keyword abilities and set mechanics.
        Each mechanic heading gets its own chunk when possible, preserving
        the full explanation for that mechanic. Falls back to paragraph
        clustering for articles without clear mechanic headings.

        Tier 2 = strategic reference (these define how mechanics work).

        Args:
            article_path: Path to the mechanics article text file.

        Returns:
            List of Chunks with mechanic-aware boundaries.
        """
        if not article_path.exists():
            logger.warning("Mechanics article not found: %s", article_path)
            return []

        text = article_path.read_text(encoding="utf-8", errors="replace")
        doc_name = article_path.stem  # e.g. "mechanics_kaldheim"

        # Extract set name from header if present
        set_name = doc_name
        if text.startswith("Set Mechanics Article:"):
            first_line = text.split("\n", 1)[0]
            set_name = first_line.replace("Set Mechanics Article:", "").strip()

        # Try to split by mechanic headings (all-caps lines or lines that
        # look like section headers: short lines followed by longer content)
        sections = self._split_mechanics_by_heading(text)

        if len(sections) > 1:
            return self._chunk_mechanic_sections(
                sections, doc_name, set_name
            )

        # Fallback: use standard article chunking at tier 2
        return self.chunk_article(article_path, tier=2)

    def _split_mechanics_by_heading(
        self, text: str
    ) -> list[tuple[str, str]]:
        """Split mechanics article text into (heading, body) pairs.

        Detects headings by looking for short lines (< 60 chars) that
        are followed by longer explanatory text, or lines in ALL CAPS.

        Returns:
            List of (heading, body) tuples. If no headings found,
            returns a single ("", full_text) entry.
        """
        lines = text.split("\n")
        sections: list[tuple[str, str]] = []
        current_heading = ""
        current_body: list[str] = []

        for line in lines:
            stripped = line.strip()
            # Skip header metadata
            if stripped.startswith("Set Mechanics Article:"):
                continue
            if stripped.startswith("Source:"):
                continue
            if stripped == "---":
                continue

            # Detect heading: short line, not empty, followed by content
            is_heading = (
                len(stripped) > 2
                and len(stripped) < 60
                and not stripped.endswith(".")
                and not stripped.endswith(",")
                and (
                    stripped.isupper()
                    or (stripped[0].isupper() and stripped.count(" ") < 8)
                )
                and not any(
                    stripped.lower().startswith(w)
                    for w in ("the ", "a ", "an ", "if ", "when ", "for ")
                )
            )

            if is_heading and current_body:
                body_text = "\n".join(current_body).strip()
                if body_text:
                    sections.append((current_heading, body_text))
                current_heading = stripped
                current_body = []
            elif is_heading and not current_body:
                current_heading = stripped
            else:
                current_body.append(line)

        # Last section
        if current_body:
            body_text = "\n".join(current_body).strip()
            if body_text:
                sections.append((current_heading, body_text))

        return sections

    def _chunk_mechanic_sections(
        self,
        sections: list[tuple[str, str]],
        doc_name: str,
        set_name: str,
    ) -> list[Chunk]:
        """Create chunks from mechanic sections, merging small ones.

        Args:
            sections: List of (heading, body) pairs.
            doc_name: Document identifier for the chunk.
            set_name: Human-readable set name for context.

        Returns:
            List of Chunks at tier 2.
        """
        target_chars = int(self.TARGET_CHUNK_TOKENS * self.CHARS_PER_TOKEN)
        chunks: list[Chunk] = []

        pending_content: list[str] = []
        pending_section: str | None = None
        pending_length = 0

        for heading, body in sections:
            section_text = f"{heading}\n\n{body}" if heading else body
            context_text = f"[{set_name}] {section_text}"

            if pending_length + len(context_text) > target_chars and pending_content:
                # Flush pending
                chunks.append(
                    Chunk(
                        id=str(uuid.uuid4()),
                        document=doc_name,
                        section=pending_section,
                        tier=2,
                        content="\n\n".join(pending_content),
                    )
                )
                pending_content = []
                pending_length = 0
                pending_section = None

            pending_content.append(context_text)
            pending_length += len(context_text)
            if pending_section is None and heading:
                pending_section = heading

        # Flush remaining
        if pending_content:
            chunks.append(
                Chunk(
                    id=str(uuid.uuid4()),
                    document=doc_name,
                    section=pending_section,
                    tier=2,
                    content="\n\n".join(pending_content),
                )
            )

        logger.info(
            "Chunked mechanics article '%s' into %d chunks",
            doc_name, len(chunks),
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
