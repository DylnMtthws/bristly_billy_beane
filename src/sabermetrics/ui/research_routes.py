"""Deck Lab Research pages and research-to-build actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user

from sabermetrics import db
from sabermetrics.card_discovery import (
    CARD_TYPES,
    RANGE_MAX,
    RARITIES,
    SUPERTYPES,
    full_range,
    normalize_bound,
)
from sabermetrics.deck_documents import DeckDocumentRepo, DeckNotFound, InvalidCommand
from sabermetrics.research import ResearchRepo
from sabermetrics.research_cache import (
    DEFAULT_WINDOW_DAYS,
    ResearchDefaultCache,
    apply_favorites,
)

bp = Blueprint("research", __name__, url_prefix="/research")


@bp.context_processor
def _source_context():
    from sabermetrics.research_sync import source_state

    return {"research_source": source_state(Path(current_app.config["DB_PATH"]))}


@bp.after_request
def _private_research(response: Response) -> Response:
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _research() -> ResearchRepo:
    return ResearchRepo(Path(current_app.config["DB_PATH"]))


def _documents() -> DeckDocumentRepo:
    return DeckDocumentRepo(Path(current_app.config["DB_PATH"]))


def _cache() -> ResearchDefaultCache:
    return cast(ResearchDefaultCache, current_app.extensions["research_default_cache"])


@bp.before_request
def _gate():
    if not current_app.config.get("DECK_LAB_RESEARCH_ENABLED"):
        abort(404)
    if not current_user.is_authenticated:
        from sabermetrics.ui.auth import login_manager

        if request.headers.get("X-Research-Fragment") == "1":
            return {"error": "authentication required"}, 401
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


def _wants_fragment() -> bool:
    return request.headers.get("X-Research-Fragment") == "1"


def _force_full_results() -> bool:
    return request.args.get("results") == "full"


def _pending_data(window_days: int, page: int) -> dict[str, Any]:
    return {
        "results": [],
        "total": 0,
        "recorded_entries": 0,
        "window_days": window_days,
        "page": page,
        "has_next": False,
    }


def _bound_arg(name: str) -> int | None:
    return normalize_bound(request.args.get(name))


def _is_default_cohort(
    tab: str,
    query: str,
    page: int,
    window_days: int,
    commander_filters: dict[str, Any],
) -> bool:
    return (
        tab == "metagame"
        and not query
        and page == 1
        and window_days == DEFAULT_WINDOW_DAYS
        and not request.args.getlist("color")
        and request.args.get("favorites") != "1"
        and request.args.get("sort", "meta") == "meta"
        and request.args.get("color_mode", "all") == "all"
        and all(
            commander_filters[key] is None
            for key in ("mana_min", "mana_max", "meta_min", "meta_max")
        )
    )


def _full_results_href() -> str:
    args = request.args.to_dict(flat=False)
    args["results"] = ["full"]
    query = urlencode(args, doseq=True)
    path = url_for("research.index")
    return f"{path}?{query}" if query else f"{path}?results=full"


def _load_index_state() -> dict[str, Any]:
    tab = request.args.get("tab", "commanders")
    if tab not in {"cards", "commanders", "metagame", "decks"}:
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
    card_color_mode = request.args.get("color_mode", "include")
    if card_color_mode not in {"include", "exclude", "exactly", "all", "any", "exact"}:
        card_color_mode = "include"
    card_filters: dict[str, Any] = {
        "oracle_text": (request.args.get("oracle_text") or "").strip()[:120],
        "type_line": (request.args.get("type_line") or "").strip()[:120],
        "super_type": (request.args.get("super_type") or "").strip(),
        "super_op": request.args.get("super_op", "is"),
        "card_type": (request.args.get("card_type") or "").strip(),
        "type_op": request.args.get("type_op", "is"),
        "sub_type": (request.args.get("sub_type") or "").strip()[:40],
        "sub_op": request.args.get("sub_op", "is"),
        "colors": card_colors,
        "color_mode": card_color_mode,
        "mana_operator": request.args.get("mana_operator", "lte"),
        "mana_value": _optional_float_arg("mana_value"),
        "mana_min_bound": _bound_arg("mana_min"),
        "mana_max_bound": _bound_arg("mana_max"),
        "power_min_bound": _bound_arg("power_min"),
        "power_max_bound": _bound_arg("power_max"),
        "toughness_min_bound": _bound_arg("toughness_min"),
        "toughness_max_bound": _bound_arg("toughness_max"),
        "rarity": request.args.get("rarity", ""),
    }
    meta_min_percent = _optional_float_arg("meta_min")
    meta_max_percent = _optional_float_arg("meta_max")
    mana_lo, mana_hi = _bound_arg("mana_min"), _bound_arg("mana_max")
    if full_range(mana_lo, mana_hi):
        commander_mana_min = commander_mana_max = None
    else:
        commander_mana_min = float(mana_lo) if mana_lo else None
        commander_mana_max = (
            None if mana_hi is None or mana_hi >= RANGE_MAX else float(mana_hi)
        )
    commander_filters: dict[str, Any] = {
        "color_mode": request.args.get("color_mode", "all"),
        "mana_min": commander_mana_min,
        "mana_max": commander_mana_max,
        "meta_min": meta_min_percent / 100 if meta_min_percent is not None else None,
        "meta_max": meta_max_percent / 100 if meta_max_percent is not None else None,
    }
    freshness = "fresh"
    computed_at = ""
    status_text = ""
    data: dict[str, Any]
    if tab == "cards":
        data = _research().cards(query, page=page, **card_filters)
    elif tab == "decks":
        data = _documents().list_public(query=query, page=page)
    elif tab == "commanders":
        catalog_filters = {
            "query": query,
            "colors": [c for c in request.args.getlist("color") if c in list("WUBRG")],
            "color_mode": commander_filters["color_mode"],
            "mana_min_bound": _bound_arg("mana_min"),
            "mana_max_bound": _bound_arg("mana_max"),
            "favorites": fav_ids,
            "favorite_only": request.args.get("favorites") == "1",
            "page": page,
        }
        data = _research().commander_catalog(**catalog_filters)
    elif _is_default_cohort(tab, query, page, window_days, commander_filters):
        cache = _cache()
        view = cache.try_serve()
        if view is None and _force_full_results() and not _wants_fragment():
            try:
                view = cache.compute_blocking()
            except RuntimeError:
                view = None
        if view is None:
            cache.request_refresh()
            data = _pending_data(window_days, page)
            freshness = "pending"
            status_text = (
                "Results could not be updated."
                if _force_full_results()
                else "Preparing commander results."
            )
        else:
            data = apply_favorites(view.data, fav_ids)
            freshness = view.freshness
            computed_at = view.computed_at
            if freshness == "stale":
                status_text = "Updating results. Previous field is still shown."
                cache.request_refresh()
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
            observed_only=True,
            **commander_filters,
        )
    return {
        "tab": tab,
        "data": data,
        "query": query,
        "window_days": window_days,
        "colors": [c for c in request.args.getlist("color") if c in list("WUBRG")],
        "favorite_only": request.args.get("favorites") == "1",
        "plays_card": "",
        "sort": request.args.get("sort", "meta" if tab == "metagame" else "name"),
        "card_filters": card_filters,
        "raw_syntax": (request.args.get("syntax") or "").strip()[:160],
        "commander_filters": commander_filters,
        "freshness": freshness,
        "computed_at": computed_at,
        "status_text": status_text,
        "full_results_href": _full_results_href(),
        "type_options": CARD_TYPES,
        "super_options": SUPERTYPES,
        "rarity_options": RARITIES,
        "range_max": RANGE_MAX,
    }


def _render_index(state: dict[str, Any]) -> Response:
    if _wants_fragment():
        status = 202 if state["freshness"] == "pending" else 200
        response = make_response(
            render_template("deck_lab/research_fragment.html", **state),
            status,
        )
    else:
        response = make_response(render_template("deck_lab/research.html", **state))
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Research-Freshness"] = state["freshness"]
    return response


@bp.get("")
@bp.get("/")
def index():
    return _render_index(_load_index_state())


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


@bp.get("/deck/<deck_id>")
def public_deck(deck_id: str):
    try:
        document = _documents().get_public(deck_id)
    except DeckNotFound:
        abort(404)
    return render_template("deck_lab/public_deck.html", deck=document)


@bp.get("/card/<card_id>")
def card(card_id: str):
    card_data = _research().card_detail(card_id)
    if card_data is None:
        abort(404)
    return render_template("deck_lab/card.html", card=card_data)


@bp.get("/compare")
def compare():
    return redirect(url_for("research.index"))


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
        commands = []
    if commands:
        _documents().apply_commands(
            current_user.id,
            deck_id,
            expected_revision=0,
            mutation_id=f"research-seed-{deck_id}",
            commands=commands,
        )
    return redirect(url_for("builder.deck", deck_id=deck_id))
