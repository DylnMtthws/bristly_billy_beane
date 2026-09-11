"""Spoiler view must show colored mana pips inside card rules text."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"
CSS_PATH = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab.css"

KINNAN_TEXT = (
    "{T}: Add {C}.\n"
    "{2}{G}{U}: Target artifact you control becomes an artifact creature."
)
BIRDS_TEXT = "{T}: Add {G} or {U}."

_HARNESS = r"""
"use strict";
const fs = require("fs");
const vm = require("vm");

function TextNode(text) {
  this.nodeType = 3;
  this.textContent = String(text);
  this.childNodes = [];
}
function El(tag) {
  this.nodeType = 1;
  this.tagName = String(tag).toLowerCase();
  this.childNodes = [];
  this.parentNode = null;
  this.attributes = Object.create(null);
  this.className = "";
  this.id = "";
  this._text = "";
  this.hidden = false;
  this.style = { setProperty: function (k, v) { this[k] = v; } };
  this.dataset = {};
  var el = this;
  this.classList = {
    add: function () {
      var parts = new Set(String(el.className || "").split(/\s+/).filter(Boolean));
      for (var i = 0; i < arguments.length; i++) parts.add(arguments[i]);
      el.className = Array.from(parts).join(" ");
    },
    remove: function () {
      var parts = new Set(String(el.className || "").split(/\s+/).filter(Boolean));
      for (var i = 0; i < arguments.length; i++) parts.delete(arguments[i]);
      el.className = Array.from(parts).join(" ");
    },
    toggle: function (name, force) {
      var parts = new Set(String(el.className || "").split(/\s+/).filter(Boolean));
      var on = force === undefined ? !parts.has(name) : !!force;
      if (on) parts.add(name); else parts.delete(name);
      el.className = Array.from(parts).join(" ");
    }
  };
}
El.prototype.setAttribute = function (k, v) {
  this.attributes[k] = String(v);
  if (k === "class") this.className = String(v);
  if (k === "id") this.id = String(v);
};
El.prototype.getAttribute = function (k) {
  if (k === "class") return this.className || null;
  if (k === "id") return this.id || null;
  return this.attributes[k] != null ? this.attributes[k] : null;
};
El.prototype.appendChild = function (child) {
  child.parentNode = this;
  this.childNodes.push(child);
  return child;
};
El.prototype.append = function () {
  for (var i = 0; i < arguments.length; i++) this.appendChild(arguments[i]);
};
El.prototype.replaceChildren = function () {
  this.childNodes.forEach(function (c) { c.parentNode = null; });
  this.childNodes = [];
  for (var i = 0; i < arguments.length; i++) this.appendChild(arguments[i]);
};
Object.defineProperty(El.prototype, "textContent", {
  get: function () {
    if (this.childNodes.length) {
      return this.childNodes.map(function (c) { return c.textContent; }).join("");
    }
    return this._text;
  },
  set: function (v) {
    this._text = v == null ? "" : String(v);
    this.childNodes = [];
  }
});
El.prototype.addEventListener = function () {};
El.prototype.querySelector = function () { return null; };
El.prototype.querySelectorAll = function () { return []; };
El.prototype.closest = function () { return null; };

function walk(el, acc) {
  acc.push(el);
  (el.childNodes || []).forEach(function (c) { if (c.nodeType === 1) walk(c, acc); });
  return acc;
}
function visibleText(el) {
  if (el.nodeType === 3) return el.textContent;
  if (/\bdl-visually-hidden\b/.test(el.className || "")) return "";
  if (!(el.childNodes || []).length) return el.textContent || "";
  return (el.childNodes || []).map(visibleText).join("");
}
function hiddenText(el) {
  if (el.nodeType === 3) return "";
  if (/\bdl-visually-hidden\b/.test(el.className || "")) return el.textContent;
  return (el.childNodes || []).map(hiddenText).join("");
}
function inspect(p) {
  var icons = walk(p, []).filter(function (el) { return el.tagName === "i"; });
  var adjacent = false;
  for (var i = 0; i < p.childNodes.length - 1; i++) {
    var left = p.childNodes[i], right = p.childNodes[i + 1];
    if (!left || !right || left.nodeType !== 1 || right.nodeType !== 1) continue;
    var li = left.childNodes[0], ri = right.childNodes[0];
    if (li && ri && /\bmana-G\b/.test(li.className) && /\bmana-U\b/.test(ri.className)) adjacent = true;
  }
  return {
    ariaLabel: p.getAttribute("aria-label"),
    textContent: p.textContent,
    visible: visibleText(p),
    hidden: hiddenText(p),
    hasBr: walk(p, []).some(function (el) { return el.tagName === "br"; }),
    adjacentGU: adjacent,
    icons: icons.map(function (token) {
      return {
        className: token.className,
        text: token.textContent,
        ariaHidden: token.getAttribute("aria-hidden")
      };
    })
  };
}

const dataEl = new El("script");
dataEl.id = "deck-document-data";
dataEl.textContent = JSON.stringify({
  id: "deck-1",
  title: "Spoiler mana",
  revision: 0,
  zones: [{ id: "zone-1", name: "Ramp", sort_order: 0 }],
  entries: [
    {
      id: "e-cmd", zone_id: "zone-1", name: "Kinnan, Bonder Prodigy",
      quantity: 1, is_commander: 1, sort_order: 0, role: "",
      type_line: "Legendary Creature — Human Druid", mana_cost: "{G}{U}",
      mana_value: 2, oracle_text: process.env.KINNAN_TEXT,
      color_identity: ["G", "U"], image_uri: ""
    },
    {
      id: "e-lib", zone_id: "zone-1", name: "Birds of Paradise",
      quantity: 1, is_commander: 0, sort_order: 1, role: "ramp",
      type_line: "Creature — Bird", mana_cost: "{G}", mana_value: 1,
      oracle_text: process.env.BIRDS_TEXT, color_identity: ["G"], image_uri: ""
    }
  ],
  presentation: {},
  preferences: {
    view_mode: "table", display_mode: "spoiler", group_mode: "zone",
    sort_mode: "manual", density: "compact", collapsed_json: "[]"
  },
  tags: [], tag_suggestions: []
});

const builder = new El("div");
builder.className = "dl-builder";
builder.dataset.shared = "true";
builder.dataset.playmatEnabled = "false";
const tableView = new El("div");
tableView.id = "table-view";
const byId = { "deck-document-data": dataEl, "table-view": tableView };
const mobile = process.argv[3] === "mobile";
const documentRef = {
  getElementById: function (id) { return byId[id] || null; },
  querySelector: function (sel) {
    if (sel === ".dl-builder") return builder;
    return null;
  },
  querySelectorAll: function () { return []; },
  createElement: function (tag) { return new El(tag); },
  createElementNS: function (_ns, tag) { return new El(tag); },
  createTextNode: function (text) { return new TextNode(text); },
  addEventListener: function () {}
};
const context = {
  window: null, document: documentRef,
  localStorage: { getItem: function () { return null; }, setItem: function () {}, removeItem: function () {} },
  matchMedia: function () { return { matches: mobile, addEventListener: function () {} }; },
  fetch: function () { return Promise.reject(new Error("no network")); },
  location: { search: "", pathname: "/build/deck/deck-1", origin: "http://local.test", href: "http://local.test/build/deck/deck-1", assign: function () {} },
  navigator: {}, crypto: { randomUUID: function () { return "id"; } },
  console, URL, URLSearchParams, Array, Object, String, Number, Boolean, Math, Date,
  JSON, RegExp, Error, Promise, Set, Map, Symbol, parseInt, parseFloat, isNaN,
  Infinity, NaN, encodeURIComponent, decodeURIComponent
};
context.window = context;
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), context, { filename: process.argv[2] });
const cards = walk(tableView, []).filter(function (el) { return /\bdl-spoiler-card\b/.test(el.className); });
const paragraphs = walk(tableView, []).filter(function (el) { return el.tagName === "p"; });
console.log(JSON.stringify({
  mobile: mobile,
  spoilerCards: cards.length,
  spoilerVisible: !tableView.hidden,
  paragraphs: paragraphs.map(inspect)
}));
"""


def _node() -> str:
    path = shutil.which("node")
    if not path:
        pytest.fail("node is required to execute spoiler rendering")
    return path


def _render_spoiler(tmp_path: Path, *, mobile: bool) -> dict:
    harness = tmp_path / (
        "spoiler-mana-mobile.js" if mobile else "spoiler-mana-desktop.js"
    )
    harness.write_text(_HARNESS)
    result = subprocess.run(
        [_node(), str(harness), str(BUILDER_JS), "mobile" if mobile else "desktop"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "KINNAN_TEXT": KINNAN_TEXT, "BIRDS_TEXT": BIRDS_TEXT},
    )
    return json.loads(result.stdout)


def _assert_spoiler_rules(payload: dict) -> None:
    assert payload["spoilerCards"] == 2
    assert payload["spoilerVisible"]
    paragraphs = payload["paragraphs"]
    assert len(paragraphs) == 2
    kinnan, birds = paragraphs

    assert kinnan["ariaLabel"] is None
    assert birds["ariaLabel"] is None
    assert kinnan["hasBr"] is True
    assert "Add" in kinnan["visible"]
    assert "Target artifact" in kinnan["visible"]
    assert "becomes an artifact creature." in kinnan["visible"]
    for token in ("{T}", "{C}", "{2}", "{G}", "{U}"):
        assert token not in kinnan["visible"]
        assert token in kinnan["hidden"]
        assert token in kinnan["textContent"]
    assert kinnan["adjacentGU"] is True

    classes = [icon["className"] for icon in kinnan["icons"]]
    assert any("mana-C" in cls and "mana" in cls.split() for cls in classes)
    assert any("mana-G" in cls and "mana" in cls.split() for cls in classes)
    assert any("mana-U" in cls and "mana" in cls.split() for cls in classes)
    generic = [
        icon for icon in kinnan["icons"] if "dl-mana-generic" in icon["className"]
    ]
    assert {icon["text"] for icon in generic} >= {"T", "2"}
    assert all(icon["ariaHidden"] == "true" for icon in kinnan["icons"])
    colored = [
        icon
        for icon in kinnan["icons"]
        if re.search(r"\bmana-[CGU]\b", icon["className"])
    ]
    assert {icon["text"] for icon in colored} == {"C", "G", "U"}
    assert all(
        "{" not in icon["text"] and "}" not in icon["text"] for icon in kinnan["icons"]
    )

    assert "Add " in birds["visible"]
    assert " or " in birds["visible"]
    assert "{G}" not in birds["visible"] and "{U}" not in birds["visible"]
    assert "{G}" in birds["hidden"] and "{U}" in birds["hidden"]
    bird_classes = " ".join(icon["className"] for icon in birds["icons"])
    assert "mana-G" in bird_classes and "mana-U" in bird_classes
    assert birds["adjacentGU"] is False


def test_spoiler_view_renders_colored_mana_icons_in_rules_text(tmp_path: Path) -> None:
    desktop = _render_spoiler(tmp_path, mobile=False)
    mobile = _render_spoiler(tmp_path, mobile=True)
    _assert_spoiler_rules(desktop)
    _assert_spoiler_rules(mobile)
    assert desktop["mobile"] is False
    assert mobile["mobile"] is True

    css = CSS_PATH.read_text()
    spoiler_icons = re.search(
        r"\.dl-spoiler-card\s+\.dl-mana-symbol,\s*\.dl-spoiler-card\s+p\s+\.mana\s*\{([^}]+)\}",
        css,
    )
    assert spoiler_icons, "spoiler rules text needs inline mana pip sizing"
    body = spoiler_icons.group(1)
    assert "inline-grid" in body
    assert re.search(r"width\s*:\s*[\d.]+em", body)
    assert ".dl-visually-hidden" in css
    green = re.search(r"\.mana-G\s*\{([^}]+)\}", css)
    blue = re.search(r"\.mana-U\s*\{([^}]+)\}", css)
    assert green and "background" in green.group(1)
    assert blue and "background" in blue.group(1)
