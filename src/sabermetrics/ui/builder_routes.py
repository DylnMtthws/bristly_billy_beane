"""Editable Deck Lab library, builder, and command API."""

from __future__ import annotations

from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user

from sabermetrics import db
from sabermetrics.deck_documents import (
    DeckDocumentRepo,
    DeckNotFound,
    InvalidCommand,
    RevisionConflict,
)
from sabermetrics.ui.feedback_images import sanitize_image

bp = Blueprint("builder", __name__)


def _repo() -> DeckDocumentRepo:
    return DeckDocumentRepo(Path(current_app.config["DB_PATH"]))


@bp.before_request
def _gate():
    if not current_app.config.get("DECK_LAB_BUILDER_ENABLED"):
        abort(404)
    if request.endpoint in {
        "builder.upload_playmat",
        "builder.playmat_image",
        "builder.shared_playmat",
    } and not current_app.config.get("DECK_LAB_PLAYMAT_ENABLED"):
        abort(404)
    if request.endpoint in {"builder.shared", "builder.shared_playmat"}:
        return None
    if not current_user.is_authenticated:
        from sabermetrics.ui.auth import login_manager

        return login_manager.unauthorized()
    return None


@bp.get("/build")
def library():
    query = (request.args.get("q") or "").strip()
    active_filter = request.args.get("filter", "all")
    sort = request.args.get("sort", "edited")
    if active_filter not in {"all", "favorites", "recent"}:
        active_filter = "all"
    if sort not in {"edited", "name"}:
        sort = "edited"
    repo = _repo()
    documents = repo.list_for_owner(
        current_user.id,
        query=query,
        favorite=active_filter == "favorites",
        recent_days=30 if active_filter == "recent" else None,
        sort=sort,
    )
    library_stats = repo.library_stats(current_user.id)
    return render_template(
        "deck_lab/library.html",
        documents=documents,
        query=query,
        active_filter=active_filter,
        sort=sort,
        library_stats=library_stats,
    )


@bp.post("/build/new")
def create_deck():
    values = request.get_json(silent=True) if request.is_json else request.form
    values = values or {}
    try:
        deck_id = _repo().create(
            current_user.id,
            title=str(values.get("title") or "Untitled deck"),
            commander_card_ids=(
                values.get("commander_card_ids")
                if "commander_card_ids" in values
                else [
                    str(values[key])
                    for key in ("commander_card_id", "partner_card_id")
                    if values.get(key)
                ]
            ),
        )
    except InvalidCommand as exc:
        if request.is_json:
            return jsonify(error=str(exc)), 400
        return redirect(url_for("builder.library", error=str(exc)))
    if request.is_json:
        return jsonify(id=deck_id, url=url_for("builder.deck", deck_id=deck_id)), 201
    return redirect(url_for("builder.deck", deck_id=deck_id))


@bp.post("/build/import/generated/<generated_id>")
def import_generated(generated_id: str):
    try:
        deck_id = _repo().import_generated(current_user.id, generated_id)
    except DeckNotFound:
        abort(404)
    return redirect(url_for("builder.deck", deck_id=deck_id))


@bp.post("/build/import/candidate/<candidate_id>")
def import_candidate(candidate_id: str):
    try:
        deck_id = _repo().import_candidate(current_user.id, candidate_id)
    except DeckNotFound:
        abort(404)
    return redirect(url_for("builder.deck", deck_id=deck_id))


@bp.get("/build/deck/<deck_id>")
def deck(deck_id: str):
    try:
        document = _repo().get(current_user.id, deck_id)
    except DeckNotFound:
        abort(404)
    return render_template("deck_lab/builder.html", document=document, shared=False)


@bp.get("/api/decks/<deck_id>")
def deck_json(deck_id: str):
    try:
        return jsonify(_repo().get(current_user.id, deck_id))
    except DeckNotFound:
        return jsonify(error="not_found"), 404


@bp.get("/api/deck-tags")
def deck_tags():
    query = (request.args.get("q") or "").strip()
    return jsonify(results=_repo().search_tags(query=query, limit=20))


@bp.post("/build/deck/<deck_id>/favorite")
def favorite_deck(deck_id: str):
    try:
        document = _repo().get(current_user.id, deck_id)
        _repo().apply_commands(
            current_user.id,
            deck_id,
            expected_revision=int(document["revision"]),
            mutation_id=f"favorite-{db.new_id()}",
            commands=[{"type": "toggle_favorite"}],
        )
    except DeckNotFound:
        abort(404)
    return redirect(request.referrer or url_for("builder.library"))


@bp.post("/build/deck/<deck_id>/delete")
def delete_deck(deck_id: str):
    try:
        custom_surface = _repo().delete(current_user.id, deck_id)
    except DeckNotFound:
        abort(404)
    if custom_surface:
        asset_dir = Path(current_app.config["DECK_LAB_ASSET_DIR"]).resolve()
        surface_path = Path(custom_surface).resolve()
        if surface_path.parent == asset_dir:
            try:
                surface_path.unlink(missing_ok=True)
            except OSError:
                pass
    return redirect(url_for("builder.library"))


@bp.post("/api/decks/<deck_id>/commands")
def commands(deck_id: str):
    values = request.get_json(silent=True) or {}
    raw_commands = values.get("commands")
    command_list = (
        [item for item in raw_commands if isinstance(item, dict)]
        if isinstance(raw_commands, list)
        else []
    )
    if not current_app.config.get("DECK_LAB_PLAYMAT_ENABLED") and any(
        item.get("type") in {"update_presentation", "move_zone"}
        for item in command_list
    ):
        return jsonify(error="playmat_disabled"), 404
    try:
        result = _repo().apply_commands(
            current_user.id,
            deck_id,
            expected_revision=int(values.get("expected_revision", -1)),
            mutation_id=str(values.get("mutation_id") or ""),
            commands=command_list,
        )
        return jsonify(result)
    except DeckNotFound:
        return jsonify(error="not_found"), 404
    except RevisionConflict as exc:
        return (
            jsonify(
                error="revision_conflict",
                detail=str(exc),
                current_revision=exc.current_revision,
            ),
            409,
        )
    except (InvalidCommand, TypeError, ValueError) as exc:
        return jsonify(error="invalid_command", detail=str(exc)), 400


@bp.get("/api/commanders/partners")
def partners():
    return jsonify(
        results=_repo().partner_choices(
            str(request.args.get("commander_id") or ""),
            query=str(request.args.get("q") or "")[:120],
        )
    )


@bp.get("/api/cards")
def cards():
    try:
        mana_max = (
            float(request.args["mana_max"])
            if request.args.get("mana_max") not in (None, "")
            else None
        )
    except ValueError:
        return jsonify(error="invalid_mana_value"), 400
    allowed_colors = None
    deck_id = (request.args.get("deck_id") or "").strip()
    if deck_id:
        try:
            document = _repo().get(current_user.id, deck_id)
        except DeckNotFound:
            return jsonify(error="not_found"), 404
        commanders = [entry for entry in document["entries"] if entry["is_commander"]]
        if commanders:
            allowed_colors = {
                color for entry in commanders for color in entry["color_identity"]
            }
    return jsonify(
        results=_repo().search_cards(
            query=(request.args.get("q") or "").strip()[:120],
            commander_only=request.args.get("commander") == "1",
            oracle_text=(request.args.get("oracle_text") or "").strip()[:300],
            type_line=(request.args.get("type_line") or "").strip()[:120],
            mana_max=mana_max,
            rarity=(request.args.get("rarity") or "").strip()[:30],
            allowed_colors=allowed_colors,
        ),
        scope=(
            "Commander identity" if allowed_colors is not None else "Unrestricted draft"
        ),
    )


@bp.get("/build/deck/<deck_id>/export.txt")
def export(deck_id: str):
    try:
        document = _repo().get(current_user.id, deck_id)
    except DeckNotFound:
        abort(404)
    filename = "".join(
        c if c.isalnum() or c in "-_" else "-" for c in document["title"]
    )
    return Response(
        _repo().export_text(document),
        mimetype="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="{filename or "deck"}.txt"'
        },
    )


@bp.post("/api/decks/<deck_id>/share")
def create_share(deck_id: str):
    try:
        token = _repo().create_share(current_user.id, deck_id)
    except DeckNotFound:
        return jsonify(error="not_found"), 404
    return jsonify(url=url_for("builder.shared", token=token, _external=True))


@bp.delete("/api/decks/<deck_id>/share")
def revoke_share(deck_id: str):
    try:
        _repo().revoke_shares(current_user.id, deck_id)
    except DeckNotFound:
        return jsonify(error="not_found"), 404
    return jsonify(ok=True)


@bp.post("/api/decks/<deck_id>/playmat")
def upload_playmat(deck_id: str):
    upload = request.files.get("playmat")
    if upload is None:
        return jsonify(error="Choose a PNG, JPEG, or WebP image."), 400
    try:
        _repo().get(current_user.id, deck_id)
        image = sanitize_image(upload)
    except DeckNotFound:
        return jsonify(error="not_found"), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    asset_dir = Path(current_app.config["DECK_LAB_ASSET_DIR"]).resolve()
    asset_dir.mkdir(parents=True, exist_ok=True)
    target = asset_dir / f"{deck_id}-{db.new_id()}.png"
    target.write_bytes(image.content)
    try:
        previous = _repo().set_custom_surface(current_user.id, deck_id, str(target))
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if previous:
        old = Path(previous)
        if old.parent == asset_dir and old != target:
            old.unlink(missing_ok=True)
    return jsonify(ok=True, surface="custom")


@bp.get("/api/decks/<deck_id>/playmat")
def playmat_image(deck_id: str):
    try:
        document = _repo().get(current_user.id, deck_id)
    except DeckNotFound:
        abort(404)
    return _send_playmat(str(document["id"]))


@bp.get("/shared/deck/<token>")
def shared(token: str):
    try:
        document = _repo().get_shared(token)
    except DeckNotFound:
        abort(404)
    return render_template("deck_lab/builder.html", document=document, shared=True)


@bp.get("/shared/deck/<token>/playmat")
def shared_playmat(token: str):
    try:
        document = _repo().get_shared(token)
    except DeckNotFound:
        abort(404)
    return _send_playmat(str(document["id"]))


def _send_playmat(deck_id: str):
    with db.connect(current_app.config["DB_PATH"]) as conn:
        row = conn.execute(
            "SELECT custom_surface_path FROM deck_presentations WHERE deck_id=?",
            (deck_id,),
        ).fetchone()
    raw_path = row[0] if row else None
    if not raw_path:
        abort(404)
    path = Path(str(raw_path))
    asset_dir = Path(current_app.config["DECK_LAB_ASSET_DIR"]).resolve()
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        abort(404)
    if resolved.parent != asset_dir:
        abort(404)
    return send_file(resolved, mimetype="image/png", conditional=True, max_age=3600)
