"""DYL-50/52: builder search performance and commander-legal identity scope."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.card_search import (
    catalog_plan,
    reset_search_catalog_cache,
)
from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.research import ResearchRepo
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database

ROOT = Path(__file__).resolve().parents[1]
BUILDER_JS = ROOT / "src" / "sabermetrics" / "ui" / "static" / "deck-lab-builder.js"


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True


def _app(path, monkeypatch):
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    return app


def _seed(path, rows):
    with db.connect(path) as conn:
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99,image_uri,rarity)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()


def _names(cards):
    return [card["name"] for card in cards]


def _database(tmp_path):
    path = tmp_path / "search.db"
    setup_database(path)
    owner = db.UsersRepo(path).create(
        email="owner@example.test", display_name="Owner", status="active"
    )
    other = db.UsersRepo(path).create(
        email="other@example.test", display_name="Other", status="active"
    )
    _seed(
        path,
        [
            (
                "kinnan",
                "ok",
                "Kinnan",
                2,
                "Legendary Creature — Human",
                "",
                '["G","U"]',
                1,
                1,
                None,
                "rare",
            ),
            (
                "krenko",
                "okr",
                "Krenko",
                3,
                "Legendary Creature — Goblin",
                "",
                '["R"]',
                1,
                1,
                None,
                "rare",
            ),
            (
                "kozilek",
                "okz",
                "Kozilek",
                10,
                "Legendary Creature — Eldrazi",
                "",
                "[]",
                1,
                1,
                None,
                "mythic",
            ),
            (
                "tymna",
                "otym",
                "Tymna",
                4,
                "Legendary Creature — Human Cleric",
                "Partner (You can have two commanders if both have partner.)",
                '["W","B"]',
                1,
                1,
                None,
                "rare",
            ),
            (
                "thrasios",
                "othr",
                "Thrasios",
                2,
                "Legendary Creature — Merfolk Wizard",
                "Partner (You can have two commanders if both have partner.)",
                '["G","U"]',
                1,
                1,
                None,
                "rare",
            ),
            (
                "ring",
                "or",
                "Sol Ring",
                1,
                "Artifact",
                "Mana",
                "[]",
                0,
                1,
                None,
                "uncommon",
            ),
            (
                "jitte",
                "oj",
                "Umezawa's Jitte",
                2,
                "Legendary Artifact — Equipment",
                "Equip",
                "[]",
                0,
                1,
                None,
                "rare",
            ),
            (
                "gideon",
                "og",
                "Gideon's Sacrifice",
                1,
                "Instant",
                "Prevent",
                '["W"]',
                0,
                1,
                None,
                "common",
            ),
            (
                "giant",
                "ogi",
                "Two-Headed Giant of Foriys",
                5,
                "Creature — Giant",
                "Double strike",
                '["R"]',
                0,
                1,
                None,
                "rare",
            ),
            (
                "bolt",
                "ob",
                "Lightning Bolt",
                1,
                "Instant",
                "Damage",
                '["R"]',
                0,
                1,
                None,
                "common",
            ),
            (
                "bolt-banned",
                "ob",
                "Lightning Bolt",
                1,
                "Instant",
                "Damage",
                '["R"]',
                0,
                0,
                "https://img.test/bolt-banned.jpg",
                "common",
            ),
            (
                "lotus",
                "ol",
                "Black Lotus",
                0,
                "Artifact",
                "Mana",
                "[]",
                0,
                0,
                None,
                "rare",
            ),
            (
                "mystery",
                "om",
                "Mystery Card",
                1,
                "Instant",
                "Unknown",
                '["U"]',
                0,
                None,
                None,
                "rare",
            ),
            (
                "notlegal",
                "on",
                "Not Legal Charm",
                1,
                "Instant",
                "No",
                '["U"]',
                0,
                0,
                None,
                "common",
            ),
        ]
        + [
            (
                f"fill-{i:02d}",
                f"of-{i:02d}",
                f"Alpha Sample {i:02d}",
                2,
                "Creature — Sample",
                "Text",
                '["G"]',
                0,
                1,
                None,
                "common",
            )
            for i in range(1, 12)
        ]
        + [
            (
                f"page-{i:02d}",
                f"op-{i:02d}",
                f"Pager Card {i:02d}",
                1,
                "Instant",
                "Page",
                "[]",
                0,
                1,
                None,
                "common",
            )
            for i in range(1, 31)
        ]
        + [
            (
                f"page-bad-{i}",
                f"opb-{i}",
                f"Pager Card Banned {i}",
                1,
                "Instant",
                "Page",
                "[]",
                0,
                0,
                None,
                "common",
            )
            for i in range(1, 4)
        ],
    )
    return path, owner, other


def test_punctuation_unique_cap_and_prefix_rank(tmp_path):
    path, _owner, _other = _database(tmp_path)
    repo = DeckDocumentRepo(path)
    assert "Gideon's Sacrifice" in _names(repo.search_cards(query="gideons"))
    assert "Two-Headed Giant of Foriys" in _names(repo.search_cards(query="two headed"))
    assert _names(repo.search_cards(query="umezawas jitte")) == ["Umezawa's Jitte"]
    limited = repo.search_cards(query="Alpha Sample", limit=8)
    assert len(limited) == 8
    assert len({card["id"] for card in limited}) == 8
    assert all(card["name"].startswith("Alpha Sample") for card in limited)
    ranked = repo.search_cards(query="Alpha Sample 01")
    assert ranked[0]["name"] == "Alpha Sample 01"


def test_mixed_legalities_exclude_banned_not_legal_and_unknown(tmp_path):
    path, _owner, _other = _database(tmp_path)
    repo = DeckDocumentRepo(path)
    names = set(_names(repo.search_cards(query="lotus")))
    assert names == set()
    names = set(_names(repo.search_cards(query="mystery")))
    assert names == set()
    names = set(_names(repo.search_cards(query="not legal")))
    assert names == set()
    bolts = repo.search_cards(query="lightning bolt")
    assert _names(bolts) == ["Lightning Bolt"]
    assert bolts[0]["id"] == "bolt"


def test_new_card_is_searchable_after_catalog_update(tmp_path):
    path, _owner, _other = _database(tmp_path)
    repo = DeckDocumentRepo(path)
    assert repo.search_cards(query="brand new probe") == []
    _seed(
        path,
        [
            (
                "probe",
                "oprobe",
                "Brand New Probe",
                1,
                "Instant",
                "Look",
                "[]",
                0,
                1,
                None,
                "common",
            )
        ],
    )
    found = repo.search_cards(query="brand new probe")
    assert _names(found) == ["Brand New Probe"]


def test_name_search_upgrade_on_existing_populated_cards(tmp_path):
    path = tmp_path / "upgrade.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE cards (
            id TEXT PRIMARY KEY,
            oracle_id TEXT NOT NULL,
            name TEXT NOT NULL,
            mana_cost TEXT,
            cmc REAL,
            type_line TEXT,
            oracle_text TEXT,
            color_identity TEXT,
            keywords TEXT,
            is_legal_commander BOOLEAN,
            is_legal_in_99 BOOLEAN,
            set_code TEXT,
            rarity TEXT,
            image_uri TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO cards(id,oracle_id,name,is_legal_in_99,is_legal_commander,color_identity)
        VALUES('gideon','og',"Gideon's Sacrifice",1,0,'["W"]');
        """)
    for i in range(2500):
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,is_legal_in_99,is_legal_commander,color_identity) "
            "VALUES(?,?,?,1,0,'[]')",
            (f"c{i}", f"o{i}", f"Filler {i}" if i % 17 else f"Urza's Filler {i}"),
        )
    conn.execute("UPDATE cards SET rowid=424242 WHERE id='gideon'")
    conn.commit()
    before = dict(conn.execute("SELECT id, rowid FROM cards"))
    assert before["gideon"] == 424242
    assert "name_search" not in {
        row[1] for row in conn.execute("PRAGMA table_info(cards)")
    }
    assert "name_search" not in {
        row[1] for row in conn.execute("PRAGMA table_xinfo(cards)")
    }
    conn.close()
    setup_database(path)
    setup_database(path)
    with sqlite3.connect(path) as upgraded:
        info = {row[1] for row in upgraded.execute("PRAGMA table_info(cards)")}
        xinfo = {row[1] for row in upgraded.execute("PRAGMA table_xinfo(cards)")}
        assert "name_search" not in info
        assert "name_search" in xinfo
        sql = upgraded.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='cards'"
        ).fetchone()[0]
        assert "GENERATED ALWAYS" in sql
        assert "VIRTUAL" in sql
        assert "STORED" not in sql
        needle = upgraded.execute(
            "SELECT name_search FROM cards WHERE id='gideon'"
        ).fetchone()[0]
        assert needle == "Gideons Sacrifice"
        after = dict(upgraded.execute("SELECT id, rowid FROM cards"))
        assert after == before
        versions = {
            row[0] for row in upgraded.execute("SELECT version FROM _schema_version")
        }
        assert "card-search-v1" in versions
        assert "card-search-v2" in versions
    repo = DeckDocumentRepo(path)
    assert _names(repo.search_cards(query="gideons")) == ["Gideon's Sacrifice"]
    assert _names(repo.search_cards(query="urzas filler 0")) == ["Urza's Filler 0"]


def test_malformed_color_identity_fails_closed_when_scoped(tmp_path, monkeypatch):
    path, owner, _other = _database(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,is_legal_in_99,color_identity) "
            "VALUES('bad','obad','Mystery Identity',1,'not-json')"
        )
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,is_legal_in_99,color_identity) "
            "VALUES('nullish','onull','Unknown Identity',1,NULL)"
        )
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name,is_legal_in_99,color_identity) "
            "VALUES('obj','oobj','Object Identity',1,?)",
            ('{"U":1}',),
        )
    repo = DeckDocumentRepo(path)
    assert "Mystery Identity" in _names(repo.search_cards(query="Mystery Identity"))
    assert "Unknown Identity" in _names(repo.search_cards(query="Unknown Identity"))
    assert repo.search_cards(query="Mystery Identity", allowed_colors={"U"}) == []
    assert repo.search_cards(query="Unknown Identity", allowed_colors=set()) == []
    assert repo.search_cards(query="Object Identity", allowed_colors={"U"}) == []
    app = _app(path, monkeypatch)
    client = app.test_client()
    _login(client, owner)
    deck_id = repo.create(owner, commander_card_id="kinnan")
    scoped = client.get(
        f"/api/cards?deck_id={deck_id}&q=Mystery+Identity&limit=8"
    ).get_json()
    assert scoped["results"] == []
    unscoped = client.get("/api/cards?q=Mystery+Identity&limit=8").get_json()
    assert _names(unscoped["results"]) == ["Mystery Identity"]


def test_warm_search_does_not_recompute_catalog_names(tmp_path, monkeypatch):
    path, _owner, _other = _database(tmp_path)
    repo = DeckDocumentRepo(path)
    assert "Gideon's Sacrifice" in _names(repo.search_cards(query="gideons"))
    from sabermetrics import card_search as cs

    original = cs.search_needle
    calls: list[str] = []

    def wrapped(query: str) -> str:
        calls.append(query)
        return str(original(query))

    monkeypatch.setattr(cs, "search_needle", wrapped)
    found = repo.search_cards(query="gideons")
    assert _names(found) == ["Gideon's Sacrifice"]
    assert calls == ["gideons"]


def test_api_authorization_identity_scope_and_fail_closed(tmp_path, monkeypatch):
    path, owner, other = _database(tmp_path)
    repo = DeckDocumentRepo(path)
    app = _app(path, monkeypatch)
    client = app.test_client()
    guest = client.get(
        "/api/cards?q=ring", headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert guest.status_code == 401

    _login(client, owner)
    missing = client.get("/api/cards?deck_id=missing&q=ring&limit=8")
    assert missing.status_code == 404

    other_deck = repo.create(other, commander_card_id="kinnan")
    forbidden = client.get(f"/api/cards?deck_id={other_deck}&q=ring&limit=8")
    assert forbidden.status_code == 404

    gu = repo.create(owner, commander_card_id="kinnan")
    scoped = client.get(f"/api/cards?deck_id={gu}&q=ring&limit=8").get_json()
    assert scoped["scope"] == "Commander identity"
    assert scoped["identity"] == ["G", "U"]
    assert _names(scoped["results"]) == ["Sol Ring"]
    bolt = client.get(f"/api/cards?deck_id={gu}&q=lightning&limit=8").get_json()
    assert bolt["results"] == []
    jitte = client.get(f"/api/cards?deck_id={gu}&q=umezawas&limit=8").get_json()
    assert _names(jitte["results"]) == ["Umezawa's Jitte"]
    client_colors = client.get(
        f"/api/cards?deck_id={gu}&q=lightning&limit=8&color=R&allowed_colors=R"
    ).get_json()
    assert client_colors["results"] == []

    colorless = repo.create(owner, commander_card_id="kozilek")
    only_colorless = client.get(
        f"/api/cards?deck_id={colorless}&q=sol&limit=8"
    ).get_json()
    assert only_colorless["identity"] == []
    assert _names(only_colorless["results"]) == ["Sol Ring"]
    islandish = client.get(
        f"/api/cards?deck_id={colorless}&q=gideons&limit=8"
    ).get_json()
    assert islandish["results"] == []

    unscoped = repo.create(owner, title="No commander")
    open_search = client.get(
        f"/api/cards?deck_id={unscoped}&q=lightning&limit=8"
    ).get_json()
    assert open_search["scope"] == "Unrestricted draft"
    assert open_search["identity"] is None
    assert _names(open_search["results"]) == ["Lightning Bolt"]

    capped = client.get(f"/api/cards?deck_id={gu}&q=Alpha%20Sample&limit=8").get_json()[
        "results"
    ]
    assert len(capped) == 8

    picker = client.get("/api/cards?commander=1&q=kinnan").get_json()
    assert picker["scope"] == "Unrestricted draft"
    assert _names(picker["results"]) == ["Kinnan"]


def test_identity_follows_commander_and_partner_changes(tmp_path, monkeypatch):
    path, owner, _other = _database(tmp_path)
    repo = DeckDocumentRepo(path)
    app = _app(path, monkeypatch)
    client = app.test_client()
    _login(client, owner)
    deck_id = repo.create(owner, commander_card_id="kinnan")
    before = client.get(f"/api/cards?deck_id={deck_id}&q=gideons&limit=8").get_json()
    assert before["results"] == []
    changed = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=0,
        mutation_id="swap-red",
        commands=[{"type": "set_commanders", "card_ids": ["krenko"]}],
    )
    assert changed["revision"] == 1
    after = client.get(f"/api/cards?deck_id={deck_id}&q=lightning&limit=8").get_json()
    assert _names(after["results"]) == ["Lightning Bolt"]
    assert after["identity"] == ["R"]

    pair = repo.create(owner, commander_card_ids=["tymna", "thrasios"])
    white = client.get(f"/api/cards?deck_id={pair}&q=gideons&limit=8").get_json()
    assert _names(white["results"]) == ["Gideon's Sacrifice"]
    assert set(white["identity"]) == {"W", "U", "B", "G"}
    red = client.get(f"/api/cards?deck_id={pair}&q=lightning&limit=8").get_json()
    assert red["results"] == []
    artifact = client.get(f"/api/cards?deck_id={pair}&q=sol&limit=8").get_json()
    assert _names(artifact["results"]) == ["Sol Ring"]


def test_research_results_count_and_pagination_are_commander_legal(
    tmp_path, monkeypatch
):
    path, owner, _other = _database(tmp_path)
    repo = ResearchRepo(path)
    mixed = repo.cards("Pager Card", per_page=24)
    names = _names(mixed["results"])
    assert mixed["total"] == 30
    assert mixed["has_next"] is True
    assert len(names) == 24
    assert all(name.startswith("Pager Card") for name in names)
    assert all("Banned" not in name for name in names)
    page_two = repo.cards("Pager Card", page=2, per_page=24)
    assert len(page_two["results"]) == 6
    assert page_two["has_next"] is False
    assert repo.cards("Black Lotus")["results"] == []
    assert repo.cards("Mystery")["results"] == []
    assert repo.cards("Not Legal")["results"] == []
    bolt = repo.cards("Lightning Bolt")
    assert _names(bolt["results"]) == ["Lightning Bolt"]
    assert bolt["results"][0]["id"] == "bolt"

    app = _app(path, monkeypatch)
    client = app.test_client()
    _login(client, owner)
    html = client.get("/research?tab=cards&q=Pager+Card").data.decode()
    assert "30 commander-legal cards" in html
    assert "Pager Card 01" in html
    assert "Pager Card Banned" not in html
    assert "Black Lotus" not in html
    page_html = client.get("/research?tab=cards&q=Pager+Card&page=2").data.decode()
    assert "Pager Card 25" in page_html
    assert "Pager Card Banned" not in page_html
    lotus_html = client.get("/research?tab=cards&q=Black+Lotus").data.decode()
    assert "0 commander-legal cards" in lotus_html
    assert "No cards match this search" in lotus_html
    assert 'aria-label="Open Black Lotus"' not in lotus_html
    filtered = client.get(
        "/research?tab=cards&q=Lightning&card_type=Instant"
    ).data.decode()
    assert "Lightning Bolt" in filtered
    assert "bolt-banned" not in filtered


def test_search_benchmark_and_query_plan(tmp_path):
    path = tmp_path / "bench.db"
    setup_database(path)
    rows = []
    for i in range(30000):
        legal = 0 if i % 37 == 0 else 1
        name = f"Gideon's Bench {i:05d}" if i % 19 == 0 else f"Bulk Card {i:05d}"
        rows.append(
            (
                f"id-{i}",
                f"o-{i}",
                name,
                1,
                "Instant",
                "Text",
                "[]" if i % 5 else '["G"]',
                1 if i % 80 == 0 else 0,
                legal,
                None if i % 4 else "https://img.test/x.jpg",
                "common",
            )
        )
    _seed(path, rows)
    repo = DeckDocumentRepo(path)
    reset_search_catalog_cache()
    cold_start = time.perf_counter()
    cold = repo.search_cards(query="gideons", limit=8)
    cold_ms = (time.perf_counter() - cold_start) * 1000
    warm_times = []
    for _ in range(3):
        start = time.perf_counter()
        warm = repo.search_cards(query="gideons", limit=8)
        warm_times.append((time.perf_counter() - start) * 1000)
    assert len(cold) == 8
    assert _names(cold) == _names(warm)
    assert all("Gideon" in card["name"] for card in cold)
    assert cold_ms < 400
    assert min(warm_times) < 150
    with db.connect(path) as conn:
        plan = catalog_plan(conn)
    assert "CORRELATED" not in plan.upper()
    assert "SCALAR SUBQUERY" not in plan.upper()
    assert "SCAN" in plan.upper() or "SEARCH" in plan.upper()
    Path("/tmp/dyl-50-search-bench.txt").write_text(
        f"cold_ms={cold_ms:.1f}\nwarm_ms={warm_times}\nplan={plan}\n"
    )


SEARCH_HARNESS = r"""
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
    else this._set(this._parts().includes(name) ? this._parts().filter((n) => n !== name) : this._parts().concat([name]));
  }
}

function el(tag, attrs = {}) {
  const node = {
    tagName: String(tag).toUpperCase(),
    attrs: { ...attrs },
    children: [],
    parentNode: null,
    className: attrs.className || "",
    hidden: attrs.hidden != null,
    value: attrs.value || "",
    style: { _props: {}, setProperty(k, v) { this._props[k] = v; } },
    classList: null,
    dataset: {},
    _text: attrs.text || "",
    setAttribute(name, value) { this.attrs[name] = String(value); if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value); },
    getAttribute(name) { return this.attrs[name] == null ? null : String(this.attrs[name]); },
    removeAttribute(name) { delete this.attrs[name]; },
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
    append(...nodes) { nodes.forEach((n) => this.appendChild(typeof n === "string" ? el("span", { text: n }) : n)); },
    replaceChildren(...nodes) { this.children = []; nodes.forEach((n) => this.appendChild(n)); },
    addEventListener(type, fn) { (this.listeners || (this.listeners = {}))[type] = (this.listeners[type] || []).concat([fn]); },
    dispatchEvent(event) { event.target = event.target || this; (this.listeners && this.listeners[event.type] || []).forEach((fn) => fn(event)); },
    querySelector(sel) { return queryAll(this, sel)[0] || null; },
    querySelectorAll(sel) { return queryAll(this, sel); },
    closest(sel) { let n = this; while (n) { if (matches(n, sel)) return n; n = n.parentNode; } return null; },
    focus() { document.activeElement = this; },
  };
  node.classList = new ClassList(node);
  Object.defineProperty(node, "textContent", {
    get() { return this.children.length ? this.children.map((c) => c.textContent).join("") : this._text; },
    set(value) { this.children = []; this._text = value == null ? "" : String(value); },
  });
  Object.keys(attrs).forEach((key) => {
    if (key === "text" || key === "className") return;
    if (key.startsWith("data-")) node.setAttribute(key, attrs[key] === "" ? "" : attrs[key]);
    else if (key === "hidden") node.hidden = true;
    else node.setAttribute(key, attrs[key]);
  });
  return node;
}

function matches(node, selector) {
  if (selector.startsWith(".")) return (node.className || "").split(/\s+/).includes(selector.slice(1));
  if (selector.startsWith("#")) return node.attrs.id === selector.slice(1);
  if (selector.startsWith("[")) {
    const body = selector.slice(1, -1);
    const eq = body.indexOf("=");
    if (eq < 0) return Object.prototype.hasOwnProperty.call(node.attrs, body) || node.attrs[body] === "";
    const name = body.slice(0, eq), value = body.slice(eq + 1).replace(/^"|"$/g, "");
    return node.getAttribute(name) === value;
  }
  if (selector.includes("[")) {
    const tag = selector.slice(0, selector.indexOf("["));
    return node.tagName === tag.toUpperCase() && matches(node, selector.slice(selector.indexOf("[")));
  }
  return node.tagName === selector.toUpperCase();
}

function queryAll(root, selector) {
  const out = [];
  const parts = selector.split(" ");
  function walk(node) {
    if (node !== root && matches(node, parts[parts.length - 1])) {
      if (parts.length === 1 || (node.parentNode && matches(node.parentNode, parts[0])) || parts.every((part, i) => i === parts.length - 1 ? true : true)) out.push(node);
      if (parts.length === 1) { /* keep */ }
    }
    if (node !== root && matches(node, selector)) out.push(node);
    (node.children || []).forEach(walk);
  }
  if (selector.includes(" ")) {
    const [parent, child] = [parts[0], parts.slice(1).join(" ")];
    queryAll(root, parent).forEach((node) => queryAll(node, child).forEach((hit) => out.push(hit)));
    return out;
  }
  (root.children || []).forEach(walk);
  if (root !== document && matches(root, selector)) out.unshift(root);
  return [...new Set(out)];
}

const document = {
  body: el("body"),
  head: el("head"),
  activeElement: null,
  getElementById(id) { return queryAll(this.body, "#" + id)[0] || queryAll(this.head, "#" + id)[0] || null; },
  querySelector(sel) { return this.getElementById(sel.slice(1)) && sel.startsWith("#") ? this.getElementById(sel.slice(1)) : (queryAll(this.body, sel)[0] || queryAll(this.head, sel)[0] || null); },
  querySelectorAll(sel) { return queryAll(this.body, sel).concat(queryAll(this.head, sel)); },
  addEventListener() {},
  createElement(tag) { return el(tag); },
};
document.body.parentNode = document;
const windowObj = { matchMedia() { return { matches: false, addEventListener() {} }; }, setTimeout, clearTimeout, crypto: { randomUUID: () => "uuid-1" }, DeckLabSelects: { refresh() {} } };

const deck = {
  id: "deck-1", revision: 0,
  entries: [{ id: "e1", card_id: "kinnan", name: "Kinnan", is_commander: true, quantity: 1, zone_id: "z1", type_line: "Creature", mana_value: 2, color_identity: ["G","U"] }],
  zones: [{ id: "z1", name: "Unsorted", layout_mode: "spread", x: 0, y: 0 }],
  preferences: { view_mode: "table", display_mode: "text", group_mode: "zone", sort_mode: "manual", density: "compact" },
  presentation: {}, tags: [], playmats: []
};

document.head.appendChild(Object.assign(el("script", { id: "deck-document-data" }), { textContent: JSON.stringify(deck) }));
const root = el("div", { className: "dl-builder", "data-shared": "false", "data-playmat-enabled": "false" });
document.body.appendChild(root);
document.head.appendChild(el("meta", { name: "csrf-token", content: "token" }));
document.body.appendChild(el("button", { id: "save-state", text: "Saved" }));
document.body.appendChild(el("div", { id: "table-view" }));
document.body.appendChild(el("div", { id: "playmat-view" }));
["zoom-label","mana-curve","color-stats","zone-stats"].forEach((id) => document.body.appendChild(el(id === "zoom-label" ? "span" : "div", { id })));
const combobox = el("div", { className: "dl-card-combobox", "data-card-combobox": "" });
combobox.appendChild(el("input", { "data-card-search": "", role: "combobox" }));
combobox.appendChild(el("select", { "data-add-zone": "" }));
combobox.appendChild(el("ul", { id: "card-search-list", "data-card-results": "", hidden: "" }));
combobox.appendChild(el("div", { "data-search-status": "" }));
document.body.appendChild(combobox);
const dialog = el("dialog", { id: "commanders-dialog" });
dialog.appendChild(el("input", { "data-commander-id": "", value: "kinnan" }));
dialog.appendChild(el("input", { "data-partner-id": "", value: "" }));
document.body.appendChild(dialog);

const fetches = [];
let searchCalls = 0;
let pendingSearch;
function makeEvent(type, extra = {}) {
  return { type, preventDefault() {}, stopPropagation() {}, ...extra };
}
async function flush(ms = 0) {
  if (ms) await new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < 12; i++) await new Promise((r) => setTimeout(r, 0));
}

await (async function boot() {
  vm.runInContext(source, vm.createContext({
    console, document, window: windowObj, setTimeout, clearTimeout,
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    fetch(url, opts = {}) {
      fetches.push({ url: String(url), opts });
      if (String(url).startsWith("/api/cards")) {
        searchCalls += 1;
        const call = searchCalls;
        const payload = call === 1
          ? [{ id: "bolt", name: "Lightning Bolt", type_line: "Instant" }]
          : [{ id: "ring", name: "Sol Ring", type_line: "Artifact" }];
        const result = { ok: true, json: async () => ({ scope: "Commander identity", results: payload }) };
        if (call === 1 && pendingSearch) return pendingSearch.then(() => result);
        return Promise.resolve(result);
      }
      if (String(url).includes("/commands")) {
        deck.entries = [{ id: "e1", card_id: "krenko", name: "Krenko", is_commander: true, quantity: 1, zone_id: "z1", type_line: "Creature", mana_value: 3, color_identity: ["R"] }];
        deck.revision = 1;
        return Promise.resolve({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(deck)) });
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

const input = document.querySelector("[data-card-search]");
const results = document.querySelector("[data-card-results]");
let resolveSlow;
pendingSearch = new Promise((resolve) => { resolveSlow = resolve; });
input.value = "bolt";
input.dispatchEvent(makeEvent("input", { target: input }));
await flush(220);
dialog.returnValue = "save";
dialog.dispatchEvent(makeEvent("close", { target: dialog }));
await flush(40);
resolveSlow();
await flush(80);
const afterSwap = {
  names: [...results.querySelectorAll("[role=option]")].map((n) => n.textContent),
  calls: searchCalls,
  hidden: results.hidden,
};
await flush(220);
const later = {
  names: [...results.querySelectorAll("[role=option]")].map((n) => n.textContent),
  calls: searchCalls,
};

input.dispatchEvent(makeEvent("keydown", { key: "Escape", target: input }));
const escaped = { hidden: results.hidden };

console.log(JSON.stringify({ afterSwap, later, escaped, debounce: source.includes("setTimeout(runSearch, 180)") }));
"""


def test_builder_search_drops_stale_commander_scope(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute builder search behavior")
    harness = tmp_path / "search_scope_harness.mjs"
    harness.write_text(SEARCH_HARNESS)
    completed = subprocess.run(
        [node, str(harness), str(BUILDER_JS)],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    result = json.loads(completed.stdout.splitlines()[-1])
    assert result["debounce"] is True
    assert "Lightning Bolt" not in "".join(result["afterSwap"]["names"])
    assert result["escaped"]["hidden"] is True
    if result["later"]["names"]:
        assert "Sol Ring" in "".join(result["later"]["names"])
        assert "Lightning Bolt" not in "".join(result["later"]["names"])
