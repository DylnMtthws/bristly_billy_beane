"""Home resume tiles must match Build deck cards, including tile actions."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
LIBRARY_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-library.js"

_CARD_MARKERS = (
    'class="dl-deck-card-hit-area"',
    'class="dl-deck-stack"',
    "data-deck-actions",
    'aria-haspopup="menu"',
    "dl-deck-favorite",
    "data-share-deck",
    "dl-deck-delete-form",
    "Copy share link",
    "Delete deck",
    "Open builder",
    "Manage tags",
    "Export decklist",
)


class _DeckCardParser(HTMLParser):
    """Collect each Build-style deck card and its visible labels."""

    def __init__(self) -> None:
        super().__init__()
        self.cards: list[dict[str, object]] = []
        self._card: dict[str, object] | None = None
        self._capture: str | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {name: value or "" for name, value in attrs}
        classes = attrs_map.get("class", "").split()
        if tag == "article" and "dl-deck-card" in classes:
            self._card = {
                "hit_area": "",
                "image": "",
                "title": "",
                "favorite": "",
                "favorite_action": "",
                "delete_action": "",
                "share_id": "",
                "open": False,
            }
            self._depth = 1
            return
        if self._card is None:
            return
        if tag == "article" and "dl-deck-card" in classes:
            self._depth += 1
        if "dl-deck-card-hit-area" in classes:
            self._card["hit_area"] = attrs_map.get("href", "")
        if tag == "img" and not self._card["image"]:
            self._card["image"] = attrs_map.get("src", "")
        if tag == "h2":
            self._capture = "title"
        if "dl-deck-favorite" in classes:
            self._card["favorite"] = attrs_map.get("aria-label", "")
        if tag == "form":
            action = attrs_map.get("action", "")
            if action.endswith("/favorite"):
                self._card["favorite_action"] = action
            if "dl-deck-delete-form" in classes:
                self._card["delete_action"] = action
        if "data-share-deck" in attrs_map or attrs_map.get("data-share-deck") == "":
            self._card["share_id"] = attrs_map.get("data-deck-id", "")

    def handle_endtag(self, tag: str) -> None:
        if self._capture and tag == "h2":
            self._capture = None
        if self._card is not None and tag == "article":
            self._depth -= 1
            if self._depth <= 0:
                self.cards.append(self._card)
                self._card = None

    def handle_data(self, data: str) -> None:
        if self._card is not None and self._capture == "title":
            self._card["title"] = f"{self._card['title']}{data}"


def _login(client, user_id: str) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True


def _app(tmp_path, monkeypatch, **env: str):
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    path = tmp_path / "resume-tiles.db"
    setup_database(path)
    user = db.UsersRepo(path).create(
        email="resume@example.test",
        display_name="Resume",
        role="user",
        status="active",
    )
    with db.connect(path) as conn:
        conn.execute("""INSERT INTO cards
            (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99,image_uri)
            VALUES('kinnan','oracle-kinnan','Kinnan Test','{G}{U}',2,
             'Legendary Creature — Human Druid','Mana text','["G","U"]',1,1,
             'https://images.example.test/kinnan.jpg')""")
        conn.execute(
            """INSERT INTO cards
            (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99,image_uri)
            VALUES('ring','oracle-ring','Sol Ring','{1}',1,'Artifact','Mana text','[]',0,1,NULL)"""
        )
        conn.commit()
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    _login(client, user)
    return client, path


def _resume_html(html: str) -> str:
    match = re.search(
        r"Pick up where you left off.*?</section>",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    assert match, "home is missing the resume section"
    return match.group(0)


def _cards(html: str) -> list[dict[str, object]]:
    parser = _DeckCardParser()
    parser.feed(html)
    return parser.cards


def _seed_deck(client, title: str) -> str:
    created = client.post(
        "/build/new", json={"title": title, "commander_card_id": "kinnan"}
    )
    assert created.status_code == 201
    deck_id = created.get_json()["id"]
    document = client.get(f"/api/decks/{deck_id}").get_json()
    unsorted = document["zones"][0]["id"]
    edited = client.post(
        f"/api/decks/{deck_id}/commands",
        json={
            "expected_revision": 0,
            "mutation_id": f"ring-{title}",
            "commands": [{"type": "add_card", "card_id": "ring", "zone_id": unsorted}],
        },
    )
    assert edited.status_code == 200
    tagged = client.post(
        f"/api/decks/{deck_id}/commands",
        json={
            "expected_revision": 1,
            "mutation_id": f"tag-{title}",
            "commands": [{"type": "add_tag", "name": "Turbo"}],
        },
    )
    assert tagged.status_code == 200
    return deck_id


def test_home_resume_tiles_match_build_cards_and_keep_actions(
    tmp_path, monkeypatch
) -> None:
    client, path = _app(tmp_path, monkeypatch)
    first = _seed_deck(client, "First draft")
    second = _seed_deck(client, "Kinnan draft")
    newest = _seed_deck(client, "Newest draft")
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE deck_documents SET updated_at=? WHERE id=?",
            ("2026-01-01T00:00:00", first),
        )
        conn.execute(
            "UPDATE deck_documents SET updated_at=? WHERE id=?",
            ("2026-01-02T00:00:00", second),
        )
        conn.execute(
            "UPDATE deck_documents SET updated_at=? WHERE id=?",
            ("2026-01-03T00:00:00", newest),
        )
        conn.commit()

    home = client.get("/").get_data(as_text=True)
    library = client.get("/build").get_data(as_text=True)
    resume = _resume_html(home)
    home_cards = _cards(resume)
    library_cards = _cards(library)

    assert "deck-lab-library.js" in home
    assert "deck-lab-commanders.js" not in home
    assert 'id="new-deck-dialog"' not in home
    assert "data-commander-picker" not in home
    assert "data-library-filters" not in home
    assert "First draft" not in resume
    assert [card["title"] for card in home_cards] == ["Newest draft", "Kinnan draft"]
    assert len(home_cards) == 2
    assert "2 cards" in resume
    assert any(card["title"] == "First draft" for card in library_cards)

    for marker in _CARD_MARKERS:
        assert marker in resume
        assert marker in library
    assert "https://images.example.test/kinnan.jpg" in resume
    assert "mana-U" in resume and "mana-G" in resume
    assert "Turbo" in resume
    assert "Research cards" in resume
    assert "Research commander" in resume
    assert "Add Kinnan draft to favorites" in resume

    kinnan = next(card for card in home_cards if card["title"] == "Kinnan draft")
    assert str(kinnan["hit_area"]).endswith(f"/build/deck/{second}")
    assert kinnan["image"] == "https://images.example.test/kinnan.jpg"
    assert str(kinnan["favorite_action"]).endswith(f"/build/deck/{second}/favorite")
    assert str(kinnan["delete_action"]).endswith(f"/build/deck/{second}/delete")
    assert kinnan["share_id"] == second

    favored = client.post(
        str(kinnan["favorite_action"]),
        headers={"Referer": "http://localhost/"},
    )
    assert favored.status_code == 302
    resume_after = _resume_html(client.get("/").get_data(as_text=True))
    assert "Remove Kinnan draft from favorites" in resume_after
    assert 'class="dl-deck-favorite active"' in resume_after
    favorites = client.get("/build?filter=favorites").get_data(as_text=True)
    assert "Kinnan draft" in favorites
    assert "Add Kinnan draft to favorites" not in favorites

    shared = client.post(f"/api/decks/{second}/share")
    assert shared.status_code == 200
    assert "/shared/deck/" in shared.get_json()["url"]

    deleted = client.post(str(kinnan["delete_action"]))
    assert deleted.status_code == 302
    resume_deleted = _resume_html(client.get("/").get_data(as_text=True))
    assert "Kinnan draft" not in resume_deleted
    assert "Newest draft" in resume_deleted
    assert "First draft" in resume_deleted


def test_home_resume_research_links_follow_the_research_gate(
    tmp_path, monkeypatch
) -> None:
    client, _path = _app(tmp_path, monkeypatch, SABER_DECK_LAB_RESEARCH="0")
    _seed_deck(client, "Table-only")
    resume = _resume_html(client.get("/").get_data(as_text=True))
    assert "Copy share link" in resume
    assert "Research cards" not in resume
    assert "Research commander" not in resume


_HARNESS = r"""
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(process.argv[2], "utf8");

class ClassList {
  constructor(el) { this.el = el; }
  _parts() { return (this.el.className || "").split(/\s+/).filter(Boolean); }
  _set(parts) { this.el.className = [...new Set(parts)].join(" "); }
  add(...names) { this._set(this._parts().concat(names)); }
  contains(name) { return this._parts().includes(name); }
}

class Element {
  constructor(tag, owner) {
    this.tagName = String(tag).toUpperCase();
    this.ownerDocument = owner;
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.className = "";
    this.classList = new ClassList(this);
    this.listeners = {};
    this._text = "";
    this.id = "";
    this.disabled = false;
    this._open = false;
    const self = this;
    this.dataset = new Proxy({}, {
      get(obj, key) {
        if (typeof key !== "string") return undefined;
        const attr = "data-" + key.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase());
        return key in obj ? obj[key] : (self.attributes[attr] || "");
      },
      set(obj, key, value) {
        obj[key] = String(value);
        self.attributes["data-" + key.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase())] = String(value);
        return true;
      },
    });
  }
  get textContent() { return this._text; }
  set textContent(value) { this._text = value == null ? "" : String(value); }
  get open() { return this._open; }
  set open(value) {
    const next = Boolean(value);
    if (next === this._open) return;
    this._open = next;
    this.dispatchEvent(makeEvent("toggle"));
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
    if (name === "content") this.content = String(value);
    if (name.startsWith("data-")) {
      const camel = name.slice(5).replace(/-([a-z])/g, (_, l) => l.toUpperCase());
      this.dataset[camel] = String(value);
    }
  }
  getAttribute(name) { return name === "id" ? this.id || null : this.attributes[name] ?? null; }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  contains(other) { return other === this || this.children.some((c) => c.contains(other)); }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  addEventListener(type, fn) { (this.listeners[type] || (this.listeners[type] = [])).push(fn); }
  focus() { this.ownerDocument.activeElement = this; }
  dispatchEvent(event) {
    event.target = event.target || this;
    event.currentTarget = this;
    (this.listeners[event.type] || []).slice().forEach((fn) => fn.call(this, event));
    if (!event._stopped && this.parentNode && this.parentNode.dispatchEvent) {
      this.parentNode.dispatchEvent(event);
    }
    return !event.defaultPrevented;
  }
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
function walk(el, acc) { acc.push(el); el.children.forEach((c) => walk(c, acc)); return acc; }
function queryAll(root, selector) {
  const all = []; root.children.forEach((c) => walk(c, all));
  return all.filter((el) => tokenize(selector).some((part) => simpleMatch(el, part.trim().split(/\s+/).pop())));
}
function makeEvent(type, props = {}) {
  return {
    type, ...props, defaultPrevented: false, _stopped: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this._stopped = true; },
  };
}
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

const documentElement = new Element("html", null);
const body = new Element("body", null);
const head = new Element("head", null);
documentElement.appendChild(head); documentElement.appendChild(body);
const document = {
  documentElement, body, head, activeElement: body,
  createElement(tag) { const node = new Element(tag, document); node.ownerDocument = document; return node; },
  getElementById(id) { return walk(documentElement, []).find((node) => node.id === id) || null; },
  querySelector(sel) { return queryAll(documentElement, sel)[0] || null; },
  querySelectorAll(sel) { return queryAll(documentElement, sel); },
  addEventListener(type, fn) { documentElement.addEventListener(type, fn); },
  dispatchEvent(event) { return documentElement.dispatchEvent(event); },
};
body.ownerDocument = document;
head.ownerDocument = document;
documentElement.ownerDocument = document;

function card(id, title) {
  const article = el("article", { className: "dl-card dl-deck-card" });
  article.appendChild(el("a", { className: "dl-deck-card-hit-area", href: "/build/deck/" + id }));
  const menu = el("details", { className: "dl-deck-actions", "data-deck-actions": "" });
  const summary = el("summary", { "aria-label": "Actions for " + title, "aria-haspopup": "menu" });
  summary.textContent = "•••";
  menu.appendChild(summary);
  const box = el("div", { className: "dl-deck-action-menu", id: "deck-actions-" + id, role: "menu" });
  box.appendChild(el("a", { href: "/build/deck/" + id, role: "menuitem", text: "Open builder" }));
  box.appendChild(el("a", { href: "/build/deck/" + id + "?panel=tags", role: "menuitem", text: "Manage tags" }));
  const share = el("button", { type: "button", role: "menuitem", "data-share-deck": "", "data-deck-id": id, text: "Copy share link" });
  box.appendChild(share);
  const form = el("form", { className: "dl-deck-delete-form", method: "post", action: "/build/deck/" + id + "/delete", "data-deck-title": title });
  form.appendChild(el("button", { className: "danger", type: "submit", role: "menuitem", text: "Delete deck" }));
  box.appendChild(form);
  menu.appendChild(box);
  article.appendChild(menu);
  const favorite = el("form", { method: "post", action: "/build/deck/" + id + "/favorite" });
  favorite.appendChild(el("button", { className: "dl-deck-favorite", type: "submit", "aria-label": "Add " + title + " to favorites" }));
  article.appendChild(favorite);
  body.appendChild(article);
  return { menu, summary, share, form };
}

head.appendChild(el("meta", { name: "csrf-token", content: "csrf-token" }));
const first = card("deck-a", "Alpha");
const second = card("deck-b", "Beta");

const fetches = [];
const copied = [];
const prompts = [];
const confirms = [];
const timers = [];
let confirmResult = false;
let shareOk = true;
const windowObj = {
  setTimeout(fn) { timers.push(fn); return timers.length; },
  prompt(message, value) { prompts.push({ message, value }); return value; },
  confirm(message) { confirms.push(message); return confirmResult; },
};
const navigatorObj = {
  clipboard: { writeText(text) { copied.push(text); return Promise.resolve(); } },
};

vm.runInContext(source, vm.createContext({
  console, document, window: windowObj, navigator: navigatorObj,
  fetch(url, opts = {}) {
    fetches.push({ url: String(url), opts });
    if (!shareOk) return Promise.resolve({ ok: false });
    return Promise.resolve({ ok: true, json: async () => ({ url: "http://deck.lab/shared/deck/tok" }) });
  },
  setTimeout: windowObj.setTimeout,
  Array, Object, String, Boolean, Promise, JSON, encodeURIComponent, Error,
}), { filename: process.argv[2] });

function flushTimers() { const pending = timers.splice(0); pending.forEach((fn) => fn()); }
async function tick() {
  for (let i = 0; i < 40; i++) await Promise.resolve();
}

const boot = {
  hasDialog: Boolean(document.getElementById("new-deck-dialog")),
  hasFilters: Boolean(document.querySelector("[data-library-filters]")),
  hasPicker: Boolean(document.querySelector("[data-commander-picker]")),
  menus: document.querySelectorAll("[data-deck-actions]").length,
};

first.summary.dispatchEvent(makeEvent("keydown", { key: "ArrowDown" }));
const afterOpen = {
  open: first.menu.open,
  focused: document.activeElement && document.activeElement.textContent,
  otherClosed: !second.menu.open,
};
first.menu.dispatchEvent(makeEvent("keydown", { key: "ArrowDown" }));
const afterNext = document.activeElement && document.activeElement.textContent;
first.menu.dispatchEvent(makeEvent("keydown", { key: "Escape" }));
const afterEscape = {
  open: first.menu.open,
  focused: document.activeElement && document.activeElement.getAttribute("aria-label"),
};

first.menu.open = true;
second.menu.open = true;
const afterSecond = { first: first.menu.open, second: second.menu.open };
document.dispatchEvent(makeEvent("click", { target: body }));
const afterOutside = { first: first.menu.open, second: second.menu.open };
second.menu.open = true;
second.menu.dispatchEvent(makeEvent("click", { target: second.share }));
const afterInside = second.menu.open;
document.dispatchEvent(makeEvent("click", { target: body }));

second.share.dispatchEvent(makeEvent("click", { target: second.share }));
await tick();
const afterShare = {
  copied: copied.slice(),
  label: second.share.textContent,
  csrf: fetches[0] && fetches[0].opts.headers && fetches[0].opts.headers["X-CSRFToken"],
  url: fetches[0] && fetches[0].url,
  disabled: second.share.disabled,
};
flushTimers();
const afterShareRestore = { label: second.share.textContent, disabled: second.share.disabled };

shareOk = false;
second.share.dispatchEvent(makeEvent("click", { target: second.share }));
await tick();
const afterShareFail = second.share.textContent;
flushTimers();

confirmResult = false;
const cancelEvent = makeEvent("submit");
first.form.dispatchEvent(cancelEvent);
const afterCancel = { prevented: cancelEvent.defaultPrevented, message: confirms[0] };
confirmResult = true;
const deleteEvent = makeEvent("submit");
first.form.dispatchEvent(deleteEvent);
const afterDelete = { prevented: deleteEvent.defaultPrevented, confirms: confirms.length };

console.log(JSON.stringify({
  boot, afterOpen, afterNext, afterEscape, afterSecond, afterOutside, afterInside,
  afterShare, afterShareRestore, afterShareFail, afterCancel, afterDelete, prompts,
}));
"""


def test_home_resume_tile_actions_run_without_library_chrome(tmp_path) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute resume-tile menu behavior")
    harness = tmp_path / "resume_tile_actions.mjs"
    harness.write_text(_HARNESS)
    completed = subprocess.run(
        [node, str(harness), str(LIBRARY_JS)],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    payload = json.loads(completed.stdout)
    assert payload["boot"] == {
        "hasDialog": False,
        "hasFilters": False,
        "hasPicker": False,
        "menus": 2,
    }
    assert payload["afterOpen"] == {
        "open": True,
        "focused": "Open builder",
        "otherClosed": True,
    }
    assert payload["afterNext"] == "Manage tags"
    assert payload["afterEscape"] == {
        "open": False,
        "focused": "Actions for Alpha",
    }
    assert payload["afterSecond"] == {"first": False, "second": True}
    assert payload["afterOutside"] == {"first": False, "second": False}
    assert payload["afterInside"] is True
    assert payload["afterShare"]["copied"] == ["http://deck.lab/shared/deck/tok"]
    assert payload["afterShare"]["label"] == "Link copied"
    assert payload["afterShare"]["csrf"] == "csrf-token"
    assert payload["afterShare"]["url"] == "/api/decks/deck-b/share"
    assert payload["afterShare"]["disabled"] is True
    assert payload["afterShareRestore"] == {
        "label": "Copy share link",
        "disabled": False,
    }
    assert payload["afterShareFail"] == "Could not share"
    assert payload["afterCancel"]["prevented"] is True
    assert "Alpha" in payload["afterCancel"]["message"]
    assert payload["afterDelete"] == {"prevented": False, "confirms": 2}
    assert payload["prompts"] == []
