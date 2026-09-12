"""DYL-54: outside, touch, and Escape dismissal of transient menus."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELL_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-shell.js"
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
CSS_PATH = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"

HARNESS = r"""
import fs from "node:fs";
import vm from "node:vm";

const shellPath = process.argv[2];
const builderPath = process.argv[3];
const shellSource = fs.readFileSync(shellPath, "utf8");
const builderSource = fs.readFileSync(builderPath, "utf8");

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
  constructor() { this._props = {}; this.maxHeight = ""; this.overflowY = ""; this.width = ""; this.left = ""; this.top = ""; }
  setProperty(name, value) { this._props[name] = String(value); this[name] = String(value); }
}
function camelToData(key) { return "data-" + key.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase()); }

class Element {
  constructor(tag, owner) {
    this.tagName = String(tag).toUpperCase();
    this.nodeType = tag === "#text" ? 3 : 1;
    this.ownerDocument = owner;
    this.children = [];
    this.childNodes = this.children;
    this.parentNode = null;
    this.parentElement = null;
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
    this.selected = false;
    this.multiple = false;
    this.size = 0;
    this.tabIndex = 0;
    this.src = "";
    this.alt = "";
    this._open = false;
    this.offsetWidth = 180;
    this.offsetHeight = 160;
    this.scrollHeight = 160;
    this.clientWidth = 800;
    this.clientHeight = 600;
    this._rect = { top: 48, bottom: 80, left: 12, right: 190, width: 178, height: 32 };
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
  get open() { return this._open; }
  set open(value) {
    const next = Boolean(value);
    if (next === this._open) return;
    this._open = next;
    this.dispatchEvent(makeEvent("toggle", { bubbles: false, target: this }));
  }
  get options() { return this.tagName === "SELECT" ? this.querySelectorAll("option") : []; }
  get selectedIndex() {
    const opts = this.options;
    const index = opts.findIndex((opt) => opt.selected);
    return index >= 0 ? index : 0;
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
    if (name === "hidden") this.hidden = true;
    if (name === "open") this._open = true;
    if (name.startsWith("data-")) {
      const camel = name.slice(5).replace(/-([a-z])/g, (_, l) => l.toUpperCase());
      this.dataset[camel] = String(value);
    }
  }
  getAttribute(name) {
    if (name === "id") return this.id || null;
    if (name === "class") return this.className || null;
    if (name === "hidden") return this.hidden ? "" : null;
    if (name === "open") return this._open ? "" : null;
    return this.attributes[name] ?? null;
  }
  hasAttribute(name) {
    if (name === "hidden") return this.hidden;
    if (name === "open") return this._open;
    return this.getAttribute(name) != null;
  }
  removeAttribute(name) {
    delete this.attributes[name];
    if (name === "hidden") this.hidden = false;
    if (name === "open") this._open = false;
  }
  _adopt(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    child.parentElement = this;
    return child;
  }
  appendChild(child) { this._adopt(child); this.children.push(child); return child; }
  append(...nodes) { nodes.forEach((n) => this.appendChild(typeof n === "string" ? this.ownerDocument.createTextNode(n) : n)); }
  insertBefore(node, ref) {
    this._adopt(node);
    if (!ref) { this.children.push(node); return node; }
    const i = this.children.indexOf(ref);
    if (i < 0) this.children.push(node);
    else this.children.splice(i, 0, node);
    return node;
  }
  replaceChildren(...nodes) { this.children.forEach((c) => { c.parentNode = null; c.parentElement = null; }); this.children.length = 0; this._text = ""; nodes.forEach((n) => this.appendChild(n)); }
  removeChild(child) { const i = this.children.indexOf(child); if (i >= 0) { this.children.splice(i, 1); child.parentNode = null; child.parentElement = null; } return child; }
  contains(other) { return other === this || this.children.some((c) => c.contains && c.contains(other)); }
  closest(selector) { let node = this; while (node && node.tagName) { if (matches(node, selector)) return node; node = node.parentNode; } return null; }
  matches(selector) { return matches(this, selector); }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  addEventListener(type, fn, opts) {
    const capture = opts === true || (opts && opts.capture);
    const key = type + (capture ? "::capture" : "::bubble");
    (this.listeners[key] || (this.listeners[key] = [])).push(fn);
  }
  removeEventListener(type, fn, opts) {
    const capture = opts === true || (opts && opts.capture);
    const key = type + (capture ? "::capture" : "::bubble");
    this.listeners[key] = (this.listeners[key] || []).filter((h) => h !== fn);
  }
  dispatchEvent(event) { return dispatchPath(event.target || this, event); }
  getBoundingClientRect() { return { ...this._rect }; }
  setPointerCapture() {}
  focus() { this.ownerDocument.activeElement = this; }
  select() {}
  showModal() { this.open = true; this.returnValue = ""; }
}

function tokenize(selector) { return selector.split(",").map((part) => part.trim()).filter(Boolean); }
function simpleMatch(el, sel) {
  const rejectDisabled = /:not\(:disabled\)/.test(sel);
  sel = sel.replace(/:not\([^)]*\)/g, "");
  if (rejectDisabled && el.disabled) return false;
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
    if (attr.value == null) { if (actual == null && !(attr.name === "hidden" && el.hidden) && !(attr.name === "open" && el.open)) return false; }
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
  return {
    type,
    bubbles: props.bubbles !== false,
    button: 0,
    defaultPrevented: false,
    _stopped: false,
    _immediate: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this._stopped = true; },
    stopImmediatePropagation() { this._stopped = true; this._immediate = true; },
    ...props,
  };
}
function fireListeners(el, event, capture) {
  event.currentTarget = el;
  const key = event.type + (capture ? "::capture" : "::bubble");
  const list = (el.listeners[key] || []).slice();
  for (const fn of list) {
    if (event._immediate) break;
    fn.call(el, event);
  }
}
function dispatchPath(start, event) {
  event.target = event.target || start;
  const path = [];
  let node = event.target;
  while (node && node.tagName) { path.push(node); node = node.parentNode; }
  event._stopped = false;
  event._immediate = false;
  for (const el of path.slice().reverse()) {
    if (event._immediate) break;
    fireListeners(el, event, true);
  }
  const bubble = event.bubbles !== false;
  if (!event._immediate && bubble) {
    for (const el of path) {
      if (event._stopped || event._immediate) break;
      fireListeners(el, event, false);
    }
  } else if (!event._immediate && !bubble) {
    fireListeners(event.target, event, false);
  }
  return !event.defaultPrevented;
}

const documentElement = new Element("html", null);
const body = new Element("body", null);
const head = new Element("head", null);
documentElement.appendChild(head); documentElement.appendChild(body);
const document = {
  documentElement, body, head, activeElement: body,
  createElement(tag) { const el = new Element(tag, document); el.ownerDocument = document; return el; },
  createElementNS(_ns, tag) { return document.createElement(tag); },
  createTextNode(text) { const el = document.createElement("#text"); el.nodeType = 3; el.textContent = String(text); return el; },
  getElementById(id) { return walk(documentElement, []).find((el) => el.id === id) || null; },
  querySelector(sel) { return sel === "body" ? body : (queryAll(documentElement, sel)[0] || null); },
  querySelectorAll(sel) { return queryAll(documentElement, sel); },
  addEventListener(type, fn, opts) { documentElement.addEventListener(type, fn, opts); },
  dispatchEvent(event) { return dispatchPath(event.target || documentElement, event); },
};
body.ownerDocument = document;
head.ownerDocument = document;
documentElement.ownerDocument = document;

function el(tag, attrs = {}) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([key, value]) => {
    if (key === "className") node.className = value;
    else if (key === "id") { node.id = value; node.setAttribute("id", value); }
    else if (key === "text") node.textContent = value;
    else if (key === "value") node.value = value;
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
  ],
  entries: [
    { id: "entry-cmd", name: "Kinnan Test", is_commander: true, quantity: 1, zone_id: null, sort_order: 0, type_line: "Legendary Creature", mana_cost: "{G}{U}", mana_value: 2, oracle_text: "", color_identity: ["G","U"], image_uri: "", role: "" },
    ...makeEntries("zone-early", 2, "early"),
    ...makeEntries("zone-grid", 3, "grid"),
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
const searchInput = el("input", { "data-card-search": "", role: "combobox" });
searchInput.setAttribute("aria-expanded", "false");
combobox.appendChild(searchInput);
combobox.appendChild(el("select", { className: "dl-zone-select", "data-add-zone": "" }));
combobox.appendChild(el("ul", { id: "card-search-list", "data-card-results": "", hidden: "" }));
combobox.appendChild(el("div", { "data-search-status": "" }));
body.appendChild(combobox);

const groupSelect = el("select", { className: "dl-zone-select", "data-group": "", "aria-label": "Group cards" });
const groupZone = el("option", { value: "zone", text: "Group: Zone" }); groupZone.selected = true; groupSelect.appendChild(groupZone);
groupSelect.appendChild(el("option", { value: "type", text: "Group: Type" }));
body.appendChild(groupSelect);

const deckOptions = el("details", { className: "dl-decklist-more" });
const deckSummary = el("summary", { "aria-label": "Deck options", text: "•••" });
deckOptions.appendChild(deckSummary);
const deckPanel = el("div");
const commandersBtn = el("button", { "data-commanders-open": "", text: "Choose commanders / partner" });
const densityBtn = el("button", { "data-density": "compact", text: "Compact" });
const nestedInput = el("input", { "data-nested-input": "" });
deckPanel.append(commandersBtn, densityBtn, nestedInput);
deckOptions.appendChild(deckPanel);
body.appendChild(deckOptions);

const account = el("details", { className: "dl-account-menu" });
const accountSummary = el("summary", { "aria-label": "Open account menu", text: "Account" });
account.appendChild(accountSummary);
const accountPop = el("div", { className: "dl-account-popover" });
const profileLink = el("a", { href: "/profile", text: "Profile" });
accountPop.appendChild(profileLink);
account.appendChild(accountPop);
body.appendChild(account);

const exportMenu = el("details", { "data-dismiss-menu": "" });
const exportSummary = el("summary", { "aria-label": "Export", text: "Export" });
exportMenu.appendChild(exportSummary);
const exportPanel = el("div");
const copyBtn = el("button", { "data-copy-list": "", text: "Copy list" });
exportPanel.appendChild(copyBtn);
exportMenu.appendChild(exportPanel);
body.appendChild(exportMenu);

const accordion = el("details", { className: "dl-help-accordion" });
accordion.appendChild(el("summary", { text: "Help" }));
accordion.appendChild(el("div", { text: "Legitimate accordion" }));
accordion.open = true;
body.appendChild(accordion);

const dialog = el("dialog", { id: "zone-dialog" });
dialog.appendChild(el("h2", { text: "New zone" }));
dialog.appendChild(el("input", { "data-zone-name": "" }));
dialog.appendChild(el("output", { "data-zone-error": "" }));
dialog.appendChild(el("button", { value: "cancel", "aria-label": "Close", text: "×" }));
body.appendChild(dialog);
body.appendChild(el("button", { "data-new-zone": "", id: "sidebar-add-zone", "aria-label": "Add zone" }));
body.appendChild(el("div", { "data-deck-tag-list": "" }));
body.appendChild(el("div", { "data-tag-summary": "" }));
body.appendChild(el("div", { "data-tag-options": "" }));

const outside = el("div", { id: "outside-target", text: "outside" });
body.appendChild(outside);

const fetches = [];
const store = {};
let searchCalls = 0;
async function flush(ms = 0) {
  if (ms) await new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0));
}
function commandBodies() {
  return fetches.filter((f) => String(f.url).includes("/commands")).map((f) => JSON.parse(f.opts.body || "{}").commands);
}
function zone(id) { return document.querySelectorAll(".dl-mat-zone").find((z) => z.dataset.zoneId === id); }
function fire(target, type, props = {}) {
  return target.dispatchEvent(makeEvent(type, { target, ...props }));
}

const windowObj = {
  innerWidth: 430, innerHeight: 775,
  matchMedia() { return { matches: false, addEventListener() {} }; },
  setTimeout, clearTimeout,
  crypto: { randomUUID: () => "uuid-1" },
  addEventListener() {},
  prompt() { return null; },
};

const context = vm.createContext({
  console, document, window: windowObj, setTimeout, clearTimeout,
  localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
  fetch(url, opts = {}) {
    fetches.push({ url: String(url), opts });
    if (String(url).startsWith("/api/cards")) {
      searchCalls += 1;
      const payload = [{ id: "ring", name: "Sol Ring", type_line: "Artifact" }];
      return Promise.resolve({ ok: true, json: async () => ({ scope: "Commander identity", results: payload }) });
    }
    if (String(url).includes("/commands")) {
      try {
        const packet = JSON.parse(opts.body || "{}");
        (packet.commands || []).forEach((cmd) => {
          if (cmd.type === "add_card") {
            deck.entries.push({
              id: "added-" + deck.entries.length, card_id: cmd.card_id, name: "Sol Ring",
              is_commander: false, quantity: 1, zone_id: cmd.zone_id, sort_order: 99,
              type_line: "Artifact", mana_cost: "{1}", mana_value: 1, oracle_text: "", color_identity: [], image_uri: "", role: ""
            });
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
  Node: { ELEMENT_NODE: 1, TEXT_NODE: 3 },
  CSS: { escape(value) { return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&"); } },
  MutationObserver: class { constructor(cb) { this.cb = cb; } observe() {} disconnect() {} },
  CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
  crypto: windowObj.crypto,
});

vm.runInContext(shellSource, context, { filename: shellPath });
vm.runInContext(builderSource, context, { filename: builderPath });
await flush();

const results = document.querySelector("[data-card-results]");
const gridBox = zone("zone-grid");
const zoneMenu = gridBox.querySelector(".dl-zone-menu");
const zoneSummary = zoneMenu.querySelector("summary");
const zoneAction = zoneMenu.querySelector("button");
const earlyCard = zone("zone-early").querySelector(".dl-mat-card");

function snapshotCommands() { return JSON.stringify(commandBodies()); }

fire(earlyCard, "click", { button: 0, metaKey: false, ctrlKey: false });
const selectedBefore = {
  barHidden: document.querySelector("[data-playmat-selection]").hidden,
  label: document.querySelector("[data-playmat-selection]").textContent,
};

const commandsBefore = snapshotCommands();

deckOptions.open = true;
outside.focus();
const outsidePointer = fire(outside, "pointerdown", { button: 0 });
const afterOutsidePointer = {
  open: deckOptions.open,
  prevented: outsidePointer === false,
  defaultPrevented: false,
  focus: document.activeElement && document.activeElement.id,
  accordion: accordion.open,
};

deckOptions.open = true;
const insideEvent = makeEvent("pointerdown", { target: nestedInput, button: 0 });
nestedInput.dispatchEvent(insideEvent);
const afterInside = { open: deckOptions.open, prevented: insideEvent.defaultPrevented };

deckOptions.open = true;
const triggerEvent = makeEvent("pointerdown", { target: deckSummary, button: 0 });
deckSummary.dispatchEvent(triggerEvent);
const afterTrigger = { open: deckOptions.open, prevented: triggerEvent.defaultPrevented };

deckOptions.open = true;
fire(outside, "touchstart", { button: 0 });
const afterTouch = deckOptions.open;

exportMenu.open = true;
account.open = true;
fire(outside, "pointerdown", { button: 0 });
const afterExportAccount = { exportOpen: exportMenu.open, accountOpen: account.open };

zoneMenu.open = true;
const stopped = makeEvent("pointerdown", { target: stage, button: 0 });
stage.addEventListener("pointerdown", (event) => { event.stopPropagation(); event.preventDefault(); });
stage.dispatchEvent(stopped);
const afterStoppedBubble = { open: zoneMenu.open, preventDefaultOnTarget: stopped.defaultPrevented };

deckOptions.open = true;
zoneMenu.open = true;
const afterOpenOther = { deck: deckOptions.open, zone: zoneMenu.open };

zoneMenu.open = true;
zoneSummary.focus();
const escapeEvent = makeEvent("keydown", { key: "Escape", target: zoneMenu });
zoneMenu.dispatchEvent(escapeEvent);
const afterEscape = {
  open: zoneMenu.open,
  focused: document.activeElement && document.activeElement.getAttribute("aria-label"),
  prevented: escapeEvent.defaultPrevented,
};

account.open = true;
profileLink.focus();
fire(account, "keydown", { key: "Escape" });
const afterAccountEscape = {
  open: account.open,
  focused: document.activeElement && document.activeElement.getAttribute("aria-label"),
};

const renameCountBefore = commandBodies().filter((cmds) => cmds.some((c) => c.type === "rename_zone")).length;
zoneMenu.open = true;
fire(zoneAction, "click", { button: 0 });
const afterInsideAction = {
  menuOpen: zoneMenu.open,
  dialogOpen: dialog.open,
  extraRename: commandBodies().filter((cmds) => cmds.some((c) => c.type === "rename_zone")).length - renameCountBefore,
};
dialog.open = false;

const laterMenu = el("details", { className: "dl-zone-menu" });
laterMenu.appendChild(el("summary", { "aria-label": "Late zone actions", text: "•••" }));
laterMenu.appendChild(el("div", { className: "dl-zone-menu-popover" }));
gridBox.appendChild(laterMenu);
laterMenu.open = true;
fire(outside, "pointerdown", { button: 0 });
const afterDynamic = laterMenu.open;

accordion.open = true;
dialog.showModal();
const dialogInput = dialog.querySelector("[data-zone-name]");
fire(dialogInput, "pointerdown", { button: 0 });
const afterDialogPointer = { dialogOpen: dialog.open, accordion: accordion.open };
fire(outside, "keydown", { key: "Escape" });
const afterDialogEscape = dialog.open;
dialog.open = false;

const trigger = groupSelect._dlSelectTrigger;
const afterEnhance = {
  hasTrigger: !!(trigger && trigger.getAttribute("role") === "combobox"),
  expanded: trigger && trigger.getAttribute("aria-expanded"),
};
fire(trigger, "click", { button: 0 });
const selectOpen = {
  expanded: trigger.getAttribute("aria-expanded"),
  wrapperOpen: groupSelect._dlSelectComponent.wrapper.classList.contains("open"),
};
outside.focus();
fire(outside, "pointerdown", { button: 0 });
const selectOutside = {
  expanded: trigger.getAttribute("aria-expanded"),
  focus: document.activeElement && document.activeElement.id,
};
fire(trigger, "click", { button: 0 });
fire(trigger, "keydown", { key: "ArrowDown" });
const selectNav = {
  expanded: trigger.getAttribute("aria-expanded"),
  optionFocused: document.activeElement && document.activeElement.getAttribute("role"),
};
const popover = queryAll(body, ".dl-select-popover")[0] || queryAll(documentElement, ".dl-select-popover")[0];
fire(trigger, "keydown", { key: "Escape" });
const selectEscape = {
  expanded: trigger.getAttribute("aria-expanded"),
  focusedTrigger: document.activeElement === trigger,
};

searchInput.value = "sol";
fire(searchInput, "input");
await flush(220);
const searchOpen = {
  hidden: results.hidden,
  expanded: searchInput.getAttribute("aria-expanded"),
  options: results.querySelectorAll("[role=option]").length,
};
const option = results.querySelector("[role=option]");
fire(option, "pointerdown", { button: 0 });
const searchInside = { hidden: results.hidden, expanded: searchInput.getAttribute("aria-expanded") };
fire(outside, "pointerdown", { button: 0 });
const searchOutside = { hidden: results.hidden, expanded: searchInput.getAttribute("aria-expanded") };

searchInput.value = "sol";
fire(searchInput, "input");
await flush(220);
fire(searchInput, "keydown", { key: "Escape" });
const searchEscape = { hidden: results.hidden, expanded: searchInput.getAttribute("aria-expanded") };
searchInput.value = "sol";
fire(searchInput, "input");
fire(outside, "pointerdown", { button: 0 });
await flush(220);
const pendingSearchDismissed = results.hidden;


const selectedAfter = {
  barHidden: document.querySelector("[data-playmat-selection]").hidden,
  label: document.querySelector("[data-playmat-selection]").textContent,
};
const commandsAfter = snapshotCommands();
const fitted = zoneMenu.querySelector(".dl-zone-menu-popover");
zoneMenu.open = true;
const afterFit = fitted && fitted.style.maxHeight;

console.log(JSON.stringify({
  afterOutsidePointer,
  afterInside, afterTrigger, afterTouch, afterExportAccount, afterStoppedBubble,
  afterOpenOther, afterEscape, afterAccountEscape, afterInsideAction, afterDynamic,
  afterDialogPointer, afterDialogEscape, afterEnhance, selectOpen, selectOutside, selectNav, selectEscape,
  searchOpen, searchInside, searchOutside, searchEscape, pendingSearchDismissed, selectedBefore, selectedAfter,
  commandsUnchanged: commandsBefore === commandsAfter,
  afterFit, searchHook: typeof windowObj.DeckLabSearch === "object" && typeof windowObj.DeckLabSearch.close === "function",
}));
"""


def _run(tmp_path: Path) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute menu dismissal behavior")
    harness = tmp_path / "menu_dismissal.mjs"
    harness.write_text(HARNESS)
    log_path = Path("/tmp/test_menu_dismissal.log")
    completed = subprocess.run(
        [node, str(harness), str(SHELL_JS), str(BUILDER_JS)],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    log_path.write_text(
        "exit="
        + str(completed.returncode)
        + "\n"
        + (completed.stdout or "")
        + "\n"
        + (completed.stderr or "")
    )
    if completed.returncode != 0:
        raise AssertionError((completed.stderr or completed.stdout or "")[-2000:])
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return json.loads(lines[-1])


def test_transient_menus_dismiss_outside_touch_escape_and_keep_inside_actions(
    tmp_path,
) -> None:
    payload = _run(tmp_path)
    assert payload["afterOutsidePointer"]["open"] is False
    assert payload["afterOutsidePointer"]["prevented"] is False
    assert payload["afterOutsidePointer"]["focus"] == "outside-target"
    assert payload["afterOutsidePointer"]["accordion"] is True
    assert payload["afterInside"] == {"open": True, "prevented": False}
    assert payload["afterTrigger"] == {"open": True, "prevented": False}
    assert payload["afterTouch"] is False
    assert payload["afterExportAccount"] == {"exportOpen": False, "accountOpen": False}
    assert payload["afterStoppedBubble"]["open"] is False
    assert payload["afterOpenOther"] == {"deck": False, "zone": True}
    assert payload["afterEscape"]["open"] is False
    assert payload["afterEscape"]["focused"] == "Grid Four actions"
    assert payload["afterAccountEscape"] == {
        "open": False,
        "focused": "Open account menu",
    }
    assert payload["afterInsideAction"]["menuOpen"] is False
    assert payload["afterInsideAction"]["dialogOpen"] is True
    assert payload["afterInsideAction"]["extraRename"] == 0
    assert payload["afterDynamic"] is False
    assert payload["afterDialogPointer"] == {"dialogOpen": True, "accordion": True}
    assert payload["afterDialogEscape"] is True
    assert payload["afterEnhance"]["hasTrigger"] is True
    assert payload["selectOpen"]["expanded"] == "true"
    assert payload["selectOutside"]["expanded"] == "false"
    assert payload["selectOutside"]["focus"] == "outside-target"
    assert payload["selectNav"]["expanded"] == "true"
    assert payload["selectNav"]["optionFocused"] == "option"
    assert payload["selectEscape"] == {"expanded": "false", "focusedTrigger": True}
    assert payload["searchOpen"]["hidden"] is False
    assert payload["searchOpen"]["expanded"] == "true"
    assert payload["searchOpen"]["options"] == 1
    assert payload["searchInside"]["hidden"] is False
    assert payload["searchOutside"] == {"hidden": True, "expanded": "false"}
    assert payload["searchEscape"] == {"hidden": True, "expanded": "false"}
    assert payload["pendingSearchDismissed"] is True
    assert payload["selectedBefore"]["barHidden"] is False
    assert "selected" in payload["selectedBefore"]["label"]
    assert payload["selectedAfter"]["barHidden"] is False
    assert payload["selectedAfter"]["label"] == payload["selectedBefore"]["label"]
    assert payload["commandsUnchanged"] is True
    assert payload["afterFit"]
    assert payload["searchHook"] is True


def test_menu_popovers_fit_the_viewport() -> None:
    css = CSS_PATH.read_text()
    assert ".dl-zone-menu-popover" in css
    assert ".dl-decklist-more > div" in css
    assert ".dl-account-popover" in css
    assert "[data-dismiss-menu] > :not(summary)" in css
    assert "max-height: min(280px, calc(100dvh - 16px))" in css
    assert "overscroll-behavior: contain" in css
    shell = SHELL_JS.read_text()
    assert 'addEventListener("pointerdown", dismissOutsidePointer, true)' in shell
    assert 'addEventListener("touchstart", dismissOutsidePointer, true)' in shell
    assert "[data-dismiss-menu]" in shell
    builder = BUILDER_JS.read_text()
    assert "window.DeckLabSearch = { close: closeCardResults }" in builder
