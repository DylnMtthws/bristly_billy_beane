"""Builder chrome layout: commander selection belongs in deck options."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"
BUILDER_TEMPLATE = (
    ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab" / "builder.html"
)


def _rule_body(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", css)
    assert match, f"missing CSS rule for {selector}"
    return match.group(1)


def test_choose_commanders_button_is_in_deck_options_not_card_search() -> None:
    """Commander selection is a deck action, not an add-card search action."""
    html = BUILDER_TEMPLATE.read_text()
    panel = re.search(r'<aside class="dl-add-panel"[^>]*>([\s\S]*?)</aside>', html)
    assert panel is not None
    assert "data-commanders-open" not in panel.group(1)
    assert re.search(
        r'<details class="dl-decklist-more">[\s\S]*?'
        r'<summary aria-label="Deck options">•••</summary>[\s\S]*?'
        r'<button type="button" data-commanders-open>'
        r"Choose commanders / partner</button>",
        html,
    )
    body = _rule_body(CSS_PATH.read_text(), ".dl-decklist-more")
    assert re.search(r"display\s*:\s*flex", body)
