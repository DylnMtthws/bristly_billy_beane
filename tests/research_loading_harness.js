"use strict";
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

const calls = [];
const historyStack = ["https://decklab.studio/research/"];
let seqFetch = 0;
const pending = {};

function el(name, extras) {
  extras = extras || {};
  const node = {
    nodeType: 1,
    innerHTML: "",
    hidden: false,
    className: "",
    attributes: Object.assign({}, extras.attributes || {}),
    children: [],
    dataset: Object.assign({}, extras.dataset || {}),
    querySelector(sel) {
      if (this._map && this._map[sel]) return this._map[sel];
      return null;
    },
    querySelectorAll() { return this._all || []; },
    closest(sel) {
      if (this._closest && this._closest[sel]) return this._closest[sel];
      return null;
    },
    contains() { return true; },
    getAttribute(attr) { return this.attributes[attr]; },
    setAttribute(attr, value) { this.attributes[attr] = value; },
    removeAttribute(attr) { delete this.attributes[attr]; },
    classList: {
      _on: new Set(),
      toggle(n, on) { if (on) this._on.add(n); else this._on.delete(n); },
      contains(n) { return this._on.has(n); }
    },
    appendChild(child) { this.children.push(child); return child; },
    replaceChildren() { this.replaced = true; this.children = ["replaced"]; },
    addEventListener() {},
    focus() {},
    textContent: ""
  };
  return Object.assign(node, extras);
}

const status = el("status");
const results = el("results", { attributes: { "data-research-freshness": "fresh" } });
const live = el("live");
live.querySelector = function (sel) {
  return sel.includes("data-research-freshness") ? results : null;
};
const tab = el("tab");
tab.value = "commanders";
const search = el("search");
search.action = "https://decklab.studio/research/";
search.querySelector = function (sel) {
  if (sel.includes("name='tab'") || sel.includes('name="tab"') || sel.includes("name='tab'")) return tab;
  if (sel.includes("name='q'")) return { value: "" };
  return null;
};
const page = el("page");
page.contains = function () { return true; };

const handlers = {};
function on(name, fn, capture) {
  (handlers[name] = handlers[name] || []).push({ fn, capture: !!capture });
}
function fire(name, event, capture) {
  (handlers[name] || []).filter((h) => h.capture === !!capture).forEach((h) => h.fn(event));
}

const location = new URL("https://decklab.studio/research/");
const history = {
  pushState(_s, _t, url) { historyStack.push(String(url)); }
};

const document = {
  querySelector(sel) {
    if (sel === "[data-research-page]") return page;
    if (sel === "[data-research-status]") return status;
    if (sel === "[data-research-live]") return live;
    if (sel === "[data-research-results]") return results;
    if (sel === "[data-research-search]") return search;
    if (sel === "meta[name='csrf-token']") return { content: "csrf" };
    if (sel === ".dl-research-search input[type='search']") return { focus() {} };
    if (sel === "[data-research-sort-menu]") return null;
    return null;
  },
  querySelectorAll() { return []; },
  getElementById() { return null; },
  addEventListener(name, fn, opts) {
    on(name, fn, !!(opts && opts.capture || opts === true));
  },
  createElement() {
    const node = el("el");
    node.textContent = "";
    node.content = el("content");
    node.content.querySelector = (sel) => sel === "[data-research-fragment]" && node.innerHTML.includes("data-research-fragment") ? el("fragment") : null;
    return node;
  },
  createTextNode(text) { return { text }; }
};

class AbortController {
  constructor() { this.signal = { aborted: false }; }
  abort() { this.signal.aborted = true; }
}

function fakeFetch(url, init) {
  const rec = { url: String(url), headers: init.headers, seq: ++seqFetch, signal: init.signal };
  calls.push(rec);
  if (pending.fetchImpl) return pending.fetchImpl(url, init, rec);
  return Promise.resolve({
    status: 200,
    ok: true,
    text: () => Promise.resolve('<div data-research-fragment data-research-freshness="fresh"></div>')
  });
}

const windowObj = {
  addEventListener(name, fn) { on(name, fn, false); },
  setTimeout(fn) { pending.timeout = fn; return 1; },
  clearTimeout() { pending.timeout = null; },
  location,
  history,
  fetch: fakeFetch,
  AbortController,
  URL
};

vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), {
  document,
  window: windowObj,
  location,
  URL,
  history,
  fetch: fakeFetch,
  AbortController,
  setTimeout: windowObj.setTimeout,
  clearTimeout: windowObj.clearTimeout,
  encodeURIComponent,
  console
});

function click(href, extras) {
  extras = extras || {};
  const link = {
    href,
    target: extras.linkTarget || "",
    hasAttribute(n) {
      return (n === "download" && !!extras.download) ||
        (n === "data-research-full-results" && !!extras.full);
    },
    closest(sel) {
      if (sel === "a[href]") return link;
      if (sel === "[data-research-page]") return page;
      return null;
    }
  };
  const event = {
    button: extras.button || 0,
    target: { closest(sel) { return link.closest(sel); }, nodeType: 1 },
    defaultPrevented: false,
    preventDefault() { event.defaultPrevented = true; },
    stopPropagation() {},
    metaKey: !!extras.metaKey,
    ctrlKey: !!extras.ctrlKey,
    shiftKey: !!extras.shiftKey,
    altKey: !!extras.altKey
  };
  fire("click", event, true);
  return event;
}

for (const extra of [
  { ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true },
  { button: 1 }, { linkTarget: "_blank" }, { download: true }, { full: true }
]) {
  const before = calls.length;
  const event = click("https://decklab.studio/research/?tab=metagame", extra);
  assert(!event.defaultPrevented, "native navigation must remain");
  assert.strictEqual(calls.length, before);
}

let resolveEarly;
pending.fetchImpl = function (_url, _init, rec) {
  if (rec.seq === 1) {
    return new Promise((resolve) => { resolveEarly = resolve; });
  }
  return Promise.resolve({
    status: 200,
    ok: true,
    text: () => Promise.resolve('<div data-research-fragment data-research-freshness="fresh" data-research-results></div>')
  });
};

click("https://decklab.studio/research/?q=early");
click("https://decklab.studio/research/?q=late");
assert(calls.length >= 2, "expected two fragment fetches, got " + calls.length);

resolveEarly({
  status: 200,
  ok: true,
  text: () => Promise.resolve('<div data-research-stale-winner="1"></div>')
});

function flush() {
  return Promise.resolve().then(() => Promise.resolve()).then(() => Promise.resolve());
}

flush().then(flush).then(() => {
  if (!live.replaced) {
    console.error(JSON.stringify({
      calls: calls.map((c) => ({ url: c.url, seq: c.seq })),
      history: historyStack,
      status: status.textContent,
      handlers: Object.keys(handlers)
    }));
  }
  assert(live.replaced, "successful late response must update the live region");
  assert.ok(historyStack.some((u) => u.includes("q=late")));
  assert.ok(!historyStack.some((u) => u.includes("q=early")));
  pending.fetchImpl = () => Promise.resolve({ status: 500, ok: false, text: () => Promise.resolve("") });
  click("https://decklab.studio/research/?q=fail");
}).then(() => Promise.resolve()).then(() => Promise.resolve()).then(() => {
  assert.ok(String(status.textContent).includes("could not be updated"));
  assert.ok(status.children.length >= 1);
  console.log(JSON.stringify({ passed: true, calls: calls.length, history: historyStack }));
}).catch((err) => {
  console.error(err);
  process.exit(1);
});
