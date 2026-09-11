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
    assert "dl-card-combobox" in html
    assert "dl-add-panel" not in html
    combobox_start = html.index("dl-card-combobox")
    more_start = html.index('class="dl-decklist-more"')
    assert "data-commanders-open" not in html[combobox_start:more_start]
    assert re.search(
        r'<details class="dl-decklist-more">[\s\S]*?'
        r'<summary aria-label="Deck options">•••</summary>[\s\S]*?'
        r'<button type="button" data-commanders-open>'
        r"Choose commanders / partner</button>",
        html,
    )
    body = _rule_body(CSS_PATH.read_text(), ".dl-decklist-more")
    assert re.search(r"display\s*:\s*flex", body)
