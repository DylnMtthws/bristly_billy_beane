"""Flask routes (D7.3).

Endpoints:
- GET /                         Home / commander selector
- GET /commander/<name>/profile Commander profile view
- POST /generate-deck           Trigger deck generation
- GET /deck/<deck_id>           View generated deck
- GET /reference/search         Search reference material
- GET /report                   Cost and usage report
"""

import json
import logging
import sqlite3
from pathlib import Path

from flask import Blueprint, current_app, redirect, render_template, request, url_for

bp = Blueprint("main", __name__)
logger = logging.getLogger(__name__)


def _db_path() -> Path:
    return current_app.config["DB_PATH"]


@bp.route("/")
def index():
    """Home page: commander search and recent decks."""
    db_path = _db_path()
    query = request.args.get("q", "").strip()

    commanders = []
    recent_decks = []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Search commanders
        if query:
            cursor = conn.execute(
                "SELECT id, name, type_line, color_identity, mana_cost "
                "FROM cards WHERE name LIKE ? AND is_legal_commander = 1 "
                "ORDER BY name LIMIT 20",
                (f"%{query}%",),
            )
            commanders = [dict(row) for row in cursor]
            for c in commanders:
                ci = c.get("color_identity", "[]")
                if isinstance(ci, str):
                    c["color_identity"] = json.loads(ci)

        # Recent generated decks
        cursor = conn.execute(
            "SELECT gd.id, gd.commander_id, gd.budget_usd, gd.power_target, "
            "gd.estimated_bracket, gd.generated_at, c.name as commander_name "
            "FROM generated_decks gd "
            "JOIN cards c ON gd.commander_id = c.id "
            "ORDER BY gd.generated_at DESC LIMIT 10"
        )
        recent_decks = [dict(row) for row in cursor]
    except Exception as e:
        logger.warning("Index query error: %s", e)
    finally:
        conn.close()

    return render_template(
        "index.html",
        query=query,
        commanders=commanders,
        recent_decks=recent_decks,
    )


@bp.route("/commander/<path:name>/profile")
def commander_profile(name: str):
    """View commander profile."""
    db_path = _db_path()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Find commander
        cursor = conn.execute(
            "SELECT id, name FROM cards "
            "WHERE name LIKE ? AND is_legal_commander = 1 LIMIT 1",
            (f"%{name}%",),
        )
        row = cursor.fetchone()
        if row is None:
            return render_template("profile_view.html", error=f"Commander not found: {name}")

        commander_id = row["id"]
        commander_name = row["name"]

        # Try to load cached profile
        profile_cursor = conn.execute(
            "SELECT profile_json, generated_at FROM commander_profiles "
            "WHERE commander_id = ? AND is_stale = 0 "
            "ORDER BY generated_at DESC LIMIT 1",
            (commander_id,),
        )
        profile_row = profile_cursor.fetchone()

        profile = None
        if profile_row:
            profile = json.loads(profile_row["profile_json"])
            profile["_generated_at"] = profile_row["generated_at"]

        # Get EDHREC data
        edhrec_cursor = conn.execute(
            "SELECT * FROM edhrec_commander_data WHERE commander_id = ?",
            (commander_id,),
        )
        edhrec_row = edhrec_cursor.fetchone()
        edhrec = dict(edhrec_row) if edhrec_row else None
        if edhrec:
            for field in ("themes", "top_cards"):
                val = edhrec.get(field, "[]")
                if isinstance(val, str):
                    edhrec[field] = json.loads(val)

        # Get card data
        card_cursor = conn.execute(
            "SELECT * FROM cards WHERE id = ?", (commander_id,)
        )
        card_row = card_cursor.fetchone()
        card = dict(card_row) if card_row else {}
        for field in ("color_identity", "keywords"):
            val = card.get(field, "[]")
            if isinstance(val, str):
                card[field] = json.loads(val)

    finally:
        conn.close()

    return render_template(
        "profile_view.html",
        commander_name=commander_name,
        commander_id=commander_id,
        card=card,
        profile=profile,
        edhrec=edhrec,
    )


@bp.route("/generate-deck", methods=["POST"])
def generate_deck():
    """Trigger deck generation (async-ish: blocks until complete)."""
    db_path = _db_path()

    commander_id = request.form.get("commander_id", "")
    budget = float(request.form.get("budget", 200))
    power = int(request.form.get("power", 3))
    strategy = request.form.get("strategy") or None
    user_intent = request.form.get("user_intent") or None

    if not commander_id:
        return redirect(url_for("main.index"))

    try:
        from sabermetrics.pipeline.deck_builder import DeckBuilder, DeckBuildRequest

        builder = DeckBuilder(db_path)
        req = DeckBuildRequest(
            commander_id=commander_id,
            budget_usd=budget,
            power_target=power,
            strategy=strategy,
            user_intent=user_intent,
        )
        result = builder.build(req)
        return redirect(url_for("main.view_deck", deck_id=result.deck.id))

    except Exception as e:
        logger.error("Deck generation failed: %s", e)
        return render_template(
            "index.html",
            query="",
            commanders=[],
            recent_decks=[],
            error=f"Deck generation failed: {e}",
        )


@bp.route("/deck/<deck_id>")
def view_deck(deck_id: str):
    """View a generated deck."""
    db_path = _db_path()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            "SELECT gd.*, c.name as commander_name, c.type_line, "
            "c.mana_cost, c.oracle_text, c.color_identity, c.image_uri "
            "FROM generated_decks gd "
            "JOIN cards c ON gd.commander_id = c.id "
            "WHERE gd.id = ?",
            (deck_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return render_template("deck_view.html", error="Deck not found")

        deck_data = dict(row)
        deck_data["cards"] = json.loads(deck_data.get("cards_json", "[]"))
        deck_data["rationale"] = json.loads(deck_data.get("rationale", "{}"))

        ci = deck_data.get("color_identity", "[]")
        if isinstance(ci, str):
            deck_data["color_identity"] = json.loads(ci)

        # Enrich cards with full card data
        card_ids = [c.get("card_id", "") for c in deck_data["cards"]]
        if card_ids:
            placeholders = ",".join("?" for _ in card_ids)
            card_cursor = conn.execute(
                f"SELECT id, name, type_line, mana_cost, cmc, oracle_text, "
                f"rarity, image_uri FROM cards WHERE id IN ({placeholders})",
                card_ids,
            )
            card_lookup = {r["id"]: dict(r) for r in card_cursor}

            for card_entry in deck_data["cards"]:
                full = card_lookup.get(card_entry.get("card_id", ""), {})
                card_entry.update(full)

        # Group cards by role
        by_role: dict[str, list] = {}
        for card in deck_data["cards"]:
            role = card.get("slot_role", "other")
            if role not in by_role:
                by_role[role] = []
            by_role[role].append(card)

        deck_data["cards_by_role"] = by_role

    finally:
        conn.close()

    return render_template("deck_view.html", deck=deck_data)


@bp.route("/reference/search")
def reference_search():
    """Search reference material (rules, articles)."""
    query = request.args.get("q", "").strip()
    results = []

    if query:
        try:
            from sabermetrics.reference_layer.retriever import (
                ReferenceQuery,
                ReferenceRetriever,
            )

            db_path = _db_path()
            retriever = ReferenceRetriever(db_path)
            rq = ReferenceQuery(query_text=query, top_k=10)
            raw_results = retriever.retrieve(rq)

            results = [
                {
                    "document": r.document,
                    "section": r.section or "N/A",
                    "content": r.content,
                    "score": round(r.similarity_score, 3),
                }
                for r in raw_results
            ]
        except Exception as e:
            logger.warning("Reference search failed: %s", e)

    return render_template(
        "reference_search.html",
        query=query,
        results=results,
    )


@bp.route("/report")
def cost_report():
    """Cost and usage report."""
    db_path = _db_path()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Spend by call type (last 30 days)
        cursor = conn.execute(
            "SELECT call_type, "
            "COUNT(*) as call_count, "
            "SUM(cost_usd) as total_cost, "
            "SUM(input_tokens) as total_input, "
            "SUM(output_tokens) as total_output "
            "FROM cost_log "
            "WHERE timestamp >= datetime('now', '-30 days') "
            "GROUP BY call_type "
            "ORDER BY total_cost DESC"
        )
        by_type = [dict(row) for row in cursor]

        # Total spend last 30 days
        total_cursor = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) as total "
            "FROM cost_log "
            "WHERE timestamp >= datetime('now', '-30 days')"
        )
        total_30d = total_cursor.fetchone()["total"]

        # Project annual
        annual_projection = total_30d * 12

        # Monthly ceiling
        from sabermetrics.config import settings
        ceiling = settings.llm.monthly_cost_ceiling_usd

        # Recent calls
        recent_cursor = conn.execute(
            "SELECT call_type, model, cost_usd, input_tokens, "
            "output_tokens, timestamp "
            "FROM cost_log "
            "ORDER BY timestamp DESC LIMIT 20"
        )
        recent_calls = [dict(row) for row in recent_cursor]

        # Generated deck count
        deck_cursor = conn.execute(
            "SELECT COUNT(*) as count FROM generated_decks"
        )
        deck_count = deck_cursor.fetchone()["count"]

        # Profile count
        profile_cursor = conn.execute(
            "SELECT COUNT(*) as count FROM commander_profiles WHERE is_stale = 0"
        )
        profile_count = profile_cursor.fetchone()["count"]

    except Exception as e:
        logger.warning("Report query error: %s", e)
        by_type = []
        total_30d = 0.0
        annual_projection = 0.0
        ceiling = 5.0
        recent_calls = []
        deck_count = 0
        profile_count = 0
    finally:
        conn.close()

    return render_template(
        "cost_report.html",
        by_type=by_type,
        total_30d=total_30d,
        annual_projection=annual_projection,
        ceiling=ceiling,
        recent_calls=recent_calls,
        deck_count=deck_count,
        profile_count=profile_count,
    )
