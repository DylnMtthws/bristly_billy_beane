"""DYL-53: builder export menu, clipboard copy, and Mana Pool handoff."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
EXPORT_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-export.js"
BUILDER_HTML = (
    ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab" / "builder.html"
)
LIBRARY_CARD = (
    ROOT / "src" / "sabermetrics" / "ui" / "templates" / "deck_lab" / "_deck_card.html"
)
CSS_PATH = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"
EXPORT_CSS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "feedback-export.css"

COORD_TEXT = "1 Sol Ring\n2 Island"
COORD_URL = "https://manapool.com/add-deck?deck=MSBTb2wgUmluZwoyIElzbGFuZA%3D%3D"


def mana_pool_url(text: str) -> str:
    payload = base64.standard_b64encode(text.encode("utf-8")).decode("ascii")
    return "https://manapool.com/add-deck?deck=" + quote(payload, safe="-_.!~*'()")


def test_builder_header_replaces_share_with_export_disclosure():
    html = BUILDER_HTML.read_text()
    header = html.split("<header", 1)[1].split("</header>", 1)[0]
    assert "data-export-menu" in header
    assert "data-dismiss-menu" in header
    assert "Copy list" in header
    assert "Buy deck" in header
    assert header.count("Copy list") == 1
    assert header.count("Buy deck") == 1
    assert "data-share" not in header
    assert (
        "data-table-only"
        not in header.split("dl-export-menu", 1)[1].split("</details>", 1)[0]
    )
    assert (
        "desktop-only"
        not in header.split("dl-export-menu", 1)[1].split("</details>", 1)[0]
    )
    assert "href=\"{{ url_for('builder.export'" not in header
    assert 'target="_blank"' in header
    assert "noopener" in header and "noreferrer" in header
    assert 'referrerpolicy="no-referrer"' in header
    assert 'aria-haspopup="menu"' in header
    assert 'aria-live="polite"' in header
    assert "data-export-copy" in header
    assert "data-export-buy" in header
    assert 'id="export-fallback-dialog"' in html
    assert "data-export-fallback-text" in html
    assert (
        "readonly" in html.split("data-export-fallback-text", 1)[0][-80:]
        or "readonly" in html.split("data-export-fallback-text", 1)[1][:80]
    )
    scripts = html.split("{% block scripts %}", 1)[1]
    assert "deck-lab-export.js" in scripts
    assert scripts.index("deck-lab-export.js") < scripts.index("deck-lab-builder.js")
    owner_blocks = [
        chunk.split("{% endif %}", 1)[0]
        for chunk in header.split("{% if not shared %}")[1:]
    ]
    assert any("data-export-menu" in chunk for chunk in owner_blocks)


def test_library_share_and_download_export_remain():
    card = LIBRARY_CARD.read_text()
    assert "data-share-deck" in card
    assert "Export decklist" in card
    assert "builder.export" in card
    source = BUILDER_JS.read_text()
    assert "[data-share]" not in source
    assert "/share" not in source
    assert "DeckLabExport" in source
    assert "data-dismiss-menu" in BUILDER_HTML.read_text()
    assert "feedback-export.css" in CSS_PATH.read_text()
    css = EXPORT_CSS.read_text()
    assert "flex-wrap: nowrap" in css
    assert "min-height: 44px" in css
    assert "max-width: 430px" in css
    assert "max-width: 28vw" in css


def test_mana_pool_contract_matches_coordinator_vector():
    assert mana_pool_url(COORD_TEXT) == COORD_URL


HELPER_PROBE = r"""
const api = require(process.argv[2]);
const document = {
  title: "Secret Title",
  tags: [{ name: "Turbo" }],
  zones: [
    { id: "zone-main", name: "Ramp" },
    { id: "zone-custom", name: "Interaction" },
    { id: "zone-side", name: "Sideboard" },
    { id: "zone-maybe", name: "Maybeboard" },
    { id: "zone-notes", name: "Notes" }
  ],
  entries: [
    { id: "cmd-b", name: "Thrasios, Triton Hero", is_commander: true, quantity: 1, zone_id: null, format_legal: true },
    { id: "cmd-copy", name: "Thrasios, Triton Hero", is_commander: false, quantity: 1, zone_id: "zone-main" },
    { id: "cmd-a", name: "Tymna the Weaver", is_commander: true, quantity: 1, zone_id: null, format_legal: true },
    { id: "ring-a", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", format_legal: true },
    { id: "ring-b", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-custom", format_legal: false },
    { id: "split", name: "Fire // Ice", is_commander: false, quantity: 1, zone_id: "zone-main" },
    { id: "uni", name: "Gideon’s Sacrifice", is_commander: false, quantity: 2, zone_id: "zone-custom" },
    { id: "island", name: "Island", is_commander: false, quantity: 1, zone_id: "zone-main" },
    { id: "side", name: "Lightning Bolt", is_commander: false, quantity: 4, zone_id: "zone-side" },
    { id: "maybe", name: "Brainstorm", is_commander: false, quantity: 1, zone_id: "zone-maybe" },
    { id: "note", name: "Private Note Card", is_commander: false, quantity: 1, zone_id: "zone-notes" },
    { id: "zero", name: "Should Skip", is_commander: false, quantity: 0, zone_id: "zone-main" }
  ]
};
const text = api.exportText(document);
const empty = api.exportText({ title: "Empty", entries: [], zones: [] });
console.log(JSON.stringify({
  text,
  lines: api.exportLines(document),
  url: api.manaPoolUrl(text),
  coord: api.manaPoolUrl("1 Sol Ring\n2 Island"),
  empty,
  emptyUrl: api.manaPoolUrl(empty)
}));
"""


def test_export_helper_quantities_partners_unicode_and_exclusions(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute export helper")
    probe = tmp_path / "export_helper_probe.js"
    probe.write_text(HELPER_PROBE)
    result = subprocess.run(
        [node, str(probe), str(EXPORT_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    expected = (
        "2 Thrasios, Triton Hero\n1 Tymna the Weaver\n1 Fire // Ice\n"
        "2 Gideon’s Sacrifice\n1 Island\n2 Sol Ring"
    )
    assert payload["text"] == expected
    assert "Secret Title" not in payload["text"]
    assert "Turbo" not in payload["text"]
    assert "Lightning Bolt" not in payload["text"]
    assert "Brainstorm" not in payload["text"]
    assert "Private Note Card" not in payload["text"]
    assert "Should Skip" not in payload["text"]
    assert "Ramp" not in payload["text"]
    assert payload["coord"] == COORD_URL
    assert payload["url"] == mana_pool_url(expected)
    assert "Gideon’s Sacrifice" in payload["text"]
    assert "Fire // Ice" in payload["text"]
    assert payload["empty"] == ""
    assert payload["emptyUrl"] == ""
    assert payload["lines"][0].startswith("2 Thrasios")
    assert payload["lines"][1].startswith("1 Tymna")


HARNESS = r"""
import fs from "node:fs";
import vm from "node:vm";

const builderPath = process.argv[2];
const exportPath = process.argv[3];
const builderSource = fs.readFileSync(builderPath, "utf8");
const exportSource = fs.readFileSync(exportPath, "utf8");

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
    this.open = false;
    this.readOnly = false;
    this.focus = function () {};
    this.select = function () { this._selected = true; };
    this.showModal = function () { this.open = true; };
    this.close = function () { this.open = false; this.dispatchEvent({ type: "close" }); };
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
    if (key === "href") this.href = String(value);
  }
  getAttribute(name) {
    const key = String(name);
    if (key === "id") return this.id || null;
    if (key === "class") return this.className || null;
    if (key === "title") return this.title || null;
    return this.attributes[key] != null ? this.attributes[key] : null;
  }
  hasAttribute(name) { return this.getAttribute(name) != null; }
  removeAttribute(name) {
    delete this.attributes[String(name)];
    if (String(name) === "href") delete this.href;
  }
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
    else if (key === "readOnly") node.readOnly = !!value;
    else node.setAttribute(key, value);
  });
  return node;
}
function makeEvent(type, extra = {}) {
  return Object.assign({ type, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {}, defaultPrevented: false }, extra);
}

const deck = {
  id: "deck-1", title: "Secret Title", revision: 0,
  zones: [
    { id: "zone-main", name: "Unsorted", x: 40, y: 18, width: 400, layout_mode: "spread", sort_order: 0, layer: 0 },
    { id: "zone-side", name: "Sideboard", x: 80, y: 18, width: 400, layout_mode: "spread", sort_order: 1, layer: 0 }
  ],
  entries: [
    { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
    { id: "entry-ring", name: "Sol Ring", is_commander: false, quantity: 1, zone_id: "zone-main", sort_order: 0, type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] },
    { id: "entry-side", name: "Lightning Bolt", is_commander: false, quantity: 2, zone_id: "zone-side", sort_order: 0, type_line: "Instant", mana_cost: "{R}", mana_value: 1, oracle_text: "", color_identity: ["R"], image_uri: "", role: "", format_legal: true, commander_legal: true, validation_issues: [] }
  ],
  presentation: { canvas_width: 1600, canvas_height: 900, zoom: 1, pan_x: 0, pan_y: 0, surface: "slate-grid", show_zone_outlines: false, dim_inactive: false, snap_to_grid: false },
  preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact", collapsed_json: "[]" },
  validation: { commander_count: 1, library_count: 1, library_target: 99, total_count: 99, legal: false, issues: [], entry_issues: {} },
  tags: [{ name: "Turbo" }], tag_suggestions: [],
};

body.appendChild(Object.assign(el("script", { id: "deck-document-data" }), { textContent: JSON.stringify(deck) }));
const root = el("div", { className: "dl-builder", "data-shared": "false", "data-playmat-enabled": "true" });
body.appendChild(root);
head.appendChild(el("meta", { name: "csrf-token", content: "token" }));
body.appendChild(el("button", { id: "save-state", text: "Saved" }));
const menu = el("details", { className: "dl-export-menu", "data-export-menu": "", "data-dismiss-menu": "" });
menu.appendChild(el("summary", { text: "Export" }));
const panel = el("div", { id: "deck-export-actions" });
panel.setAttribute("role", "menu");
const copyBtn = el("button", { type: "button", "data-export-copy": "", text: "Copy list" });
copyBtn.setAttribute("role", "menuitem");
const buy = el("a", { "data-export-buy": "", text: "Buy deck", target: "_blank", rel: "noopener noreferrer", referrerpolicy: "no-referrer" });
buy.setAttribute("role", "menuitem");
const status = el("p", { className: "dl-export-status", "data-export-status": "" });
status.setAttribute("role", "status");
panel.append(copyBtn, buy, status);
menu.appendChild(panel);
body.appendChild(menu);
const fallback = el("dialog", { id: "export-fallback-dialog" });
const fallbackText = el("textarea", { "data-export-fallback-text": "" });
fallbackText.readOnly = true;
fallback.appendChild(fallbackText);
body.appendChild(fallback);
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
const copied = [];
const store = {};
let commandMode = "ok";
let releaseCommand;
let hangCommand = new Promise((resolve) => { releaseCommand = resolve; });
const navigatorState = {
  clipboard: {
    writeText(text) {
      if (navigatorState.clipboardFail) return Promise.reject(new Error("denied"));
      copied.push(text);
      return Promise.resolve();
    }
  },
  clipboardFail: false
};

async function flush(ms = 0) {
  if (ms) await new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0));
}

const windowObj = {
  matchMedia() { return { matches: false, addEventListener() {} }; },
  setTimeout, clearTimeout,
  crypto: { randomUUID: () => "uuid-1" },
  DeckLabSelects: { refresh() {} },
  prompt() { throw new Error("share prompt should not run"); }
};
const context = vm.createContext({
  console, document, window: windowObj, setTimeout, clearTimeout,
  localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
  fetch(url, opts = {}) {
    fetches.push({ url: String(url), method: opts.method || "GET" });
    if (String(url).includes("/share")) {
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ url: "http://deck.lab/shared/secret" }) });
    }
    if (String(url).includes("/commands")) {
      const run = () => {
        if (commandMode === "fail") return Promise.reject(new Error("network"));
        const packet = JSON.parse(opts.body || "{}");
        (packet.commands || []).forEach((cmd) => {
          if (cmd.type === "adjust_quantity") {
            const entry = deck.entries.find((item) => item.id === cmd.entry_id);
            if (entry) entry.quantity = Number(entry.quantity) + Number(cmd.delta);
          }
        });
        deck.revision = (deck.revision || 0) + 1;
        return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) });
      };
      if (commandMode === "hang") return hangCommand.then(run);
      return run();
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) });
  },
  AbortController, URL, URLSearchParams, btoa, atob, TextEncoder, Uint8Array,
  location: { href: "http://deck.lab/build/deck/deck-1", origin: "http://deck.lab", pathname: "/build/deck/deck-1", search: "" },
  navigator: navigatorState, Set, Map, Promise, JSON, Math, Number, Date, encodeURIComponent, parseFloat, parseInt, Array, Object, String, Boolean, Error,
  CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
  crypto: windowObj.crypto,
});
vm.runInContext(exportSource, context, { filename: exportPath });
vm.runInContext(builderSource, context, { filename: builderPath });
await flush();

function snapshot() {
  return {
    copyDisabled: !!copyBtn.disabled,
    buyHref: buy.getAttribute("href"),
    buyDisabled: buy.getAttribute("aria-disabled"),
    status: status.textContent,
    copied: copied.slice(),
    shareCalls: fetches.filter((item) => item.url.includes("/share")).length,
    fallbackOpen: !!fallback.open,
    fallbackText: fallbackText.value,
    fallbackReadOnly: !!fallbackText.readOnly,
    entries: deck.entries.map((item) => ({ id: item.id, name: item.name, quantity: item.quantity }))
  };
}

const initial = snapshot();
copyBtn.dispatchEvent(makeEvent("click", { target: copyBtn }));
await flush();
const afterCopy = snapshot();

navigatorState.clipboardFail = true;
copyBtn.dispatchEvent(makeEvent("click", { target: copyBtn }));
await flush();
const afterDenied = snapshot();
const entriesAfterDenied = JSON.stringify(deck.entries);
fallback.close();
await flush();
const afterFallbackClose = {
  fallbackOpen: !!fallback.open,
  fallbackText: fallbackText.value,
  entriesUnchanged: JSON.stringify(deck.entries) === entriesAfterDenied,
  claimedSuccess: status.textContent === "Copied list"
};

navigatorState.clipboardFail = false;
delete navigatorState.clipboard;
status.dataset.exportNotice = "";
status.textContent = "";
copyBtn.dispatchEvent(makeEvent("click", { target: copyBtn }));
await flush();
const afterMissingClipboard = snapshot();
navigatorState.clipboard = {
  writeText(text) {
    if (navigatorState.clipboardFail) return Promise.reject(new Error("denied"));
    copied.push(text);
    return Promise.resolve();
  }
};

menu.open = true;
commandMode = "hang";
hangCommand = new Promise((resolve) => { releaseCommand = resolve; });
const plus = [...document.querySelectorAll("button")].find((n) => (n.getAttribute("aria-label") || "").startsWith("Add one "));
plus.dispatchEvent(makeEvent("click", { target: plus, button: 0 }));
const whilePending = snapshot();
copyBtn.dispatchEvent(makeEvent("click", { target: copyBtn }));
const buyClick = makeEvent("click", { target: buy });
buy.dispatchEvent(buyClick);
const pendingClicks = { copied: copied.slice(), buyPrevented: !!buyClick.defaultPrevented, shareCalls: fetches.filter((item) => item.url.includes("/share")).length };
releaseCommand();
await flush();
const afterSaved = snapshot();

commandMode = "fail";
plus.dispatchEvent(makeEvent("click", { target: plus, button: 0 }));
await flush();
const afterFailed = snapshot();

console.log(JSON.stringify({ initial, afterCopy, afterDenied, afterFallbackClose, afterMissingClipboard, whilePending, pendingClicks, afterSaved, afterFailed }));
"""


def test_builder_export_clipboard_pending_and_mana_pool_payload(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute builder export harness")
    harness = tmp_path / "deck_export_menu_harness.mjs"
    harness.write_text(HARNESS)
    result = subprocess.run(
        [node, str(harness), str(BUILDER_JS), str(EXPORT_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    expected = "1 Kinnan Test\n1 Sol Ring"
    expected_url = mana_pool_url(expected)
    initial = payload["initial"]
    assert initial["copyDisabled"] is False
    assert initial["buyHref"] == expected_url
    assert initial["buyDisabled"] in (None, "")
    assert initial["shareCalls"] == 0
    assert "Lightning Bolt" not in (payload["afterCopy"]["copied"] or [""])[-1]
    assert payload["afterCopy"]["copied"][-1] == expected
    assert payload["afterCopy"]["status"] == "Copied list"
    assert payload["afterCopy"]["shareCalls"] == 0

    denied = payload["afterDenied"]
    assert denied["status"] != "Copied list"
    assert "Copy unavailable" in denied["status"]
    assert denied["fallbackOpen"] is True
    assert denied["fallbackText"] == expected
    assert denied["fallbackReadOnly"] is True
    assert denied["copied"] == payload["afterCopy"]["copied"]

    closed = payload["afterFallbackClose"]
    assert closed["fallbackOpen"] is False
    assert closed["entriesUnchanged"] is True
    assert closed["claimedSuccess"] is False

    missing = payload["afterMissingClipboard"]
    assert "Copy unavailable" in missing["status"]
    assert missing["fallbackOpen"] is True

    pending = payload["whilePending"]
    assert pending["copyDisabled"] is True
    assert pending["buyHref"] is None
    assert pending["buyDisabled"] == "true"
    assert pending["status"] == "Saving…"
    assert payload["pendingClicks"]["copied"] == payload["afterCopy"]["copied"]
    assert payload["pendingClicks"]["buyPrevented"] is True
    assert payload["pendingClicks"]["shareCalls"] == 0

    saved = payload["afterSaved"]
    saved_text = "1 Kinnan Test\n2 Sol Ring"
    assert saved["copyDisabled"] is False
    assert saved["buyHref"] == mana_pool_url(saved_text)
    assert saved["shareCalls"] == 0
    assert saved["status"] != "Copied list"

    failed = payload["afterFailed"]
    assert failed["copyDisabled"] is True
    assert failed["buyHref"] is None
    assert "Retry" in failed["status"]
    assert failed["shareCalls"] == 0
