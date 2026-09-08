"""Playmat commander zone must widen for partner pairs."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"

CARD_WIDTH = 132
CARD_GAP = 7
ZONE_CHROME = 22
SINGLE_MIN = 170


def _command_zone_width(commander_count: int) -> int:
    return max(
        SINGLE_MIN,
        ZONE_CHROME
        + commander_count * CARD_WIDTH
        + max(0, commander_count - 1) * CARD_GAP,
    )


def test_commander_zone_grows_for_partner_commanders() -> None:
    """A partner pair is two 132px cards; a 170px zone cannot show both."""
    css = CSS_PATH.read_text()
    pinned = [
        body
        for body in re.findall(r"(?<![\w.-])\.dl-mat-command\s*\{([^}]+)\}", css)
        if re.search(r"width\s*:\s*\d+px\s*!important", body)
    ]
    assert not pinned, "commander zone must not pin a single-card width"

    js = BUILDER_JS.read_text()
    start = js.find('var commandBox = node("section", "dl-mat-zone dl-mat-command")')
    assert start >= 0, "missing commander zone render"
    end = js.find("mat.appendChild(commandBox)", start)
    assert end > start
    body = js[start:end]
    assign = re.search(r"commandBox\.style\.width\s*=\s*([^;]+);", body)
    assert assign, "commander zone must set width from commander count"
    expr = assign.group(1)
    assert "commanders.length" in expr
    assert str(CARD_WIDTH) in expr
    assert str(CARD_GAP) in expr
    assert str(SINGLE_MIN) in expr

    assert _command_zone_width(1) == SINGLE_MIN
    assert _command_zone_width(2) == 293
    assert _command_zone_width(2) > _command_zone_width(1)
