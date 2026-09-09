"""Card artwork must follow the pointer for playmat and sidebar drags."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
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
    this.currentSrc = "";
    this.complete = true;
    this.naturalWidth = 120;
    this.width = 1;
    this.height = 1;
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
    if (name.startsWith("data-")) {
      const camel = name.slice(5).replace(/-([a-z])/g, (_, l) => l.toUpperCase());
      this.dataset[camel] = String(value);
    }
  }
  getAttribute(name) {
    if (name === "id") return this.id || null;
    if (name === "class") return this.className || null;
    return this.attributes[name] ?? null;
  }
  hasAttribute(name) { return this.getAttribute(name) != null; }
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
  showModal() { this.open = true; }
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
    if (attr.value == null) { if (actual == null) return false; }
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

class DataTransferMock {
  constructor() { this._data = {}; this.types = []; this.effectAllowed = "uninitialized"; this.dropEffect = "none"; this.ghost = null; }
  setData(type, value) { this._data[type] = String(value); if (!this.types.includes(type)) this.types.push(type); }
  getData(type) { return this._data[type] || ""; }
  setDragImage(el, x, y) { this.ghost = { tag: el && el.tagName, x, y }; }
}
function dropAllowed(dt) {
  const allowed = dt.effectAllowed, effect = dt.dropEffect;
  if (!effect || effect === "none") return false;
  if (allowed === "all" || allowed === "uninitialized") return true;
  if (allowed === "copyMove") return effect === "copy" || effect === "move";
  return allowed === effect;
}
function makeEvent(type, props = {}) {
  return { type, ...props, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {} };
}

const documentElement = new Element("html", null);
const body = new Element("body", null);
const head = new Element("head", null);
documentElement.appendChild(head); documentElement.appendChild(body);
const document = {
  documentElement, body, head,
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

const deck = {
  id: "deck-1", title: "Test Deck", revision: 0,
  zones: [
    { id: "zone-unsorted", name: "Unsorted", x: 220, y: 18, width: 400, layout_mode: "spread", sort_order: 0 },
    { id: "zone-lands", name: "Lands", x: 650, y: 18, width: 400, layout_mode: "spread", sort_order: 1 },
  ],
  entries: [
    { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "https://img.test/kinnan.jpg", role: "" },
    { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-unsorted", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "https://img.test/solring.jpg", role: "" },
  ],
  presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
  preferences: { view_mode: "playmat", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
  tags: [], tag_suggestions: [],
};

body.appendChild(Object.assign(el("script", { id: "deck-document-data" }), { textContent: JSON.stringify(deck) }));
body.appendChild(el("div", { className: "dl-builder", "data-shared": "false", "data-playmat-enabled": "true" }));
head.appendChild(el("meta", { name: "csrf-token", content: "token" }));
body.appendChild(el("button", { id: "save-state", text: "Saved" }));
body.appendChild(el("div", { id: "table-view" }));
const stage = el("div", { id: "playmat-stage" });
stage.appendChild(el("div", { id: "playmat" }));
body.appendChild(Object.assign(el("div", { id: "playmat-view" }), { children: [] }));
document.getElementById("playmat-view") || body.appendChild(el("div", { id: "playmat-view" }));
const playmatView = document.getElementById("playmat-view");
playmatView.appendChild(stage);
["zoom-label","mana-curve","color-stats","zone-stats"].forEach((id) => body.appendChild(el(id === "zoom-label" ? "span" : "div", { id })));
body.appendChild(el("div", { "data-playmat-selection": "" }));
body.appendChild(el("input", { "data-card-search": "" }));
body.appendChild(el("div", { "data-card-results": "" }));
body.appendChild(el("span", { "data-search-scope": "" }));
body.appendChild(el("span", { "data-card-total": "" }));
body.appendChild(el("select", { "data-add-zone": "" }));
body.appendChild(el("div", { "data-deck-tag-list": "" }));
body.appendChild(el("div", { "data-tag-summary": "" }));
body.appendChild(el("div", { "data-tag-options": "" }));

const fetches = [];
const store = {};
async function flush() { for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0)); }
function commandBodies() {
  return fetches.filter((f) => String(f.url).includes("/commands")).map((f) => JSON.parse(f.opts.body || "{}").commands);
}
function preview() { return document.querySelector(".dl-drag-preview"); }
function previewInfo() {
  const node = preview();
  if (!node) return null;
  const img = node.querySelector("img");
  const span = node.querySelector("span");
  return {
    left: node.style.left, top: node.style.top,
    src: img ? img.src : "",
    text: span ? span.textContent : "",
    ariaHidden: node.getAttribute("aria-hidden"),
  };
}
function zone(id) { return document.querySelectorAll(".dl-mat-zone").find((z) => z.dataset.zoneId === id); }
function cardByEntry(id) { return document.querySelectorAll(".dl-mat-card").find((c) => c.dataset.entryId === id); }
function tileByName(name) {
  return document.querySelectorAll(".dl-search-result").find((tile) => (tile.textContent || "").includes(name));
}

await (async function boot() {
  const windowObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} } };
  vm.runInContext(source, vm.createContext({
    console, document, window: windowObj,
    localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    fetch(url, opts = {}) {
      fetches.push({ url: String(url), opts });
      if (String(url).startsWith("/api/cards")) {
        return Promise.resolve({ ok: true, json: async () => ({ scope: "Commander identity", results: [
          { id: "bolt", name: "Lightning Bolt", type_line: "Instant", image_uri: "https://img.test/bolt.jpg" },
          { id: "brainstorm", name: "Brainstorm", type_line: "Instant" },
        ] }) });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => deck });
    },
    AbortController, URL, URLSearchParams,
    location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1" },
    navigator: {}, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error,
    CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    crypto: windowObj.crypto,
  }), { filename: builderPath });
  await flush();
})();

async function runDrag(source, start, move, dropTarget) {
  const dt = new DataTransferMock();
  const addBtn = source.querySelector("button[aria-label]");
  source.dispatchEvent(makeEvent("dragstart", { dataTransfer: dt, clientX: start.x, clientY: start.y, target: source }));
  const afterStart = {
    preview: previewInfo(),
    sourceAlt: (source.querySelector("img") || {}).alt || "",
    sourceName: source.title || source.textContent,
    addLabel: addBtn ? addBtn.getAttribute("aria-label") : "",
    effectAllowed: dt.effectAllowed,
    types: dt.types.slice(),
    nativeGhost: dt.ghost,
  };
  document.dispatchEvent(makeEvent("dragover", { dataTransfer: dt, clientX: move.x, clientY: move.y, target: document.body }));
  const afterMove = previewInfo();
  dropTarget.dispatchEvent(makeEvent("dragover", { dataTransfer: dt, clientX: move.x + 20, clientY: move.y, target: dropTarget }));
  const afterOver = { dropEffect: dt.dropEffect, wouldDrop: dropAllowed(dt), preview: previewInfo() };
  const before = commandBodies().length;
  if (afterOver.wouldDrop) dropTarget.dispatchEvent(makeEvent("drop", { dataTransfer: dt, clientX: move.x + 20, clientY: move.y, target: dropTarget }));
  source.dispatchEvent(makeEvent("dragend", { dataTransfer: dt, target: source }));
  await flush();
  return { afterStart, afterMove, afterOver, commands: commandBodies().slice(before), cleaned: { preview: previewInfo(), dragSource: Boolean(document.querySelector(".drag-source")) } };
}

async function runCancel(source, start) {
  const dt = new DataTransferMock();
  const before = commandBodies().length;
  source.dispatchEvent(makeEvent("dragstart", { dataTransfer: dt, clientX: start.x, clientY: start.y, target: source }));
  const hadPreview = Boolean(preview());
  document.dispatchEvent(makeEvent("keydown", { key: "Escape", target: document.body }));
  source.dispatchEvent(makeEvent("dragend", { dataTransfer: dt, target: source }));
  await flush();
  return { hadPreview, preview: previewInfo(), dragSource: Boolean(document.querySelector(".drag-source")), extraCommands: commandBodies().slice(before) };
}

const lands = zone("zone-lands");
const playmatComplete = await runDrag(cardByEntry("entry-ring"), { x: 40, y: 40 }, { x: 300, y: 120 }, lands);
const sidebarArt = await runDrag(tileByName("Lightning Bolt"), { x: 20, y: 200 }, { x: 260, y: 140 }, lands);
const sidebarText = await runDrag(tileByName("Brainstorm"), { x: 24, y: 260 }, { x: 280, y: 150 }, lands);
const playmatCancel = await runCancel(cardByEntry("entry-ring"), { x: 48, y: 44 });
const sidebarCancel = await runCancel(tileByName("Lightning Bolt"), { x: 22, y: 210 });

console.log(JSON.stringify({
  playmatComplete, sidebarArt, sidebarText, playmatCancel, sidebarCancel,
  addControl: (tileByName("Lightning Bolt").querySelector("button") || {}).getAttribute ? tileByName("Lightning Bolt").querySelector("button").getAttribute("aria-label") : "",
}));
"""


def _run_builder_drags(builder: Path, tmp_path: Path) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute builder drag behavior")
    harness = tmp_path / "drag_preview_harness.mjs"
    harness.write_text(HARNESS)
    completed = subprocess.run(
        [node, str(harness), str(builder)],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


def _moved(start: dict, moved: dict) -> bool:
    assert start is not None and moved is not None
    return (start["left"], start["top"]) != (moved["left"], moved["top"])


def test_card_image_follows_pointer_from_playmat_and_sidebar(tmp_path: Path) -> None:
    result = _run_builder_drags(BUILDER_JS, tmp_path)

    playmat = result["playmatComplete"]
    assert playmat["afterStart"]["types"] == ["text/deck-entry"]
    assert playmat["afterStart"]["effectAllowed"] == "move"
    assert playmat["afterStart"]["sourceAlt"] == "Sol Ring"
    assert playmat["afterStart"]["preview"]["src"] == "https://img.test/solring.jpg"
    assert playmat["afterStart"]["preview"]["ariaHidden"] == "true"
    assert _moved(playmat["afterStart"]["preview"], playmat["afterMove"])
    assert playmat["afterOver"]["dropEffect"] == "move"
    assert playmat["afterOver"]["wouldDrop"] is True
    assert playmat["commands"] == [
        [
            {
                "type": "move_entry",
                "entry_id": "entry-ring",
                "zone_id": "zone-lands",
                "sort_order": 999,
            }
        ]
    ]
    assert playmat["cleaned"]["preview"] is None
    assert playmat["cleaned"]["dragSource"] is False

    sidebar = result["sidebarArt"]
    assert sidebar["afterStart"]["types"] == ["text/card-id"]
    assert sidebar["afterStart"]["effectAllowed"] == "copy"
    assert sidebar["afterStart"]["addLabel"] == "Add Lightning Bolt"
    assert sidebar["afterStart"]["preview"]["src"] == "https://img.test/bolt.jpg"
    assert _moved(sidebar["afterStart"]["preview"], sidebar["afterMove"])
    assert sidebar["afterOver"]["dropEffect"] == "copy"
    assert sidebar["afterOver"]["wouldDrop"] is True
    assert sidebar["commands"] == [
        [
            {
                "type": "add_card",
                "card_id": "bolt",
                "zone_id": "zone-lands",
                "quantity": 1,
            }
        ]
    ]
    assert sidebar["cleaned"]["preview"] is None

    text_origin = result["sidebarText"]
    assert text_origin["afterStart"]["preview"] is not None
    assert (
        text_origin["afterStart"]["preview"]["src"]
        or text_origin["afterStart"]["preview"]["text"]
    )
    assert _moved(text_origin["afterStart"]["preview"], text_origin["afterMove"])
    assert text_origin["afterOver"]["dropEffect"] == "copy"
    assert text_origin["afterOver"]["wouldDrop"] is True
    assert text_origin["commands"] == [
        [
            {
                "type": "add_card",
                "card_id": "brainstorm",
                "zone_id": "zone-lands",
                "quantity": 1,
            }
        ]
    ]

    for origin in ("playmatCancel", "sidebarCancel"):
        cancelled = result[origin]
        assert cancelled["hadPreview"] is True
        assert cancelled["preview"] is None
        assert cancelled["dragSource"] is False
        assert cancelled["extraCommands"] == []

    assert result["addControl"] == "Add Lightning Bolt"
