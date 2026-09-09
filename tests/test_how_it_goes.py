"""How-it-goes step numbers must sit on the heading's first line."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from sabermetrics import db
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"

_STEPS = (
    ("01", "Pick a commander"),
    ("02", "Take the staples"),
    ("03", "Arrange the deck"),
)


def _login(client, user_id: str) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True


def _home_html(tmp_path, monkeypatch) -> str:
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    path = tmp_path / "how-it-goes.db"
    setup_database(path)
    user = db.UsersRepo(path).create(
        email="how@example.test",
        display_name="How",
        role="user",
        status="active",
    )
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    _login(client, user)
    response = client.get("/")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _rule_body(css: str, selector: str) -> str | None:
    match = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", css)
    return match.group(1) if match else None


def _decls(css: str, selector: str) -> dict[str, str]:
    body = _rule_body(css, selector)
    if body is None:
        return {}
    out: dict[str, str] = {}
    for part in body.split(";"):
        if ":" not in part:
            continue
        prop, value = part.split(":", 1)
        out[prop.strip()] = re.sub(r"\s+", " ", value.strip())
    return out


def _media_block(css: str, query: str) -> str:
    token = f"@media ({query})"
    start = css.find(token)
    assert start >= 0, f"missing {token}"
    brace = css.find("{", start)
    depth = 0
    for index in range(brace, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[brace + 1 : index]
    raise AssertionError(f"unclosed {token}")


def _font_px(decls: dict[str, str], default: float) -> float:
    font = decls.get("font", "")
    match = re.search(r"(\d+(?:\.\d+)?)px", font)
    if match:
        return float(match.group(1))
    size = decls.get("font-size", "")
    match = re.search(r"(\d+(?:\.\d+)?)px", size)
    return float(match.group(1)) if match else default


def _line_height_px(decls: dict[str, str], font_px: float) -> float:
    font = decls.get("font", "")
    match = re.search(r"(\d+(?:\.\d+)?)px\s*/\s*(\d+(?:\.\d+)?)(px)?", font)
    if match:
        value = float(match.group(2))
        return value if match.group(3) else font_px * value
    raw = decls.get("line-height")
    if raw in (None, "normal"):
        return font_px * 1.2
    if raw.endswith("px"):
        return float(raw[:-2])
    return font_px * float(raw)


def _baseline_from_line_top(font_px: float, line_height_px: float) -> float:
    extra = max(line_height_px - font_px, 0.0)
    return extra / 2 + font_px * 0.8


class _StepParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.steps: list[dict[str, object]] = []
        self._step: dict[str, object] | None = None
        self._path: list[tuple[str, tuple[str, ...]]] = []
        self._capture: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = tuple((dict(attrs).get("class") or "").split())
        self._path.append((tag, classes))
        if tag == "article" and "dl-step" in classes:
            self._step = {"number": "", "heading": "", "copy": "", "group": None}
            self.steps.append(self._step)
        if self._step is None:
            return
        if tag == "b":
            self._capture = "number"
            self._text = []
        elif tag == "h3":
            self._capture = "heading"
            self._text = []
            parents = self._path[:-1]
            article_at = next(
                index
                for index in range(len(parents) - 1, -1, -1)
                if parents[index][0] == "article" and "dl-step" in parents[index][1]
            )
            group = (
                parents[article_at + 1 :][-1]
                if article_at + 1 < len(parents)
                else parents[article_at]
            )
            self._step["group"] = group
        elif tag == "p":
            self._capture = "copy"
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        if self._capture and self._step is not None:
            self._step[self._capture] = "".join(self._text).strip()
            self._capture = None
        if self._path and self._path[-1][0] == tag:
            self._path.pop()

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._text.append(data)


def _parsed_steps(html: str) -> list[dict[str, object]]:
    start = html.find('class="dl-how"')
    assert start >= 0, "home page is missing How it goes"
    parser = _StepParser()
    parser.feed(html[start:])
    assert len(parser.steps) == 3
    return parser.steps


def _selector(tag: str, classes: tuple[str, ...]) -> str:
    return tag + "".join(f".{name}" for name in classes)


def _first_line_baselines(
    css: str, group_selector: str, number_sel: str, heading_sel: str
) -> tuple[float, float]:
    group = _decls(css, group_selector)
    number = _decls(css, number_sel)
    heading = _decls(css, heading_sel)
    num_px = _font_px(number, 13.0)
    heading_px = _font_px(heading, 16.0 * 1.17)
    num_lh = _line_height_px(number, num_px)
    heading_lh = _line_height_px(heading, heading_px)
    num_base = _baseline_from_line_top(num_px, num_lh)
    heading_base = _baseline_from_line_top(heading_px, heading_lh)
    display = group.get("display", "block")
    align = group.get("align-items", "stretch")
    number_self = number.get("align-self", "auto")
    heading_self = heading.get("align-self", "auto")
    used_number = align if number_self == "auto" else number_self
    used_heading = align if heading_self == "auto" else heading_self
    shared = {"baseline", "first baseline"}
    if display in {"flex", "inline-flex"} and used_number in shared:
        line = max(num_base, heading_base)
        return line, line
    if display == "grid" and used_number in shared and used_heading in shared:
        line = max(num_base, heading_base)
        return line, line
    return num_base, heading_base


def test_how_it_goes_number_shares_heading_first_line_baseline(
    tmp_path, monkeypatch
) -> None:
    html = _home_html(tmp_path, monkeypatch)
    css = CSS_PATH.read_text()
    for step, (number, heading) in zip(_parsed_steps(html), _STEPS, strict=True):
        assert step["number"] == number
        assert step["heading"] == heading
        assert str(step["copy"]).startswith(("Start", "Add", "Sort"))
        group = step["group"]
        assert isinstance(group, tuple)
        group_tag, group_classes = group
        assert (
            "dl-step" not in group_classes
        ), "body copy must not share the number/heading row"
        group_sel = _selector(group_tag, group_classes)
        class_sel = "".join(f".{name}" for name in group_classes)
        number_sel = (
            f"{class_sel} b" if _decls(css, f"{class_sel} b") else f"{group_sel} b"
        )
        heading_sel = (
            f"{class_sel} h3" if _decls(css, f"{class_sel} h3") else f"{group_sel} h3"
        )
        number_y, heading_y = _first_line_baselines(
            css, class_sel or group_sel, number_sel, heading_sel
        )
        assert abs(number_y - heading_y) < 0.5
        group_decls = _decls(css, class_sel) or _decls(css, group_sel)
        assert group_decls.get("display") in {"flex", "inline-flex"}
        heading_decls = _decls(css, heading_sel)
        wrap = group_decls.get("flex-wrap", "nowrap")
        assert wrap == "nowrap"
        assert heading_decls.get("min-width") == "0"


def test_how_it_goes_alignment_holds_when_the_grid_stacks(
    tmp_path, monkeypatch
) -> None:
    html = _home_html(tmp_path, monkeypatch)
    css = CSS_PATH.read_text()
    mobile = _media_block(css, "max-width: 767px")
    assert ".dl-how-grid" in mobile
    how_grid = _decls(mobile, ".dl-how-grid")
    assert how_grid.get("grid-template-columns") == "1fr"
    steps = _parsed_steps(html)
    group = steps[0]["group"]
    assert isinstance(group, tuple)
    group_tag, group_classes = group
    assert (
        "dl-step" not in group_classes
    ), "body copy must not share the number/heading row"
    class_sel = "".join(f".{name}" for name in group_classes)
    mobile_group = _decls(mobile, class_sel) or _decls(
        mobile, _selector(group_tag, group_classes)
    )
    assert "align-items" not in mobile_group
    assert "display" not in mobile_group
    number_sel = f"{class_sel} b"
    heading_sel = f"{class_sel} h3"
    number_y, heading_y = _first_line_baselines(css, class_sel, number_sel, heading_sel)
    assert abs(number_y - heading_y) < 0.5
    heading_decls = _decls(css, heading_sel)
    assert heading_decls.get("min-width") == "0"
