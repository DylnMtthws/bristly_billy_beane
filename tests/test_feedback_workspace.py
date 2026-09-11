"""Deck workspace feedback DYL-37–41: zones, grid, layer, search, selection."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.deck_documents import DeckDocumentRepo, InvalidCommand
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
BUILDER_HTML = (
    ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab" / "builder.html"
)
CSS_PATH = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"
WORKSPACE_CSS = (
    ROOT / "src" / "sabermetrics" / "ui" / "static" / "feedback-workspace.css"
)


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True


def _database(tmp_path):
    path = tmp_path / "workspace.db"
    setup_database(path)
    owner = db.UsersRepo(path).create(
        email="owner@example.test", display_name="Owner", status="active"
    )
    with db.connect(path) as conn:
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    "commander",
                    "o-cmd",
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
                    "o-ring",
                    "Sol Ring",
                    "{1}",
                    1,
                    "Artifact",
                    "Mana",
                    "[]",
                    0,
                    1,
                ),
                (
                    "gideon",
                    "o-gid",
                    "Gideon's Sacrifice",
                    "{W}",
                    1,
                    "Instant",
                    "Prevent",
                    '["W"]',
                    0,
                    1,
                ),
                (
                    "giant",
                    "o-giant",
                    "Two-Headed Giant of Foriys",
                    "{4}{R}",
                    5,
                    "Creature — Giant",
                    "Double strike",
                    '["R"]',
                    0,
                    1,
                ),
                (
                    "jitte",
                    "o-jitte",
                    "Umezawa's Jitte",
                    "{2}",
                    2,
                    "Legendary Artifact — Equipment",
                    "Equip",
                    "[]",
                    0,
                    1,
                ),
                (
                    "bolt",
                    "o-bolt",
                    "Lightning Bolt",
                    "{R}",
                    1,
                    "Instant",
                    "Damage",
                    '["R"]',
                    0,
                    1,
                ),
            ]
            + [
                (
                    f"fill-{i}",
                    f"o-fill-{i}",
                    f"Alpha Sample {i:02d}",
                    "{1}{G}",
                    2,
                    "Creature — Elf",
                    "Text",
                    '["G"]',
                    0,
                    1,
                )
                for i in range(12)
            ],
        )
        conn.commit()
    return path, owner


def test_builder_template_has_sidebar_add_zone_and_shared_toolbar() -> None:
    html = BUILDER_HTML.read_text()
    assert "dl-add-panel" not in html
    assert 'data-toggle-rail="left"' not in html
    assert 'class="dl-zones-head"' in html
    assert "data-new-zone" in html
    assert 'aria-label="Add zone"' in html
    assert 'role="combobox"' in html
    assert 'aria-autocomplete="list"' in html
    assert 'aria-controls="card-search-list"' in html
    assert "data-card-search" in html
    assert "dl-builder-toolbar" in html
    assert "data-table-only" in html
    assert 'id="card-search-list"' in html
    assert 'role="listbox"' in html


def test_grid_layout_persists_and_older_modes_remain_valid(tmp_path):
    path, owner = _database(tmp_path)
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner, commander_card_id="commander")
    unsorted = repo.get(owner, deck_id)["zones"][0]["id"]
    document = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=0,
        mutation_id="grid-on",
        commands=[{"type": "set_zone_layout", "zone_id": unsorted, "layout": "grid"}],
    )
    assert document["zones"][0]["layout_mode"] == "grid"
    spread = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=1,
        mutation_id="grid-off",
        commands=[{"type": "set_zone_layout", "zone_id": unsorted, "layout": "spread"}],
    )
    assert spread["zones"][0]["layout_mode"] == "spread"
    fan = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=2,
        mutation_id="fan-on",
        commands=[{"type": "set_zone_layout", "zone_id": unsorted, "layout": "fan"}],
    )
    assert fan["zones"][0]["layout_mode"] == "fan"
    with pytest.raises(InvalidCommand):
        repo.apply_commands(
            owner,
            deck_id,
            expected_revision=3,
            mutation_id="bad-layout",
            commands=[
                {"type": "set_zone_layout", "zone_id": unsorted, "layout": "pile"}
            ],
        )


def test_dragged_zone_layer_is_presentation_only(tmp_path):
    path, owner = _database(tmp_path)
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner)
    repo.apply_commands(
        owner,
        deck_id,
        expected_revision=0,
        mutation_id="extra-zone",
        commands=[{"type": "create_zone", "name": "Later", "zone_id": "later"}],
    )
    document = repo.get(owner, deck_id)
    early = document["zones"][0]
    later = next(zone for zone in document["zones"] if zone["id"] == "later")
    assert early["sort_order"] < later["sort_order"]
    raised = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=1,
        mutation_id="raise-early",
        commands=[
            {
                "type": "move_zone",
                "zone_id": early["id"],
                "x": early["x"],
                "y": early["y"],
                "layer": 4,
            }
        ],
    )
    by_id = {zone["id"]: zone for zone in raised["zones"]}
    assert by_id[early["id"]]["layer"] == 4
    assert by_id[early["id"]]["sort_order"] == early["sort_order"]
    assert [zone["id"] for zone in raised["zones"]] == [
        zone["id"] for zone in document["zones"]
    ]
    swapped = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=2,
        mutation_id="raise-later",
        commands=[
            {
                "type": "move_zone",
                "zone_id": later["id"],
                "x": later["x"],
                "y": later["y"],
                "layer": 5,
            }
        ],
    )
    by_id = {zone["id"]: zone for zone in swapped["zones"]}
    assert by_id[later["id"]]["layer"] == 5
    assert by_id[early["id"]]["layer"] == 4
    assert by_id[later["id"]]["sort_order"] == later["sort_order"]
    reloaded = repo.get(owner, deck_id)
    assert {zone["id"]: zone["layer"] for zone in reloaded["zones"]} == {
        early["id"]: 4,
        later["id"]: 5,
    }


def test_card_name_search_ignores_punctuation_and_caps_results(tmp_path, monkeypatch):
    path, owner = _database(tmp_path)
    repo = DeckDocumentRepo(path)
    names = {card["name"] for card in repo.search_cards(query="gideons")}
    assert "Gideon's Sacrifice" in names
    hyphen = {card["name"] for card in repo.search_cards(query="two headed")}
    assert "Two-Headed Giant of Foriys" in hyphen
    apostrophe = {card["name"] for card in repo.search_cards(query="umezawas jitte")}
    assert "Umezawa's Jitte" in apostrophe
    limited = repo.search_cards(query="Alpha Sample", limit=8)
    assert len(limited) == 8
    assert all(card["name"].startswith("Alpha Sample") for card in limited)
    ranked = repo.search_cards(query="Alpha Sample 01")
    assert ranked[0]["name"] == "Alpha Sample 01"

    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    _login(client, owner)
    created = client.post(
        "/build/new", json={"title": "Draft", "commander_card_id": "commander"}
    )
    deck_id = created.get_json()["id"]
    scoped = client.get(f"/api/cards?deck_id={deck_id}&q=gideons&limit=8").get_json()[
        "results"
    ]
    assert scoped == []
    legal = client.get(f"/api/cards?deck_id={deck_id}&q=umezawas&limit=8").get_json()[
        "results"
    ]
    assert [card["name"] for card in legal] == ["Umezawa's Jitte"]
    capped = client.get(
        f"/api/cards?deck_id={deck_id}&q=Alpha%20Sample&limit=8"
    ).get_json()["results"]
    assert len(capped) == 8


def test_shared_builder_hides_zone_and_search_controls(tmp_path, monkeypatch):
    path, owner = _database(tmp_path)
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner, commander_card_id="commander")
    token = repo.create_share(owner, deck_id)
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    html = app.test_client().get(f"/shared/deck/{token}").data
    assert b'data-shared="true"' in html
    assert b'aria-label="Add zone"' not in html
    assert b"data-card-search" not in html
    assert b"data-new-zone" not in html
    assert b"Read-only shared deck" in html


def test_layer_column_is_added_on_existing_zone_tables(tmp_path):
    path, owner = _database(tmp_path)
    DeckDocumentRepo(path).create(owner, commander_card_id="commander")
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE deck_zones DROP COLUMN layer")
        conn.commit()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(deck_zones)")}
        assert "layer" not in columns
    from scripts.setup_db import ensure_deck_document_schema

    with sqlite3.connect(path) as conn:
        ensure_deck_document_schema(conn)
        conn.commit()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(deck_zones)")}
        assert "layer" in columns
        assert conn.execute("SELECT layer FROM deck_zones LIMIT 1").fetchone()[0] == 0


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
    this.indeterminate = false;
    this.tabIndex = 0;
    this.src = "";
    this.alt = "";
    this.offsetWidth = 220;
    this.offsetHeight = 160;
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
      },
    });
  }
  get textContent() { return this.children.length ? this.children.map((c) => c.textContent).join("") : this._text; }
  set textContent(value) { this.children.length = 0; this._text = value == null ? "" : String(value); }
  get innerHTML() { return this._text; }
  set innerHTML(value) { this._text = String(value); }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
    if (name === "hidden") this.hidden = true;
    if (name.startsWith("data-")) {
      const camel = name.slice(5).replace(/-([a-z])/g, (_, l) => l.toUpperCase());
      this.dataset[camel] = String(value);
    }
  }
  getAttribute(name) {
    if (name === "id") return this.id || null;
    if (name === "class") return this.className || null;
    if (name === "hidden") return this.hidden ? "" : null;
    return this.attributes[name] ?? null;
  }
  hasAttribute(name) { return this.getAttribute(name) != null; }
  removeAttribute(name) {
    delete this.attributes[name];
    if (name === "hidden") this.hidden = false;
  }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  append(...nodes) { nodes.forEach((n) => this.appendChild(typeof n === "string" ? Object.assign(this.ownerDocument.createElement("#text"), { textContent: n }) : n)); }
  replaceChildren(...nodes) { this.children.forEach((c) => { c.parentNode = null; }); this.children.length = 0; this._text = ""; nodes.forEach((n) => this.appendChild(n)); }
  removeChild(child) { const i = this.children.indexOf(child); if (i >= 0) { this.children.splice(i, 1); child.parentNode = null; } return child; }
  contains(other) { return other === this || this.children.some((c) => c.contains(other)); }
  closest(selector) { let node = this; while (node && node.tagName) { if (matches(node, selector)) return node; node = node.parentNode; } return null; }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  addEventListener(type, fn) { (this.listeners[type] || (this.listeners[type] = [])).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((h) => h !== fn); }
  dispatchEvent(event) { event.target = event.target || this; event.currentTarget = this; (this.listeners[event.type] || []).slice().forEach((fn) => fn.call(this, event)); return !event.defaultPrevented; }
  setPointerCapture() {}
  focus() { this.ownerDocument.activeElement = this; }
  select() {}
  showModal() { this.open = true; this.returnValue = ""; }
}

function tokenize(selector) { return selector.split(",").map((part) => part.trim()).filter(Boolean); }
function simpleMatch(el, sel) {
  const chunks = sel.trim().match(/#[\w-]+|\.[\w-]+|\[[^\]]+\]|[\w-]+/g) || [];
  let tag = null; const ids = []; const classes = []; const attrs = [];
  for (const chunk of chunks) {
    if (chunk.startsWith("#")) ids.push(chunk.slice(1));
    else if (chunk.startsWith(".")) classes.push(chunk.slice(1));
    else if (chunk.startsWith("[")) {
      const body = chunk.slice(1, -1); const eq = body.indexOf("=");
      if (eq < 0) attrs.push({ name: body, value: null });
      else attrs.push({ name: body.slice(0, eq).trim(), value: body.slice(eq + 1).trim().replace(/^['"]|['"]$/g, "") });
    } else tag = chunk.toUpperCase();
  }
  if (tag && el.tagName !== tag) return false;
  if (ids.some((id) => el.id !== id)) return false;
  if (classes.some((c) => !el.classList.contains(c))) return false;
  for (const attr of attrs) {
    const actual = attr.name === "class" ? el.className : attr.name === "id" ? el.id : el.attributes[attr.name];
    if (attr.value == null) { if (actual == null && !(attr.name === "hidden" && el.hidden)) return false; }
    else if (String(actual) !== attr.value) return false;
  }
  return true;
}
function matches(el, selector) { return tokenize(selector).some((part) => simpleMatch(el, part.trim().split(/\s+/).pop())); }
function walk(el, acc) { acc.push(el); el.children.forEach((c) => walk(c, acc)); return acc; }
function queryAll(root, selector) {
  const all = []; root.children.forEach((c) => walk(c, all));
  const parts = tokenize(selector);
  return all.filter((el) => parts.some((part) => simpleMatch(el, part.trim().split(/\s+/).pop())));
}
function makeEvent(type, props = {}) {
  return { type, ...props, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {} };
}

const documentElement = new Element("html", null);
const body = new Element("body", null);
const head = new Element("head", null);
documentElement.appendChild(head); documentElement.appendChild(body);
const document = {
  documentElement, body, head, activeElement: null,
  createElement(tag) { const el = new Element(tag, document); el.ownerDocument = document; return el; },
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

function makeEntries(zoneId, count, prefix) {
  return Array.from({ length: count }, (_, i) => ({
    id: prefix + i, name: prefix + " " + i, is_commander: false, quantity: 1,
    zone_id: zoneId, sort_order: i, type_line: "Instant", mana_cost: "{1}",
    mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: ""
  }));
}

const deck = {
  id: "deck-1", title: "Test Deck", revision: 0,
  zones: [
    { id: "zone-early", name: "Early", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
    { id: "zone-grid", name: "Grid Four", x: 320, y: 18, width: 400, layout_mode: "grid", sort_order: 1, layer: 0 },
    { id: "zone-empty", name: "Empty Grid", x: 700, y: 18, width: 400, layout_mode: "grid", sort_order: 2, layer: 0 },
    { id: "zone-one", name: "One", x: 40, y: 260, width: 400, layout_mode: "grid", sort_order: 3, layer: 0 },
    { id: "zone-large", name: "Large", x: 320, y: 260, width: 400, layout_mode: "grid", sort_order: 4, layer: 0 },
  ],
  entries: [
    { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "" },
    ...makeEntries("zone-early", 2, "early"),
    ...makeEntries("zone-grid", 4, "grid"),
    ...makeEntries("zone-one", 1, "one"),
    ...makeEntries("zone-large", 9, "large"),
  ],
  presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
  preferences: { view_mode: "playmat", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
  tags: [], tag_suggestions: [],
};

body.appendChild(Object.assign(el("script", { id: "deck-document-data" }), { textContent: JSON.stringify(deck) }));
const root = el("div", { className: "dl-builder", "data-shared": "false", "data-playmat-enabled": "true" });
body.appendChild(root);
head.appendChild(el("meta", { name: "csrf-token", content: "token" }));
body.appendChild(el("button", { id: "save-state", text: "Saved" }));
body.appendChild(el("div", { id: "table-view" }));
const stage = el("div", { id: "playmat-stage" });
stage.appendChild(el("div", { id: "playmat" }));
body.appendChild(el("div", { id: "playmat-view" }));
document.getElementById("playmat-view").appendChild(stage);
["zoom-label","mana-curve","color-stats","zone-stats"].forEach((id) => body.appendChild(el(id === "zoom-label" ? "span" : "div", { id })));
body.appendChild(el("div", { "data-playmat-selection": "" }));
const combobox = el("div", { className: "dl-card-combobox", "data-card-combobox": "" });
combobox.appendChild(el("input", { "data-card-search": "", role: "combobox" }));
combobox.appendChild(el("select", { "data-add-zone": "" }));
combobox.appendChild(el("ul", { id: "card-search-list", "data-card-results": "", hidden: "" }));
combobox.appendChild(el("div", { "data-search-status": "" }));
body.appendChild(combobox);
body.appendChild(el("button", { "data-new-zone": "", id: "sidebar-add-zone", "aria-label": "Add zone" }));
const zoneDialog = el("dialog", { id: "zone-dialog" });
zoneDialog.appendChild(el("h2", { text: "New zone" }));
zoneDialog.appendChild(el("input", { "data-zone-name": "" }));
zoneDialog.appendChild(el("output", { "data-zone-error": "" }));
body.appendChild(zoneDialog);
body.appendChild(el("div", { "data-deck-tag-list": "" }));
body.appendChild(el("div", { "data-tag-summary": "" }));
body.appendChild(el("div", { "data-tag-options": "" }));

const fetches = [];
const store = {};
let searchCalls = 0;
let pendingSearch;
async function flush(ms = 0) {
  if (ms) await new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0));
}
function commandBodies() {
  return fetches.filter((f) => String(f.url).includes("/commands")).map((f) => JSON.parse(f.opts.body || "{}").commands);
}
function zone(id) { return document.querySelectorAll(".dl-mat-zone").find((z) => z.dataset.zoneId === id); }

await (async function boot() {
  const windowObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} }, prompt() { return null; } };
  vm.runInContext(source, vm.createContext({
    console, document, window: windowObj, setTimeout, clearTimeout,
    localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    fetch(url, opts = {}) {
      fetches.push({ url: String(url), opts });
      if (String(url).startsWith("/api/cards")) {
        searchCalls += 1;
        const call = searchCalls;
        const payload = call === 1
          ? [{ id: "jitte", name: "Umezawa's Jitte", type_line: "Artifact", image_uri: "https://img.test/jitte.jpg" }]
          : [{ id: "ring", name: "Sol Ring", type_line: "Artifact" }];
        const result = { ok: true, json: async () => ({ scope: "Commander identity", results: payload }) };
        if (call === 1 && pendingSearch) return pendingSearch.then(() => result);
        return Promise.resolve(result);
      }
      if (String(url).includes("/commands")) {
        try {
          const packet = JSON.parse(opts.body || "{}");
          (packet.commands || []).forEach((cmd) => {
            if (cmd.type === "move_zone") {
              const zone = deck.zones.find((item) => item.id === cmd.zone_id);
              if (!zone) return;
              if (cmd.x != null) zone.x = cmd.x;
              if (cmd.y != null) zone.y = cmd.y;
              if (cmd.layer != null) zone.layer = cmd.layer;
            } else if (cmd.type === "set_zone_layout") {
              const zone = deck.zones.find((item) => item.id === cmd.zone_id);
              if (zone && cmd.layout) zone.layout_mode = cmd.layout;
            }
          });
          deck.revision = (deck.revision || 0) + 1;
        } catch (_) {}
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

const gridBox = zone("zone-grid");
const emptyBox = zone("zone-empty");
const oneBox = zone("zone-one");
const largeBox = zone("zone-large");
const earlyBox = zone("zone-early");
const gridCards = gridBox.querySelector(".dl-mat-cards");
const largeCards = largeBox.querySelector(".dl-mat-cards");

function layerOf(id) { return Number(zone(id).style._props["--zone-layer"]); }

const beforeLayer = { early: layerOf("zone-early"), grid: layerOf("zone-grid") };
const earlyBar = earlyBox.querySelector(".dl-mat-zone-bar");
earlyBar.dispatchEvent(makeEvent("pointerdown", { button: 0, clientX: 50, clientY: 20, pointerId: 1, target: earlyBar }));
const duringDrag = { early: layerOf("zone-early"), moving: earlyBox.classList.contains("moving") };
earlyBar.dispatchEvent(makeEvent("pointermove", { clientX: 80, clientY: 40, pointerId: 1, target: earlyBar }));
earlyBar.dispatchEvent(makeEvent("pointerup", { clientX: 80, clientY: 40, pointerId: 1, target: earlyBar }));
await flush();
const afterEarly = commandBodies().at(-1);

const laterBar = zone("zone-grid").querySelector(".dl-mat-zone-bar");
laterBar.dispatchEvent(makeEvent("pointerdown", { button: 0, clientX: 330, clientY: 20, pointerId: 2, target: laterBar }));
laterBar.dispatchEvent(makeEvent("pointerup", { clientX: 340, clientY: 30, pointerId: 2, target: laterBar }));
await flush();
const afterLater = commandBodies().at(-1);

const addZone = document.querySelector("[data-new-zone]");
addZone.dispatchEvent(makeEvent("click", { target: addZone, button: 0 }));
zoneDialog.querySelector("[data-zone-name]").value = "Interaction";
zoneDialog.returnValue = "save";
zoneDialog.dispatchEvent(makeEvent("close", { target: zoneDialog }));
zoneDialog.dispatchEvent(makeEvent("close", { target: zoneDialog }));
await flush();
const createCommands = commandBodies().filter((cmds) => cmds.some((c) => c.type === "create_zone"));

const gridToggle = gridBox.querySelector(".dl-zone-grid-toggle");
const layoutToggle = gridBox.querySelector(".dl-zone-layout-toggle");
gridToggle.dispatchEvent(makeEvent("click", { target: gridToggle, button: 0 }));
layoutToggle.dispatchEvent(makeEvent("click", { target: layoutToggle, button: 0 }));
await flush();
const layoutCommands = commandBodies().filter((cmds) => cmds.some((c) => c.type === "set_zone_layout"));

const headings = document.querySelectorAll(".dl-zone-heading");
const heading = headings[1];
const select = heading ? heading.querySelector(".dl-group-select") : null;
const headingSelect = heading ? heading.querySelector(".dl-heading-select") : null;
const title = heading ? heading.querySelector(".dl-heading-title") : null;

const input = document.querySelector("[data-card-search]");
const results = document.querySelector("[data-card-results]");
input.value = "";
input.dispatchEvent(makeEvent("input", { target: input }));
await flush(220);
const emptyQuery = { hidden: results.hidden, children: results.children.length, status: document.querySelector("[data-search-status]").textContent };

let resolveSlow;
pendingSearch = new Promise((resolve) => { resolveSlow = resolve; });
input.value = "jitte";
input.dispatchEvent(makeEvent("input", { target: input }));
await flush(220);
input.value = "sol";
input.dispatchEvent(makeEvent("input", { target: input }));
await flush(220);
resolveSlow();
await flush(40);
const stale = {
  names: [...results.querySelectorAll("[role=option]")].map((n) => n.textContent),
  expanded: input.getAttribute("aria-expanded"),
  count: results.querySelectorAll("[role=option]").length,
  searchCalls,
};

input.dispatchEvent(makeEvent("keydown", { key: "ArrowDown", target: input }));
const highlighted = [...results.querySelectorAll("[role=option]")].map((n) => n.getAttribute("aria-selected"));
input.dispatchEvent(makeEvent("keydown", { key: "Enter", target: input }));
await flush();
const chosen = commandBodies().at(-1);
input.dispatchEvent(makeEvent("keydown", { key: "Escape", target: input }));
const escaped = { hidden: results.hidden, expanded: input.getAttribute("aria-expanded") };

const plus = zone("zone-grid").querySelector(".dl-mat-add");
plus.dispatchEvent(makeEvent("click", { target: plus, button: 0 }));
const focusedSearch = document.activeElement && document.activeElement.getAttribute("data-card-search") === "";

console.log(JSON.stringify({
  grid: {
    className: gridBox.className,
    cols: gridCards.style._props["--grid-cols"],
    height: gridCards.style.height,
    overflowHint: gridCards.getAttribute("aria-label"),
    emptyCols: emptyBox.querySelector(".dl-mat-cards").style._props["--grid-cols"],
    oneCols: oneBox.querySelector(".dl-mat-cards").style._props["--grid-cols"],
    largeCols: largeCards.style._props["--grid-cols"],
    largeHeight: largeCards.style.height,
    gridTogglePressed: gridToggle.getAttribute("aria-pressed"),
  },
  layers: { beforeLayer, duringDrag, afterEarly, afterLater },
  createZone: { opens: zoneDialog.open === true || zoneDialog.returnValue === "save", count: createCommands.length, commands: createCommands },
  layouts: layoutCommands,
  heading: {
    checkboxInLead: !!(headingSelect && headingSelect.querySelector(".dl-group-select")),
    titleHasCollapse: !!(title && title.querySelector(".dl-zone-collapse")),
    checkboxNotLast: heading && heading.children[heading.children.length - 1] !== select,
  },
  search: { emptyQuery, stale, highlighted, chosen, escaped, focusedSearch, combobox: input.getAttribute("role") },
}));
"""


def _run_workspace_js(tmp_path: Path) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute builder workspace behavior")
    harness = tmp_path / "workspace_harness.mjs"
    harness.write_text(HARNESS)
    completed = subprocess.run(
        [node, str(harness), str(BUILDER_JS)],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout.splitlines()[-1])


def test_builder_workspace_interactions(tmp_path: Path) -> None:
    result = _run_workspace_js(tmp_path)
    grid = result["grid"]
    assert "grid" in grid["className"]
    assert grid["cols"] == "3"
    assert int(str(grid["height"]).replace("px", "") or 0) >= 184
    assert grid["overflowHint"] in (None, "", "null")
    assert grid["emptyCols"] == "1"
    assert grid["oneCols"] == "2"
    assert grid["largeCols"] == "4"
    assert int(str(grid["largeHeight"]).replace("px", "") or 0) >= 375
    assert grid["gridTogglePressed"] == "true"

    layers = result["layers"]
    assert layers["duringDrag"]["moving"] is True
    assert layers["duringDrag"]["early"] > layers["beforeLayer"]["early"]
    assert layers["duringDrag"]["early"] > layers["beforeLayer"]["grid"]
    assert layers["afterEarly"][-1]["type"] == "move_zone"
    assert layers["afterEarly"][-1]["layer"] >= 1
    assert layers["afterLater"][-1]["zone_id"] == "zone-grid"
    assert layers["afterLater"][-1]["layer"] > layers["afterEarly"][-1]["layer"]

    created = result["createZone"]["commands"]
    assert len(created) == 1
    assert created[0] == [{"type": "create_zone", "name": "Interaction"}]

    layouts = [cmd for batch in result["layouts"] for cmd in batch]
    assert {
        "type": "set_zone_layout",
        "zone_id": "zone-grid",
        "layout": "spread",
    } in layouts
    assert {
        "type": "set_zone_layout",
        "zone_id": "zone-grid",
        "layout": "fan",
    } in layouts

    heading = result["heading"]
    assert heading["checkboxInLead"] is True
    assert heading["titleHasCollapse"] is True
    assert heading["checkboxNotLast"] is True

    search = result["search"]
    assert search["combobox"] == "combobox"
    assert search["emptyQuery"]["children"] == 0
    assert search["stale"]["count"] == 1
    assert "Sol Ring" in "".join(search["stale"]["names"])
    assert "Jitte" not in "".join(search["stale"]["names"])
    assert search["stale"]["expanded"] == "true"
    assert search["chosen"][-1]["type"] == "add_card"
    assert search["escaped"]["hidden"] is True
    assert search["focusedSearch"] is True
