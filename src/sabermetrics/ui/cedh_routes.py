"""cEDH Deck Lab routes.

Separate blueprint from the casual portal because the two are different
products sharing an account system. Everything here is login-gated, owner-
scoped and counted against the same per-user monthly quota and the same global
cost ceiling as the legacy generator — a lab run spends tokens, and a quota
that counted only one of the two paths would not be a quota.

Provenance is not optional decoration on these pages. Every candidate view
states which corpus the card facts came from, whether tournament evidence
existed at all, and whether the simulation figure is a real run, a fixture, or
absent. A page that could show the number without the caveat would be the one
place in the product where a goldfish figure looks like a measure of quality.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user

from sabermetrics import db
from sabermetrics.cedh.domain import BuildConstraints, LabRequest, MetagameWindow
from sabermetrics.cedh.factory import build_default_lab
from sabermetrics.cedh.settings import load_cedh_settings

bp = Blueprint("cedh", __name__, url_prefix="/lab")
logger = logging.getLogger(__name__)


@bp.before_request
def _require_login():
    """Gate every lab route behind an authenticated session."""
    if not current_user.is_authenticated:
        from sabermetrics.ui.auth import login_manager

        return login_manager.unauthorized()
    return None


def _db_path() -> Path:
    db_path: Path = current_app.config["DB_PATH"]
    return db_path


def _monthly_spend(db_path: Path) -> float:
    """Trailing-30-day spend across every provider and both paths."""
    from sabermetrics.cedh.cost_ledger import CostLedger

    return CostLedger(db_path).monthly_spend()


def _can_access(owner_id: str | None) -> bool:
    """A candidate is visible to its owner or any admin."""
    return bool(owner_id == current_user.id or getattr(current_user, "is_admin", False))


@bp.route("/")
def index():
    """Lab home: supported packs, constraints form, this user's candidates."""
    db_path = _db_path()
    lab, modes = build_default_lab(db_path=str(db_path))
    settings = load_cedh_settings()
    repo = db.CedhCandidatesRepo(db_path)
    used = repo.count_this_month(current_user.id)

    return render_template(
        "cedh/lab.html",
        packs=lab.pack_summaries(),
        modes=modes,
        settings=settings,
        candidates=repo.list_for_owner(current_user.id, limit=25),
        quota_used=used,
        quota=current_user.monthly_deck_quota,
    )


@bp.route("/build", methods=["POST"])
def build():
    """Run the lab and persist the candidate."""
    db_path = _db_path()
    settings = load_cedh_settings()

    from sabermetrics.config import settings as legacy_settings

    if _monthly_spend(db_path) >= legacy_settings.llm.monthly_cost_ceiling_usd:
        flash(
            "The lab is paused: the monthly cost ceiling has been reached.",
            "error",
        )
        return redirect(url_for("cedh.index"))

    repo = db.CedhCandidatesRepo(db_path)
    used = repo.count_this_month(current_user.id)
    quota = current_user.monthly_deck_quota
    if used >= quota:
        flash(
            f"Monthly limit reached ({used}/{quota} builds).",
            "error",
        )
        return redirect(url_for("cedh.index"))

    pack_id = (request.form.get("pack_id") or "").strip()
    raw_intent = (request.form.get("raw_intent") or "").strip()
    flex_slots = max(0, min(20, int(request.form.get("flex_slots") or 0)))

    try:
        constraints = BuildConstraints(
            flex_slots=flex_slots,
            metagame=MetagameWindow(
                days=int(request.form.get("window_days") or settings.meta.window_days),
                min_event_size=int(
                    request.form.get("min_event_size") or settings.meta.min_event_size
                ),
            ),
        )
    except (ValueError, TypeError) as exc:
        flash(f"Those constraints are not valid: {exc}", "error")
        return redirect(url_for("cedh.index"))

    lab, modes = build_default_lab(db_path=str(db_path), user_id=current_user.id)
    result = lab.run(
        LabRequest(
            raw_intent=raw_intent,
            pack_id=pack_id or None,
            constraints=constraints,
        )
    )

    if result.candidate is None:
        flash(
            result.unsupported_detail
            or "No supported strategy pack matches that request.",
            "error",
        )
        return redirect(url_for("cedh.index"))

    candidate = result.candidate
    repo.save(
        candidate_id=candidate.candidate_id,
        owner_id=current_user.id,
        pack_id=result.pack_id,
        commander_key=candidate.commander.key,
        commander_name=candidate.commander.display_name,
        deck_sha256=candidate.deck_sha256,
        candidate_json=candidate.to_json(),
        evidence_hash=candidate.provenance.evidence_hash,
        meta_available=candidate.provenance.meta_available,
        simulation_status=(
            result.simulation.status if result.simulation else "not_simulated"
        ),
        simulation_json=(
            result.simulation.model_dump_json() if result.simulation else None
        ),
        explanation_json=(
            result.explanation.model_dump_json() if result.explanation else None
        ),
        warnings=result.warnings,
    )
    logger.info(
        "cEDH candidate %s built for %s (pack=%s, modes=%s, cost=$%.4f)",
        candidate.candidate_id,
        current_user.id,
        result.pack_id,
        modes,
        result.total_cost_usd,
    )
    return redirect(url_for("cedh.candidate", candidate_id=candidate.candidate_id))


def _load_owned(candidate_id: str) -> dict:
    """Fetch a candidate the current user is allowed to see, or abort."""
    row = db.CedhCandidatesRepo(_db_path()).get(candidate_id)
    if row is None:
        abort(404)
    if not _can_access(row.get("owner_id")):
        abort(403)
    return row


@bp.route("/candidate/<candidate_id>")
def candidate(candidate_id: str):
    """Render one candidate with its evidence and simulator provenance."""
    row = _load_owned(candidate_id)
    document = json.loads(row["candidate_json"])
    simulation = (
        json.loads(row["simulation_json"]) if row.get("simulation_json") else None
    )
    explanation = (
        json.loads(row["explanation_json"]) if row.get("explanation_json") else None
    )
    warnings = json.loads(row.get("warnings_json") or "[]")

    by_role: dict[str, list[dict]] = {}
    for card in document.get("cards", []):
        by_role.setdefault(card["role"], []).append(card)
    for cards in by_role.values():
        cards.sort(key=lambda c: c["name"])

    return render_template(
        "cedh/candidate.html",
        row=row,
        doc=document,
        by_role=dict(sorted(by_role.items())),
        simulation=simulation,
        explanation=explanation,
        warnings=warnings,
    )


@bp.route("/candidate/<candidate_id>.json")
def candidate_json(candidate_id: str):
    """Download the ``cedh-deck-candidate.v1`` document."""
    row = _load_owned(candidate_id)
    return Response(
        row["candidate_json"],
        mimetype="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{candidate_id}.candidate.json"'
            )
        },
    )
