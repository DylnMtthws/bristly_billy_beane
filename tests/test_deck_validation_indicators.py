"""DYL-51: deck count and per-entry legality indicators."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.deck_documents import DeckDocumentRepo
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
BUILDER_HTML = (
    ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab" / "builder.html"
)
CSS_PATH = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"
VALIDATION_CSS = (
    ROOT / "src" / "sabermetrics" / "ui" / "static" / "feedback-validation.css"
)


def _entry(**kwargs):
    card = {
        "name": "Card",
        "quantity": 1,
        "is_commander": False,
        "type_line": "Creature",
        "oracle_text": "",
        "color_identity": [],
        "zone_name": "Unsorted",
    }
    card.update(kwargs)
    return card


def _commander(**kwargs):
    values = {
        "name": "Test Commander",
        "is_commander": True,
        "type_line": "Legendary Creature",
        "oracle_text": "",
        "color_identity": ["U"],
        "id": "cmd",
        "oracle_id": "o-cmd",
        "commander_legal": True,
        "format_legal": True,
    }
    values.update(kwargs)
    return _entry(**values)


def _library(n, **kwargs):
    extras = dict(kwargs)
    prefix = extras.pop("prefix", "Spell")
    return [
        _entry(
            name=f"{prefix} {i}",
            id=f"{prefix.lower()}-{i}",
            oracle_id=f"o-{prefix.lower()}-{i}",
            format_legal=True,
            color_identity=[],
            **extras,
        )
        for i in range(n)
    ]


def _codes(entry):
    return [item["code"] for item in entry.get("validation_issues") or []]


def test_total_count_99_100_101_are_separate_from_card_issues():
    for library, expected in ((98, 99), (99, 100), (100, 101)):
        entries = [_commander()] + _library(library)
        result = DeckDocumentRepo.validate(entries)
        assert result["total_count"] == expected
        assert result["commander_count"] == 1
        assert result["library_count"] == library
        assert result["library_target"] == 99
        size_issue = f"The library has {library} of 99 cards."
        if expected == 100:
            assert size_issue not in result["issues"]
        else:
            assert size_issue in result["issues"]
        assert all("copies" not in _codes(entry) for entry in entries)
        assert all("color_identity" not in _codes(entry) for entry in entries)


def test_partner_pair_counts_as_100_with_98_library():
    entries = [
        _commander(oracle_text="Partner", color_identity=["U"]),
        _commander(
            name="Second Partner",
            id="cmd2",
            oracle_id="o-cmd2",
            oracle_text="Partner",
            color_identity=["G"],
        ),
    ] + _library(98)
    result = DeckDocumentRepo.validate(entries)
    assert result["commander_count"] == 2
    assert result["library_target"] == 98
    assert result["total_count"] == 100
    assert not any("recognized legal pair" in issue for issue in result["issues"])


def test_sideboard_and_maybeboard_are_excluded_from_count_and_card_flags():
    entries = (
        [_commander()]
        + _library(99)
        + [
            _entry(
                name="Side Bolt",
                id="side",
                oracle_id="o-side",
                quantity=4,
                zone_name="Sideboard",
                color_identity=["R"],
                format_legal=False,
            ),
            _entry(
                name="Maybe Bolt",
                id="maybe",
                oracle_id="o-maybe",
                quantity=2,
                zone_name="Maybeboard",
            ),
        ]
    )
    result = DeckDocumentRepo.validate(entries)
    assert result["total_count"] == 100
    assert result["library_count"] == 99
    assert entries[-2]["validation_issues"] == []
    assert entries[-1]["validation_issues"] == []
    assert "side" not in result["entry_issues"]
    assert "maybe" not in result["entry_issues"]


def test_split_entries_and_printings_aggregate_by_oracle_identity():
    entries = [
        _commander(),
        _entry(
            name="Sol Ring",
            id="ring-a",
            oracle_id="o-ring",
            format_legal=True,
        ),
        _entry(
            name="Sol Ring",
            id="ring-b",
            oracle_id="o-ring",
            format_legal=True,
        ),
    ]
    result = DeckDocumentRepo.validate(entries)
    assert "Commander singleton rule is exceeded." in result["issues"]
    assert _codes(entries[1]) == ["copies"]
    assert _codes(entries[2]) == ["copies"]
    assert set(result["entry_issues"]) == {"ring-a", "ring-b"}

    named = [
        _commander(),
        _entry(name="Clone", id="a", format_legal=True),
        _entry(name="Clone", id="b", format_legal=True),
    ]
    named_result = DeckDocumentRepo.validate(named)
    assert "Commander singleton rule is exceeded." in named_result["issues"]
    assert _codes(named[1]) == ["copies"]


def test_commander_duplicate_in_library_counts_toward_copies():
    entries = [
        _commander(name="Kinnan Test", oracle_id="o-cmd"),
        _entry(
            name="Kinnan Test",
            id="lib-cmd",
            oracle_id="o-cmd",
            format_legal=True,
        ),
    ]
    result = DeckDocumentRepo.validate(entries)
    assert "Commander singleton rule is exceeded." in result["issues"]
    assert "copies" in _codes(entries[0])
    assert "copies" in _codes(entries[1])


def test_basic_snow_unlimited_and_finite_oracle_limits():
    basics = [_commander()] + [
        _entry(
            name="Snow-Covered Island",
            id="snow",
            type_line="Basic Snow Land — Island",
            quantity=20,
            color_identity=["U"],
            format_legal=True,
        )
    ]
    basic_result = DeckDocumentRepo.validate(basics)
    assert "copies" not in _codes(basics[1])
    assert "Commander singleton rule is exceeded." not in basic_result["issues"]

    rats = [_commander()] + [
        _entry(
            name="Relentless Rats",
            id="rats",
            oracle_text="A deck can have any number of cards named Relentless Rats.",
            quantity=12,
            format_legal=True,
        )
    ]
    assert DeckDocumentRepo.validate(rats)["entry_issues"] == {}

    def dwarves(count):
        return [
            _commander(color_identity=["R"]),
            _entry(
                name="Seven Dwarves",
                id="dwarves",
                oracle_text="A deck can have up to seven cards named Seven Dwarves.",
                quantity=count,
                color_identity=["R"],
                format_legal=True,
            ),
        ]

    under = dwarves(6)
    at = dwarves(7)
    over = dwarves(8)
    assert DeckDocumentRepo.validate(under)["entry_issues"] == {}
    assert DeckDocumentRepo.validate(at)["entry_issues"] == {}
    over_result = DeckDocumentRepo.validate(over)
    assert "Commander singleton rule is exceeded." in over_result["issues"]
    assert over[1]["validation_issues"][0]["message"] == (
        "A deck can have at most 7 cards named Seven Dwarves."
    )

    nazgul = [
        _commander(color_identity=["B"]),
        _entry(
            name="Nazgûl",
            id="naz",
            oracle_text="A deck can have up to nine cards named Nazgûl.",
            quantity=9,
            color_identity=["B"],
            format_legal=True,
        ),
        _entry(
            name="Nazgûl",
            id="naz-2",
            oracle_id="o-naz",
            oracle_text="A deck can have up to nine cards named Nazgûl.",
            quantity=1,
            color_identity=["B"],
            format_legal=True,
        ),
    ]
    nazgul[1]["oracle_id"] = "o-naz"
    over_nine = DeckDocumentRepo.validate(nazgul)
    assert over_nine["entry_issues"]
    assert (
        "at most 9 cards named Nazgûl" in nazgul[1]["validation_issues"][0]["message"]
    )


def test_color_identity_is_union_and_ignores_mana_cost_and_missing_commander():
    entries = [
        _commander(color_identity=["U"], oracle_text="Partner"),
        _commander(
            name="Green Partner",
            id="cmd2",
            oracle_id="o-cmd2",
            oracle_text="Partner",
            color_identity=["G"],
        ),
        _entry(
            name="Birds",
            id="birds",
            color_identity=["U"],
            format_legal=True,
        ),
        _entry(
            name="Colorless Rock",
            id="rock",
            color_identity=[],
            mana_cost="{R}",
            format_legal=True,
        ),
        _entry(
            name="Bolt",
            id="bolt",
            color_identity=["R"],
            mana_cost="{R}",
            format_legal=True,
        ),
    ]
    result = DeckDocumentRepo.validate(entries)
    assert "color_identity" not in _codes(entries[2])
    assert "color_identity" not in _codes(entries[3])
    assert _codes(entries[4]) == ["color_identity"]
    assert "A card is outside the commander's color identity." in result["issues"]

    missing = [_entry(name="Bolt", id="bolt", color_identity=["R"], format_legal=True)]
    missing_result = DeckDocumentRepo.validate(missing)
    assert "Choose one commander" in " ".join(missing_result["issues"])
    assert missing[0]["validation_issues"] == []


def test_format_and_commander_legality_are_per_entry():
    entries = [
        _commander(commander_legal=False, format_legal=False),
        _entry(name="Banned", id="ban", format_legal=False),
        _entry(name="Unknown", id="unk", format_legal=None),
        _entry(name="Legal", id="ok", format_legal=True),
    ]
    result = DeckDocumentRepo.validate(entries)
    assert entries[0]["validation_issues"][0]["code"] == "commander"
    assert entries[1]["validation_issues"][0]["code"] == "format"
    assert "unknown" in entries[2]["validation_issues"][0]["message"].casefold()
    assert entries[3]["validation_issues"] == []
    assert any("not Commander-legal" in issue for issue in result["issues"])
    assert any("commander" in issue.casefold() for issue in result["issues"])


def test_get_annotates_entries_after_mutation(tmp_path):
    path = tmp_path / "documents.db"
    setup_database(path)
    owner = db.UsersRepo(path).create(
        email="owner@example.test", display_name="Owner", status="active"
    )
    with db.connect(path) as conn:
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    "commander",
                    "oracle-commander",
                    "Kinnan Test",
                    "{G}{U}",
                    2,
                    "Legendary Creature — Human Druid",
                    "Mana",
                    '["G","U"]',
                    1,
                    1,
                ),
                (
                    "ring",
                    "oracle-ring",
                    "Sol Ring",
                    "{1}",
                    1,
                    "Artifact",
                    "Add mana",
                    "[]",
                    0,
                    1,
                ),
                (
                    "banned",
                    "oracle-banned",
                    "Black Lotus",
                    "{0}",
                    0,
                    "Artifact",
                    "Mana",
                    "[]",
                    0,
                    0,
                ),
            ],
        )
        conn.commit()
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner, commander_card_id="commander")
    unsorted = repo.get(owner, deck_id)["zones"][0]
    added = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=0,
        mutation_id="add-ring",
        commands=[{"type": "add_card", "card_id": "ring", "zone_id": unsorted["id"]}],
    )
    ring = next(entry for entry in added["entries"] if entry["name"] == "Sol Ring")
    doubled = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=added["revision"],
        mutation_id="dup-ring",
        commands=[{"type": "adjust_quantity", "entry_id": ring["id"], "delta": 1}],
    )
    ring = next(entry for entry in doubled["entries"] if entry["name"] == "Sol Ring")
    assert ring["quantity"] == 2
    assert ring["validation_issues"]
    assert doubled["validation"]["entry_issues"][ring["id"]]
    assert "Commander singleton rule is exceeded." in doubled["validation"]["issues"]

    with db.connect(path) as conn:
        conn.execute(
            """INSERT INTO deck_entries
            (id,deck_id,zone_id,card_id,oracle_id,name,quantity,is_commander,
             sort_order,type_line,oracle_text,color_identity)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "banned-entry",
                deck_id,
                unsorted["id"],
                "banned",
                "oracle-banned",
                "Black Lotus",
                1,
                0,
                9,
                "Artifact",
                "Mana",
                "[]",
            ),
        )
        conn.commit()
    loaded = repo.get(owner, deck_id)
    banned = next(
        entry for entry in loaded["entries"] if entry["name"] == "Black Lotus"
    )
    assert banned["format_legal"] is False
    assert any(item["code"] == "format" for item in banned["validation_issues"])


def test_builder_template_exposes_shared_toolbar_count():
    html = BUILDER_HTML.read_text()
    toolbar = html.split('class="dl-builder-toolbar"', 1)[1]
    assert "data-deck-count" in toolbar
    combobox = toolbar.split("{% if not shared %}", 1)[1].split("{% endif %}", 1)[0]
    assert "data-deck-count" not in combobox
    assert "data-deck-count" in toolbar.split("{% endif %}", 1)[1]
    count_open = toolbar.split("data-deck-count", 1)[0].rsplit("<span", 1)[-1]
    assert "data-table-only" not in count_open
    assert "data-playmat-only" not in count_open
    assert "feedback-validation.css" in CSS_PATH.read_text()
    css = VALIDATION_CSS.read_text()
    assert ".dl-deck-count.is-invalid" in css
    assert ".dl-card-invalid" in css
    source = BUILDER_JS.read_text()
    render = source.split("function render() {", 1)[1].split("\n", 1)[0]
    assert "renderDeckCount();" in render
    assert "state = body" in source and "render();" in source
    assert "applyCardValidity" in source


HARNESS = r"""
import fs from "node:fs";
import vm from "node:vm";

const builderPath = process.argv[2];
const source = fs.readFileSync(builderPath, "utf8");

class ClassList {
  constructor(el) { this.el = el; }
  _parts() { return (this.el.className || "").split(/\s+/).filter(Boolean); }
  _set(parts) { this.el.className = [...new Set(parts)].join(" "); }
  add(...names) { this._set(this._parts().concat(names)); }
  remove(...names) { const drop = new Set(names); this._set(this._parts().filter((n) => !drop.has(n))); }
  contains(name) { return this._parts().includes(name); }
  toggle(name, force) {
    if (force === true) this.add(name);
    else if (force === false) this.remove(name);
    else if (this.contains(name)) this.remove(name);
    else this.add(name);
    return this.contains(name);
  }
}
class Style {
  constructor() { this._props = {}; }
  setProperty(name, value) { this._props[name] = String(value); this[name] = String(value); }
}
function camelToData(key) { return "data-" + key.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase()); }
class Element {
  constructor(tag, owner) {
    this.tagName = String(tag).toUpperCase();
    this.ownerDocument = owner;
    this.children = [];
    this.childNodes = this.children;
    this.parentNode = null;
    this.attributes = {};
    this.style = new Style();
    this.className = "";
    this.classList = new ClassList(this);
    this.listeners = {};
    this._text = "";
    this.hidden = false;
    this.id = "";
    this.type = "";
    this.value = "";
    this.title = "";
    this.draggable = false;
    this.disabled = false;
    this.checked = false;
    this.tabIndex = 0;
    this.src = "";
    this.alt = "";
    const self = this;
    this.dataset = new Proxy({}, {
      get(obj, key) {
        if (typeof key !== "string") return undefined;
        return key in obj ? obj[key] : (self.attributes[camelToData(key)] || "");
      },
      set(obj, key, value) {
        obj[key] = String(value);
        self.attributes[camelToData(key)] = String(value);
        return true;
      }
    });
  }
  get textContent() {
    if (this.children.length) return this.children.map((c) => c.textContent).join("");
    return this._text;
  }
  set textContent(value) { this.children.length = 0; this._text = String(value); }
  set innerHTML(value) { this._text = String(value); }
  get innerHTML() { return this._text; }
  setAttribute(name, value) {
    const key = String(name);
    this.attributes[key] = String(value);
    if (key === "id") this.id = String(value);
    if (key === "class") this.className = String(value);
    if (key === "title") this.title = String(value);
  }
  getAttribute(name) {
    const key = String(name);
    if (key === "id") return this.id || null;
    if (key === "class") return this.className || null;
    if (key === "title") return this.title || null;
    return this.attributes[key] != null ? this.attributes[key] : null;
  }
  hasAttribute(name) { return this.getAttribute(name) != null; }
  removeAttribute(name) { delete this.attributes[String(name)]; }
  appendChild(child) {
    if (typeof child === "string") { const t = this.ownerDocument.createElement("#text"); t.textContent = child; child = t; }
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  append(...nodes) { nodes.forEach((n) => this.appendChild(n)); }
  replaceChildren(...nodes) { this.children.forEach((c) => { c.parentNode = null; }); this.children.length = 0; this._text = ""; nodes.forEach((n) => this.appendChild(n)); }
  closest(selector) { let node = this; while (node && node.tagName) { if (matches(node, selector)) return node; node = node.parentNode; } return null; }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  addEventListener(type, fn) { (this.listeners[type] || (this.listeners[type] = [])).push(fn); }
  dispatchEvent(event) {
    event.target = event.target || this;
    (this.listeners[event.type] || []).forEach((fn) => fn(event));
    return true;
  }
}
function matches(el, selector) {
  return String(selector).split(",").map((part) => part.trim()).some((part) => {
    if (part === "*") return true;
    let rest = part, tag = null, id = null, classes = [], attrs = [];
    rest = rest.replace(/^([a-zA-Z][\w-]*)/, (_, t) => { tag = t.toUpperCase(); return ""; });
    rest = rest.replace(/#([\w-]+)/g, (_, v) => { id = v; return ""; });
    rest = rest.replace(/\.([\w-]+)/g, (_, v) => { classes.push(v); return ""; });
    rest.replace(/\[([^\]]+)\]/g, (_, raw) => {
      const eq = raw.indexOf("=");
      if (eq < 0) attrs.push([raw, null]);
      else attrs.push([raw.slice(0, eq), raw.slice(eq + 1).replace(/^["']|["']$/g, "")]);
      return "";
    });
    if (tag && el.tagName !== tag) return false;
    if (id && el.id !== id) return false;
    if (classes.some((name) => !el.classList.contains(name))) return false;
    return attrs.every(([name, value]) => {
      const actual = el.getAttribute(name);
      if (value == null) return actual != null;
      return String(actual) === String(value);
    });
  });
}
function walk(node, acc) {
  acc.push(node);
  (node.children || []).forEach((child) => walk(child, acc));
  return acc;
}
function queryAll(root, selector) {
  const nodes = [];
  walk(root, []).forEach((node) => { if (node !== root && matches(node, selector)) nodes.push(node); });
  nodes.forEach = Array.prototype.forEach;
  nodes.map = Array.prototype.map;
  nodes.filter = Array.prototype.filter;
  nodes.find = Array.prototype.find;
  return nodes;
}

const documentElement = new Element("html");
const head = new Element("head", null);
const body = new Element("body", null);
head.ownerDocument = { createElement(tag) { return new Element(tag); } };
body.ownerDocument = head.ownerDocument;
documentElement.appendChild(head);
documentElement.appendChild(body);
const document = {
  documentElement, body, head, activeElement: null,
  createElement(tag) { const el = new Element(tag, document); el.ownerDocument = document; return el; },
  createTextNode(text) { const el = document.createElement("#text"); el.textContent = String(text == null ? "" : text); return el; },
  createElementNS(_ns, tag) { return document.createElement(tag); },
  getElementById(id) { return walk(documentElement, []).find((el) => el.id === id) || null; },
  querySelector(sel) { return sel === "body" ? body : (queryAll(documentElement, sel)[0] || null); },
  querySelectorAll(sel) { return queryAll(documentElement, sel); },
  addEventListener(type, fn) { documentElement.addEventListener(type, fn); },
  dispatchEvent(event) { return documentElement.dispatchEvent(event); },
};
function el(tag, attrs = {}) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([key, value]) => {
    if (key === "className") node.className = value;
    else if (key === "id") { node.id = value; node.setAttribute("id", value); }
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  });
  return node;
}
function makeEvent(type, extra = {}) { return Object.assign({ type, preventDefault() {}, stopPropagation() {} }, extra); }

const issues = [{ code: "copies", message: "Commander singleton rule is exceeded." }];
const deck = {
  id: "deck-1", title: "Test Deck", revision: 0,
  zones: [{ id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 }],
  entries: [
    { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
    { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 2, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: issues },
  ],
  presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
  preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
  validation: { commander_count: 1, library_count: 2, library_target: 99, total_count: 99, legal: false, issues: ["The library has 2 of 99 cards.", "Commander singleton rule is exceeded."], entry_issues: { "entry-ring": issues } },
  tags: [], tag_suggestions: [],
};

body.appendChild(Object.assign(el("script", { id: "deck-document-data" }), { textContent: JSON.stringify(deck) }));
const root = el("div", { className: "dl-builder", "data-shared": "false", "data-playmat-enabled": "true" });
body.appendChild(root);
head.appendChild(el("meta", { name: "csrf-token", content: "token" }));
body.appendChild(el("button", { id: "save-state", text: "Saved" }));
const toolbar = el("div", { className: "dl-builder-toolbar" });
const count = el("span", { className: "dl-deck-count is-invalid", "data-deck-count": "", text: "0/100" });
toolbar.appendChild(count);
["text", "grid", "spoiler"].forEach((mode) => {
  const button = el("button", { "data-display": mode, text: mode });
  toolbar.appendChild(button);
});
body.appendChild(toolbar);
body.appendChild(el("div", { id: "table-view" }));
const stage = el("div", { id: "playmat-stage" });
stage.appendChild(el("div", { id: "playmat" }));
body.appendChild(el("div", { id: "playmat-view" }));
document.getElementById("playmat-view").appendChild(stage);
["zoom-label","mana-curve","color-stats","zone-stats"].forEach((id) => body.appendChild(el(id === "zoom-label" ? "span" : "div", { id })));
body.appendChild(el("div", { "data-playmat-selection": "" }));
body.appendChild(el("div", { "data-deck-tag-list": "" }));
body.appendChild(el("div", { "data-tag-summary": "" }));
body.appendChild(el("div", { "data-tag-options": "" }));

const fetches = [];
const store = {};
async function flush(ms = 0) {
  if (ms) await new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
}

await (async function boot() {
  const windowObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null; } };
  vm.runInContext(source, vm.createContext({
    console, document, window: windowObj, setTimeout, clearTimeout,
    localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    fetch(url, opts = {}) {
      fetches.push({ url: String(url), opts });
      if (String(url).includes("/commands")) {
        const packet = JSON.parse(opts.body || "{}");
        (packet.commands || []).forEach((cmd) => {
          if (cmd.type === "update_view" && cmd.display_mode) deck.preferences.display_mode = cmd.display_mode;
          if (cmd.type === "adjust_quantity") {
            const entry = deck.entries.find((item) => item.id === cmd.entry_id);
            if (entry) entry.quantity = Number(entry.quantity) + Number(cmd.delta);
            deck.validation.total_count = 100;
            deck.validation.library_count = 99;
          }
        });
        deck.revision = (deck.revision || 0) + 1;
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) });
    },
    AbortController, URL, URLSearchParams,
    location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" },
    navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error,
    CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    crypto: windowObj.crypto,
  }), { filename: builderPath });
  await flush();
})();

function snapshot(selector) {
  return [...document.querySelectorAll(selector)].map((node) => ({
    className: node.className,
    title: node.title,
    aria: node.getAttribute("aria-label") || node.getAttribute("aria-invalid"),
    issue: (node.querySelector(".dl-card-issue") || {}).textContent || "",
    hidden: (node.querySelector(".dl-visually-hidden") || {}).textContent || "",
  }));
}

const initial = {
  count: count.textContent,
  countClass: count.className,
  countAria: count.getAttribute("aria-label"),
  rows: snapshot(".dl-deck-row"),
  mat: snapshot(".dl-mat-card"),
};

const plus = [...document.querySelectorAll("button")].find((n) => (n.getAttribute("aria-label") || "").startsWith("Add one "));
if (plus) plus.dispatchEvent(makeEvent("click", { target: plus, button: 0 }));
await flush();
const afterAdd = { count: count.textContent, countClass: count.className, countAria: count.getAttribute("aria-label") };

const displayButtons = [...document.querySelectorAll("button")].filter((n) => n.getAttribute("data-display"));
const gridBtn = displayButtons.find((n) => n.getAttribute("data-display") === "grid");
if (!gridBtn) throw new Error("missing grid display button");
gridBtn.dispatchEvent(makeEvent("click", { target: gridBtn, button: 0 }));
await flush();
const grid = snapshot(".dl-grid-card");

const spoilerBtn = displayButtons.find((n) => n.getAttribute("data-display") === "spoiler");
if (!spoilerBtn) throw new Error("missing spoiler display button");
spoilerBtn.dispatchEvent(makeEvent("click", { target: spoilerBtn, button: 0 }));
await flush();
const spoiler = snapshot(".dl-spoiler-card");

console.log(JSON.stringify({ initial, afterAdd, grid, spoiler, fetches: fetches.length }));
"""


def test_builder_renders_count_and_invalid_cards_live(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute builder rendering")
    harness = tmp_path / "deck-validation-indicators.js"
    harness.write_text(HARNESS)
    result = subprocess.run(
        [node, str(harness), str(BUILDER_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    initial = payload["initial"]
    assert initial["count"] == "99/100"
    assert "is-invalid" in initial["countClass"].split()
    assert initial["countAria"] == "99 of 100 cards"
    ring_row = next(
        row for row in initial["rows"] if "dl-card-invalid" in row["className"].split()
    )
    assert "Commander singleton rule is exceeded." in ring_row["title"]
    assert "Commander singleton rule is exceeded." in ring_row["hidden"]
    assert "!" in ring_row["issue"]
    mat = next(
        card
        for card in initial["mat"]
        if "dl-card-invalid" in card["className"].split()
    )
    assert "Commander singleton rule is exceeded." in mat["title"]
    assert payload["afterAdd"]["count"] == "100/100"
    assert "is-invalid" not in payload["afterAdd"]["countClass"].split()
    assert payload["afterAdd"]["countAria"] == "100 of 100 cards"
    grid_invalid = next(
        card
        for card in payload["grid"]
        if "dl-card-invalid" in card["className"].split()
    )
    assert "Commander singleton rule is exceeded." in grid_invalid["title"]
    spoiler_invalid = next(
        card
        for card in payload["spoiler"]
        if "dl-card-invalid" in card["className"].split()
    )
    assert "Commander singleton rule is exceeded." in spoiler_invalid["title"]
    assert payload["fetches"] >= 1
