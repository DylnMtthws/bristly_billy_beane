"use strict";
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const calls = [];
let reducedMotion = false;
let seqFetch = 0;

class ClassList {
  constructor(el) { this.el = el; }
  _parts() { return (this.el.className || "").split(/\s+/).filter(Boolean); }
  _set(parts) { this.el.className = [...new Set(parts)].join(" "); }
  add(...names) { this._set(this._parts().concat(names)); }
  remove(...names) { this._set(this._parts().filter((n) => !names.includes(n))); }
  toggle(name, on) {
    if (on === false) this.remove(name);
    else if (on === true) this.add(name);
    else if (this.contains(name)) this.remove(name);
    else this.add(name);
  }
  contains(name) { return this._parts().includes(name); }
}

class Style {
  constructor() { this._props = {}; }
  setProperty(name, value) { this._props[name] = String(value); }
  getPropertyValue(name) { return this._props[name] || ""; }
}

class Element {
  constructor(tag, owner) {
    this.tagName = String(tag).toUpperCase();
    this.nodeType = 1;
    this.ownerDocument = owner;
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.className = "";
    this.classList = new ClassList(this);
    this.listeners = {};
    this._text = "";
    this.id = "";
    this.href = "";
    this.value = "";
    this.hidden = false;
    this.disabled = false;
    this.style = new Style();
    this.dataset = {};
  }
  get textContent() { return this._text || this.children.map((c) => c.textContent).join(""); }
  set textContent(value) { this._text = value == null ? "" : String(value); this.children = []; }
  get firstChild() { return this.children[0] || null; }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
    if (name === "href") this.href = String(value);
    if (name === "hidden") this.hidden = true;
    if (name.startsWith("data-")) {
      const camel = name.slice(5).replace(/-([a-z])/g, (_, l) => l.toUpperCase());
      this.dataset[camel] = String(value);
    }
  }
  getAttribute(name) {
    if (name === "class") return this.className || null;
    if (name === "id") return this.id || null;
    if (name === "href") return this.href || this.attributes.href || null;
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }
  hasAttribute(name) {
    return this.getAttribute(name) != null;
  }
  removeAttribute(name) {
    delete this.attributes[name];
    if (name === "hidden") this.hidden = false;
    if (name.startsWith("data-")) {
      const camel = name.slice(5).replace(/-([a-z])/g, (_, l) => l.toUpperCase());
      delete this.dataset[camel];
    }
  }
  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  insertBefore(child, before) {
    child.parentNode = this;
    const idx = this.children.indexOf(before);
    if (idx < 0) this.children.push(child);
    else this.children.splice(idx, 0, child);
    return child;
  }
  replaceChildren(...nodes) {
    this.children.forEach((c) => { c.parentNode = null; });
    this.children = [];
    nodes.forEach((n) => this.appendChild(n));
  }
  contains(other) {
    return other === this || this.children.some((c) => c.contains(other));
  }
  matches(selector) { return simpleMatch(this, selector); }
  closest(selector) {
    let node = this;
    while (node && node.tagName) {
      if (simpleMatch(node, selector)) return node;
      node = node.parentNode;
    }
    return null;
  }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  addEventListener(type, fn, opts) {
    const capture = !!(opts && (opts.capture || opts === true));
    (this.listeners[type] || (this.listeners[type] = [])).push({ fn, capture });
  }
  dispatchEvent(event) {
    event.target = event.target || this;
    event.currentTarget = this;
    const cap = this.listeners[event.type] || [];
    cap.filter((h) => h.capture).forEach((h) => h.fn.call(this, event));
    cap.filter((h) => !h.capture).forEach((h) => h.fn.call(this, event));
    if (!event._stopped && this.parentNode && this.parentNode.dispatchEvent) {
      this.parentNode.dispatchEvent(event);
    }
    return !event.defaultPrevented;
  }
}

function tokenize(selector) {
  return selector.split(",").map((part) => part.trim()).filter(Boolean);
}
function simpleMatch(el, sel) {
  const chunks = sel.trim().match(/#[\w-]+|\.[\w-]+|\[[^\]]+\]|[A-Za-z][\w-]*/g) || [];
  let tag = null;
  const ids = [];
  const classes = [];
  const attrs = [];
  for (const chunk of chunks) {
    if (chunk.startsWith("#")) ids.push(chunk.slice(1));
    else if (chunk.startsWith(".")) classes.push(chunk.slice(1));
    else if (chunk.startsWith("[")) {
      const body = chunk.slice(1, -1);
      const eq = body.indexOf("=");
      if (eq < 0) attrs.push({ name: body, value: null });
      else {
        attrs.push({
          name: body.slice(0, eq).trim(),
          value: body.slice(eq + 1).trim().replace(/^['"]|['"]$/g, "")
        });
      }
    } else tag = chunk.toUpperCase();
  }
  if (tag && el.tagName !== tag) return false;
  if (ids.some((id) => el.id !== id)) return false;
  if (classes.some((c) => !el.classList.contains(c))) return false;
  for (const attr of attrs) {
    const actual = attr.name === "class" ? el.className
      : attr.name === "id" ? el.id
      : attr.name === "href" ? el.href || el.attributes.href
      : el.attributes[attr.name];
    if (attr.value == null) { if (actual == null || actual === false) return false; }
    else if (String(actual) !== attr.value) return false;
  }
  return true;
}
function walk(el, acc) {
  acc.push(el);
  el.children.forEach((c) => walk(c, acc));
  return acc;
}
function queryAll(root, selector) {
  const all = [];
  root.children.forEach((c) => walk(c, all));
  return all.filter((el) => tokenize(selector).some((part) => {
    const steps = part.trim().split(/\s+/);
    return simpleMatch(el, steps[steps.length - 1]);
  }));
}

const documentElement = new Element("html", null);
const body = new Element("body", null);
const head = new Element("head", null);
documentElement.appendChild(head);
documentElement.appendChild(body);

const handlers = {};
function on(name, fn, capture) {
  (handlers[name] = handlers[name] || []).push({ fn, capture: !!capture });
}
function fire(name, event, capture) {
  (handlers[name] || []).filter((h) => h.capture === !!capture).forEach((h) => h.fn(event));
}

const location = new URL("https://decklab.studio/research/?tab=commanders");
const historyStack = [location.href];
const history = {
  pushState(_s, _t, url) { historyStack.push(String(url)); }
};

function fakeFetch(url, init) {
  const rec = { url: String(url), headers: init.headers, seq: ++seqFetch };
  calls.push(rec);
  return new Promise(() => {});
}

const timeouts = [];
const document = {
  documentElement,
  body,
  head,
  activeElement: body,
  createElement(tag) {
    const node = new Element(tag, document);
    node.ownerDocument = document;
    node.content = new Element("content", document);
    node.content.querySelector = (sel) => {
      if (sel === "[data-research-fragment]" && String(node.innerHTML || "").includes("data-research-fragment")) {
        return new Element("fragment", document);
      }
      return null;
    };
    return node;
  },
  createTextNode(text) { return { nodeType: 3, text, textContent: text }; },
  getElementById(id) { return walk(documentElement, []).find((node) => node.id === id) || null; },
  querySelector(sel) { return queryAll(documentElement, sel)[0] || null; },
  querySelectorAll(sel) { return queryAll(documentElement, sel); },
  addEventListener(name, fn, opts) { on(name, fn, !!(opts && (opts.capture || opts === true))); },
  dispatchEvent(event) {
    fire(event.type, event, true);
    fire(event.type, event, false);
    return !event.defaultPrevented;
  }
};
body.ownerDocument = document;
head.ownerDocument = document;
documentElement.ownerDocument = document;
documentElement.addEventListener = function (type, fn, opts) {
  on(type, fn, !!(opts && (opts.capture || opts === true)));
};
documentElement.dispatchEvent = function (event) {
  fire(event.type, event, true);
  fire(event.type, event, false);
  return !event.defaultPrevented;
};

function el(tag, attrs) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([key, value]) => {
    if (key === "className") { node.className = value; node.setAttribute("class", value); }
    else if (key === "text") node.textContent = value;
    else if (key === "value") node.value = String(value);
    else node.setAttribute(key, value);
  });
  return node;
}

const page = el("div", { "data-research-page": "" });
const status = el("p", { "data-research-status": "" });
status.hidden = true;
const search = el("form", { "data-research-search": "" });
search.action = "https://decklab.studio/research/";
search.method = "get";
const tabField = el("input", { name: "tab", value: "commanders" });
tabField.value = "commanders";
search.appendChild(tabField);
const live = el("div", { "data-research-live": "" });
const tabs = el("nav", { className: "dl-segments", "data-research-tabs": "", "aria-label": "Research type" });
["cards", "commanders", "metagame", "decks"].forEach((key, i) => {
  const link = el("a", {
    href: "https://decklab.studio/research/?tab=" + key,
    text: key
  });
  if (i === 1) link.setAttribute("aria-current", "page");
  tabs.appendChild(link);
});
const filters = el("form", { id: "research-filters", method: "get" });
filters.action = "https://decklab.studio/research/";
filters.method = "get";
const range = el("div", { className: "dl-bound-range" });
const min = el("input", { "data-bound-min": "", type: "range", min: "0", max: "10", step: "1", value: "0" });
min.value = "0";
const max = el("input", { "data-bound-max": "", type: "range", min: "0", max: "10", step: "1", value: "10" });
max.value = "10";
const clear = el("button", { "data-bound-clear": "", type: "button", text: "Clear" });
const liveMin = el("span", { "data-bound-live": "min" });
const liveMax = el("span", { "data-bound-live": "max" });
const liveCombined = el("span", { "data-bound-live": "combined" });
liveMin.hidden = true;
liveMax.hidden = true;
liveCombined.hidden = true;
range.appendChild(clear);
range.appendChild(min);
range.appendChild(max);
range.appendChild(liveMin);
range.appendChild(liveMax);
range.appendChild(liveCombined);
filters.appendChild(range);
const results = el("div", { "data-research-results": "", "data-research-freshness": "fresh" });
const fragment = el("div", { "data-research-fragment": "", "data-tab": "commanders" });
fragment.appendChild(tabs);
fragment.appendChild(filters);
fragment.appendChild(results);
live.appendChild(fragment);
page.appendChild(status);
page.appendChild(search);
page.appendChild(live);
body.appendChild(page);

class FormDataStub {
  constructor(form) {
    this._pairs = [];
    (form.querySelectorAll ? form.querySelectorAll("input") : []).forEach((input) => {
      this._pairs.push([input.getAttribute("name") || "q", input.value || ""]);
    });
  }
  forEach(fn) { this._pairs.forEach((pair) => fn(pair[1], pair[0])); }
}

const windowObj = {
  addEventListener(name, fn) { on(name, fn, false); },
  setTimeout(fn) { timeouts.push(fn); return timeouts.length; },
  clearTimeout() {},
  matchMedia(query) {
    return {
      matches: reducedMotion && String(query).includes("prefers-reduced-motion"),
      media: query,
      addListener() {},
      addEventListener() {}
    };
  },
  location,
  history,
  fetch: fakeFetch,
  AbortController: class {
    constructor() { this.signal = { aborted: false }; }
    abort() { this.signal.aborted = true; }
  },
  URL
};

vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), {
  document,
  window: windowObj,
  location,
  URL,
  history,
  fetch: fakeFetch,
  FormData: FormDataStub,
  AbortController: windowObj.AbortController,
  setTimeout: windowObj.setTimeout,
  clearTimeout: windowObj.clearTimeout,
  matchMedia: windowObj.matchMedia,
  encodeURIComponent,
  console
});

function clickLink(link) {
  const event = {
    type: "click",
    button: 0,
    target: link,
    defaultPrevented: false,
    preventDefault() { event.defaultPrevented = true; },
    stopPropagation() { event._stopped = true; },
    metaKey: false,
    ctrlKey: false,
    shiftKey: false,
    altKey: false,
    nodeType: 1
  };
  document.dispatchEvent(event);
  return event;
}

function fireInput(input) {
  const event = {
    type: "input",
    target: input,
    defaultPrevented: false,
    preventDefault() { event.defaultPrevented = true; },
    stopPropagation() { event._stopped = true; }
  };
  input.dispatchEvent(event);
}

assert.strictEqual(liveMin.hidden, true);
assert.strictEqual(liveMax.hidden, true);
assert.strictEqual(liveCombined.hidden, true);
const baselineHidden = liveMin.hidden && liveMax.hidden && liveCombined.hidden;

min.value = "1";
max.value = "7";
fireInput(min);
fireInput(max);
assert.strictEqual(liveMin.hidden, false);
assert.strictEqual(liveMax.hidden, false);
assert.strictEqual(liveCombined.hidden, true);
assert.strictEqual(liveMin.textContent, "1");
assert.strictEqual(liveMax.textContent, "7");
assert.strictEqual(min.getAttribute("aria-valuetext"), "1");
assert.strictEqual(max.getAttribute("aria-valuetext"), "7");
const mid = [liveMin.textContent, liveMax.textContent];

min.value = "2";
max.value = "3";
fireInput(min);
assert.strictEqual(liveCombined.hidden, false);
assert.strictEqual(liveMin.hidden, true);
assert.strictEqual(liveMax.hidden, true);
assert.strictEqual(liveCombined.textContent, "2–3");
const close = liveCombined.textContent;

min.value = "4";
max.value = "4";
fireInput(max);
assert.strictEqual(liveCombined.hidden, false);
assert.strictEqual(liveCombined.textContent, "4");
const coincident = liveCombined.textContent;

min.value = "8";
max.value = "6";
fireInput(min);
assert.strictEqual(min.value, "6");
assert.strictEqual(max.value, "6");
assert.strictEqual(liveCombined.textContent, "6");
const clamped = [min.value, max.value];

min.value = "1";
max.value = "9";
fireInput(min);
clear.dispatchEvent({
  type: "click",
  target: clear,
  preventDefault() {},
  stopPropagation() {}
});
assert.strictEqual(min.value, "0");
assert.strictEqual(max.value, "10");
assert.strictEqual(liveMin.hidden, true);
assert.strictEqual(liveMax.hidden, true);
assert.strictEqual(liveCombined.hidden, true);
assert.strictEqual(max.getAttribute("aria-valuetext"), "10+");
const cleared = liveMin.hidden && liveMax.hidden && liveCombined.hidden;

const beforeFetch = calls.length;
const meta = tabs.querySelectorAll("a[href]")[2];
clickLink(meta);
assert.strictEqual(meta.getAttribute("aria-current"), "page");
assert.strictEqual(tabs.getAttribute("data-research-tab-index"), "2");
assert.strictEqual(tabs.getAttribute("data-research-tab-dir"), "forward");
assert.ok(tabs.querySelector("[data-research-tab-indicator]"));
assert.ok(calls.length > beforeFetch);
const immediate = tabs.getAttribute("data-research-tab-index");
const dir = tabs.getAttribute("data-research-tab-dir");
const fetchOnClick = calls.length > beforeFetch;

const decks = tabs.querySelectorAll("a[href]")[3];
const cards = tabs.querySelectorAll("a[href]")[0];
clickLink(decks);
clickLink(cards);
assert.strictEqual(cards.getAttribute("aria-current"), "page");
assert.strictEqual(tabs.getAttribute("data-research-tab-index"), "0");
const rapid = tabs.getAttribute("data-research-tab-index");

reducedMotion = true;
clickLink(meta);
assert.strictEqual(tabs.getAttribute("data-research-tab-static"), "1");
const reducedStatic = tabs.getAttribute("data-research-tab-static") === "1";
reducedMotion = false;

const dirBeforeFilter = tabs.getAttribute("data-research-tab-dir");
const indexBeforeFilter = tabs.getAttribute("data-research-tab-index");
const submitEvent = {
  type: "submit",
  target: filters,
  defaultPrevented: false,
  preventDefault() { submitEvent.defaultPrevented = true; }
};
document.dispatchEvent(submitEvent);
assert.strictEqual(tabs.getAttribute("data-research-tab-dir"), dirBeforeFilter);
assert.strictEqual(tabs.getAttribute("data-research-tab-index"), indexBeforeFilter);
const filterUnchanged = tabs.getAttribute("data-research-tab-index") === indexBeforeFilter;

location.href = "https://decklab.studio/research/?tab=decks";
fire("popstate", { type: "popstate" }, false);
assert.strictEqual(tabs.getAttribute("data-research-tab-index"), "3");
const popstate = tabs.getAttribute("data-research-tab-index");

console.log(JSON.stringify({
  passed: true,
  live: { baselineHidden, mid, close, coincident, clamped, cleared },
  tabs: { immediate, dir, rapid, reducedStatic, filterUnchanged, fetchOnClick, popstate }
}));
