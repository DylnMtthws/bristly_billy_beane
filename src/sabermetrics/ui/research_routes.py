"""Deck Lab Research pages and research-to-build actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user

from sabermetrics import db
from sabermetrics.deck_documents import DeckDocumentRepo, InvalidCommand
from sabermetrics.research import ResearchRepo

bp = Blueprint("research", __name__, url_prefix="/research")


@bp.context_processor
def _source_context():
    from sabermetrics.research_sync import source_state

    return {"research_source": source_state(Path(current_app.config["DB_PATH"]))}


def _research() -> ResearchRepo:
    return ResearchRepo(Path(current_app.config["DB_PATH"]))


def _documents() -> DeckDocumentRepo:
    return DeckDocumentRepo(Path(current_app.config["DB_PATH"]))


@bp.before_request
def _gate():
    if not current_app.config.get("DECK_LAB_RESEARCH_ENABLED"):
        abort(404)
    if not current_user.is_authenticated:
        from sabermetrics.ui.auth import login_manager

        return login_manager.unauthorized()
    return None


def _int_arg(name: str, default: int) -> int:
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


def _optional_float_arg(name: str) -> float | None:
    try:
        value = request.args.get(name)
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _window_arg(default: int = 90) -> int:
    value = request.args.get("window")
    if value in {"0", "all"}:
        return 0
    try:
        return max(7, min(int(value if value is not None else default), 365))
    except (TypeError, ValueError):
        return default


@bp.get("")
@bp.get("/")
def index():
    tab = request.args.get("tab", "commanders")
    if tab not in {"cards", "commanders", "metagame"}:
        tab = "commanders"
    query = (request.args.get("q") or "").strip()[:120]
    page = max(1, _int_arg("page", 1))
    window_days = _window_arg()
    fav_ids = db.FavoritesRepo(current_app.config["DB_PATH"]).commander_ids(
        current_user.id
    )
    card_colors = [
        color for color in request.args.getlist("card_color") if color in list("WUBRGC")
    ]
    card_filters: dict[str, Any] = {
        "oracle_text": (request.args.get("oracle_text") or "").strip()[:120],
        "type_line": (request.args.get("type_line") or "").strip()[:120],
        "colors": card_colors,
        "color_mode": request.args.get("color_mode", "all"),
        "mana_operator": request.args.get("mana_operator", "lte"),
        "mana_value": _optional_float_arg("mana_value"),
        "rarity": request.args.get("rarity", ""),
    }
    meta_min_percent = _optional_float_arg("meta_min")
    meta_max_percent = _optional_float_arg("meta_max")
    commander_filters: dict[str, Any] = {
        "color_mode": request.args.get("color_mode", "all"),
        "mana_min": _optional_float_arg("mana_min"),
        "mana_max": _optional_float_arg("mana_max"),
        "meta_min": meta_min_percent / 100 if meta_min_percent is not None else None,
        "meta_max": meta_max_percent / 100 if meta_max_percent is not None else None,
    }
    if tab == "cards":
        data = _research().cards(query, page=page, **card_filters)
    elif (
        not query
        and page == 1
        and window_days == 90
        and not request.args.getlist("color")
        and request.args.get("favorites") != "1"
        and request.args.get("sort", "meta") == "meta"
        and all(
            commander_filters[key] is None
            for key in ("mana_min", "mana_max", "meta_min", "meta_max")
        )
    ):
        # The default Commanders view loads the same cohort Meta will need.
        # Reuse its data rather than repeat the expensive query on selection.
        data = current_app.extensions["research_landing_cache"].get(
            Path(current_app.config["DB_PATH"]), _research().commanders
        )
        for row in data["results"]:
            row["favorited"] = row["id"] in fav_ids
    else:
        data = _research().commanders(
            query=query,
            colors=[c for c in request.args.getlist("color") if c in list("WUBRG")],
            favorites=fav_ids,
            favorite_only=request.args.get("favorites") == "1",
            plays_card="",
            window_days=window_days,
            sort=request.args.get("sort", "meta"),
            page=page,
            **commander_filters,
        )
    return render_template(
        "deck_lab/research.html",
        tab=tab,
        data=data,
        query=query,
        window_days=window_days,
        colors=[c for c in request.args.getlist("color") if c in list("WUBRG")],
        favorite_only=request.args.get("favorites") == "1",
        plays_card="",
        sort=request.args.get("sort", "meta"),
        card_filters=card_filters,
        raw_syntax=(request.args.get("syntax") or "").strip()[:160],
        commander_filters=commander_filters,
    )


@bp.get("/commander/<card_id>")
def commander(card_id: str):
    window_days = _window_arg()
    commander_data = _research().commander_detail(card_id, window_days=window_days)
    if commander_data is None:
        abort(404)
    favorite = card_id in db.FavoritesRepo(current_app.config["DB_PATH"]).commander_ids(
        current_user.id
    )
    return render_template(
        "deck_lab/commander.html", commander=commander_data, favorite=favorite
    )


@bp.get("/card/<card_id>")
def card(card_id: str):
    card_data = _research().card_detail(card_id)
    if card_data is None:
        abort(404)
    return render_template("deck_lab/card.html", card=card_data)


@bp.get("/compare")
def compare():
    window_days = _window_arg()
    choices = _research().commander_choices()
    left_id = (request.args.get("left") or "").strip()
    right_id = (request.args.get("right") or "").strip()
    left = (
        _research().commander_detail(left_id, window_days=window_days)
        if left_id
        else None
    )
    right = (
        _research().commander_detail(right_id, window_days=window_days)
        if right_id
        else None
    )
    for selected in (left, right):
        if selected and all(item["id"] != selected["id"] for item in choices):
            choices.append({"id": selected["id"], "name": selected["name"]})
    return render_template(
        "deck_lab/compare.html",
        choices=choices,
        left=left,
        right=right,
        left_id=left_id,
        right_id=right_id,
        window_days=window_days,
    )


@bp.post("/commander/<card_id>/build")
def build_commander(card_id: str):
    commander_data = _research().commander_detail(card_id)
    if commander_data is None:
        abort(404)
    try:
        deck_id = _documents().create(
            current_user.id,
            title=f"{commander_data['name']} build",
            commander_card_ids=commander_data["commander_card_ids"],
        )
    except InvalidCommand as exc:
        abort(400, description=str(exc))
    document = _documents().get(current_user.id, deck_id)
    unsorted = next(z for z in document["zones"] if z["name"] == "Unsorted")
    commands = []
    selected_card = (request.form.get("card_id") or "").strip()
    if selected_card:
        commands.append(
            {
                "type": "add_card",
                "card_id": selected_card,
                "quantity": 1,
                "zone_id": unsorted["id"],
            }
        )
    elif request.form.get("top") == "40":
        commands = [
            {
                "type": "add_card",
                "card_id": item["id"],
                "quantity": 1,
                "zone_id": unsorted["id"],
            }
            for item in commander_data["inclusions"][:40]
            if item.get("id") != card_id
        ]
    if commands:
        _documents().apply_commands(
            current_user.id,
            deck_id,
            expected_revision=0,
            mutation_id=f"research-seed-{deck_id}",
            commands=commands,
        )
    return redirect(url_for("builder.deck", deck_id=deck_id))
