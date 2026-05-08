"""Reference document ingestion (D3.1, D3.2, D3.3).

Downloads and stores reference documents:
- WotC Comprehensive Rules
- Commander-specific rules
- Curated strategic articles
"""

import logging
import re
from pathlib import Path
from typing import Any

import httpx
import yaml

from sabermetrics.errors import NetworkError
from sabermetrics.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

COMPREHENSIVE_RULES_URL = "https://media.wizards.com/2026/downloads/MagicCompRules%2020260227.txt"
COMMANDER_RULES_URL = "https://mtgcommander.net/index.php/rules/"


class ReferenceIngestion:
    """Downloads and stores reference documents for RAG grounding."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.reference_dir = data_dir / "reference"
        self.reference_dir.mkdir(parents=True, exist_ok=True)
        self._rate_limiter = RateLimiter(requests_per_second=1.0)

    def fetch_comprehensive_rules(self) -> Path:
        """Download WotC Comprehensive Rules.

        Returns:
            Path to the saved rules file.
        """
        output_path = self.reference_dir / "comprehensive_rules.txt"

        logger.info("Downloading Comprehensive Rules...")
        self._rate_limiter.wait()

        # Try multiple known URL patterns
        urls = [
            COMPREHENSIVE_RULES_URL,
            "https://media.wizards.com/2025/downloads/MagicCompRules%2020250606.txt",
            "https://media.wizards.com/2025/downloads/MagicCompRules.txt",
            "https://media.wizards.com/2024/downloads/MagicCompRules.txt",
        ]

        for url in urls:
            try:
                resp = httpx.get(url, timeout=60, follow_redirects=True)
                if resp.status_code == 200 and len(resp.text) > 10000:
                    output_path.write_text(resp.text, encoding="utf-8")
                    logger.info(
                        "Comprehensive Rules saved (%d bytes)", len(resp.text)
                    )
                    return output_path
            except httpx.HTTPError:
                continue

        raise NetworkError("Failed to download Comprehensive Rules from any URL")

    def fetch_commander_rules(self) -> Path:
        """Download Commander-specific rules.

        Returns:
            Path to the saved rules file.
        """
        output_path = self.reference_dir / "commander_rules.txt"

        logger.info("Downloading Commander rules...")
        self._rate_limiter.wait()

        try:
            resp = httpx.get(
                COMMANDER_RULES_URL,
                timeout=30,
                follow_redirects=True,
                headers={"User-Agent": "Sabermetrics/1.0 (personal research)"},
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise NetworkError(f"Failed to download Commander rules: {e}") from e

        # Extract text content from HTML
        text = self._extract_text_from_html(resp.text)
        output_path.write_text(text, encoding="utf-8")
        logger.info("Commander rules saved (%d bytes)", len(text))
        return output_path

    def fetch_strategic_articles(self, config_path: Path) -> list[Path]:
        """Fetch curated strategic articles.

        Args:
            config_path: Path to strategic_articles.yaml config file.

        Returns:
            List of paths to saved article files.
        """
        if not config_path.exists():
            logger.warning("No strategic_articles.yaml found at %s", config_path)
            return []

        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        articles = config.get("articles", [])
        saved: list[Path] = []

        for article in articles:
            url = article.get("url", "")
            slug = article.get("slug", "")
            if not url or not slug:
                continue

            output_path = self.reference_dir / f"article_{slug}.txt"
            if output_path.exists():
                saved.append(output_path)
                continue

            try:
                self._rate_limiter.wait()
                resp = httpx.get(
                    url,
                    timeout=30,
                    follow_redirects=True,
                    headers={"User-Agent": "Sabermetrics/1.0 (personal research)"},
                )
                resp.raise_for_status()
                text = self._extract_text_from_html(resp.text)
                output_path.write_text(text, encoding="utf-8")
                saved.append(output_path)
                logger.info("Saved article '%s' (%d bytes)", slug, len(text))
            except Exception as e:
                logger.warning("Failed to fetch article '%s': %s", slug, e)

        return saved

    def fetch_set_mechanics_articles(self, config_path: Path) -> list[Path]:
        """Fetch WotC set mechanics articles.

        Downloads mechanics articles from magic.wizards.com and caches
        them locally. Uses politeness rate limiting (1 req/sec).

        Args:
            config_path: Path to set_mechanics_articles.yaml config file.

        Returns:
            List of paths to saved article files.
        """
        if not config_path.exists():
            logger.warning(
                "No set_mechanics_articles.yaml found at %s", config_path
            )
            return []

        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        articles = config.get("articles", [])
        saved: list[Path] = []
        failed: list[str] = []

        for article in articles:
            url = article.get("url", "")
            slug = article.get("slug", "")
            set_name = article.get("set_name", slug)
            if not url or not slug:
                continue

            output_path = self.reference_dir / f"mechanics_{slug}.txt"
            if output_path.exists() and output_path.stat().st_size > 500:
                saved.append(output_path)
                continue

            try:
                self._rate_limiter.wait()
                resp = httpx.get(
                    url,
                    timeout=30,
                    follow_redirects=True,
                    headers={
                        "User-Agent": "Sabermetrics/1.0 (personal research)",
                    },
                )
                if resp.status_code == 404:
                    logger.warning(
                        "Mechanics article not found (404): %s", slug
                    )
                    failed.append(slug)
                    continue
                resp.raise_for_status()
                text = self._extract_text_from_html(resp.text)

                # Prepend set name header for context in RAG chunks
                header = (
                    f"Set Mechanics Article: {set_name}\n"
                    f"Source: {url}\n"
                    f"---\n\n"
                )
                output_path.write_text(header + text, encoding="utf-8")
                saved.append(output_path)
                logger.info(
                    "Saved mechanics article '%s' (%d bytes)",
                    slug, len(text),
                )
            except Exception as e:
                logger.warning(
                    "Failed to fetch mechanics article '%s': %s", slug, e
                )
                failed.append(slug)

        logger.info(
            "Mechanics articles: %d saved, %d failed", len(saved), len(failed)
        )
        if failed:
            logger.warning("Failed slugs: %s", ", ".join(failed))

        return saved

    @staticmethod
    def _extract_text_from_html(html: str) -> str:
        """Extract readable text from HTML content.

        Strips navigation, header, footer, and aside elements before
        extracting text to avoid polluting content with menu/chrome text.
        """
        # Remove non-content structural elements
        text = html
        for tag in ("nav", "header", "footer", "aside"):
            text = re.sub(
                rf"<{tag}[^>]*>.*?</{tag}>", "", text, flags=re.DOTALL
            )
        # Remove script and style elements
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        # Remove HTML tags
        text = re.sub(r"<[^>]+>", "\n", text)
        # Clean up whitespace
        text = re.sub(r"\n\s*\n", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        # Decode HTML entities
        text = text.replace("&amp;", "&")
        text = text.replace("&lt;", "<")
        text = text.replace("&gt;", ">")
        text = text.replace("&quot;", '"')
        text = text.replace("&#39;", "'")
        text = text.replace("&nbsp;", " ")
        return text.strip()
