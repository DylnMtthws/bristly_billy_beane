"""Builder chrome layout: the commanders opener must fit the add-cards rail."""

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


def test_choose_commanders_button_is_inset_and_compact_in_add_panel() -> None:
    """The add-rail opener was full-bleed 16px text in a 220–250px column."""
    html = BUILDER_TEMPLATE.read_text()
    assert re.search(
        r'<aside class="dl-add-panel"[^>]*>[\s\S]*?'
        r'<button class="dl-button"[^>]*data-commanders-open[^>]*>'
        r"Choose commanders / partner</button>",
        html,
    )
    body = _rule_body(CSS_PATH.read_text(), ".dl-add-panel [data-commanders-open]")
    assert re.search(r"margin\s*:\s*0\s+14px\s+10px", body)
    font = re.search(r"font-size\s*:\s*(\d+(?:\.\d+)?)px", body)
    assert font is not None and float(font.group(1)) <= 12
    assert re.search(r"white-space\s*:\s*nowrap", body)
    assert re.search(r"flex\s*:\s*none", body)
    min_height = re.search(r"min-height\s*:\s*(\d+(?:\.\d+)?)px", body)
    assert min_height is not None and float(min_height.group(1)) <= 36
