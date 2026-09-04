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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
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
_BUILD_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cedh-build")


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


def _wants_json() -> bool:
    """Return whether this request explicitly asks for JSON."""
    return bool(
        request.is_json
        or request.accept_mimetypes.best_match(["text/html", "application/json"])
        == "application/json"
    )


def _persist_result(db_path: Path, owner_id: str, result: Any) -> str:
    """Persist a successful lab result and return its candidate id."""
    candidate = result.candidate
    if candidate is None:  # guarded by the worker; keeps this boundary honest
        raise ValueError("cannot persist a lab result without a candidate")
    db.CedhCandidatesRepo(db_path).save(
        candidate_id=candidate.candidate_id,
        owner_id=owner_id,
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
    return str(candidate.candidate_id)


def _execute_build_job(db_path: Path, job_id: str, owner_id: str) -> None:
    """Run and persist one queued build; never leak an exception to the pool."""
    jobs = db.BuildJobsRepo(db_path)
    try:
        job = jobs.get(job_id)
        if job is None:
            return
        jobs.set_status(job_id, "running")
        lab_request = LabRequest.model_validate_json(job["request_json"])
        lab, modes = build_default_lab(db_path=str(db_path), user_id=owner_id)
        jobs.set_status(job_id, "simulating")
        result = lab.run(lab_request)
        if result.candidate is None:
            jobs.set_status(
                job_id,
                "failed",
                error_code="unsupported",
                error_detail=(
                    result.unsupported_detail
                    or "No supported strategy pack matches that request."
                ),
            )
            return

        jobs.set_status(job_id, "explaining")
        candidate_id = _persist_result(db_path, owner_id, result)
        jobs.set_status(job_id, "done", candidate_id=candidate_id)
        logger.info(
            "cEDH build job %s completed as candidate %s for %s "
            "(pack=%s, modes=%s, cost=$%.4f)",
            job_id,
            candidate_id,
            owner_id,
            result.pack_id,
            modes,
            result.total_cost_usd,
        )
    except Exception as exc:  # the persisted error is the async response boundary
        logger.exception("cEDH build job %s failed", job_id)
        jobs.set_status(
            job_id,
            "failed",
            error_code="build_failed",
            error_detail=str(exc) or exc.__class__.__name__,
        )


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
    """Validate a request, queue the lab run, and return its job resource."""
    db_path = _db_path()
    settings = load_cedh_settings()

    from sabermetrics.config import settings as legacy_settings

    if _monthly_spend(db_path) >= legacy_settings.llm.monthly_cost_ceiling_usd:
        if _wants_json():
            return jsonify(error="cost_ceiling_reached"), 503
        flash(
            "The lab is paused: the monthly cost ceiling has been reached.",
            "error",
        )
        return redirect(url_for("cedh.index"))

    repo = db.CedhCandidatesRepo(db_path)
    used = repo.count_this_month(current_user.id)
    quota = current_user.monthly_deck_quota
    if used >= quota:
        if _wants_json():
            return jsonify(error="quota_exhausted", used=used, quota=quota), 429
        flash(
            f"Monthly limit reached ({used}/{quota} builds).",
            "error",
        )
        return redirect(url_for("cedh.index"))

    values = request.get_json(silent=True) if request.is_json else request.form
    values = values or {}
    pack_id = str(values.get("pack_id") or "").strip()
    raw_intent = str(values.get("raw_intent") or "").strip()

    try:
        flex_slots = max(0, min(20, int(values.get("flex_slots") or 0)))
        constraints = BuildConstraints(
            flex_slots=flex_slots,
            metagame=MetagameWindow(
                days=int(values.get("window_days") or settings.meta.window_days),
                min_event_size=int(
                    values.get("min_event_size") or settings.meta.min_event_size
                ),
            ),
        )
    except (ValueError, TypeError) as exc:
        if _wants_json():
            return jsonify(error="invalid_request", detail=str(exc)), 400
        flash(f"Those constraints are not valid: {exc}", "error")
        return redirect(url_for("cedh.index"))

    lab_request = LabRequest(
        raw_intent=raw_intent,
        pack_id=pack_id or None,
        constraints=constraints,
    )
    jobs = db.BuildJobsRepo(db_path)
    job_id = jobs.create(
        user_id=current_user.id, request_json=lab_request.model_dump_json()
    )
    try:
        _BUILD_EXECUTOR.submit(_execute_build_job, db_path, job_id, current_user.id)
    except RuntimeError as exc:
        jobs.set_status(
            job_id,
            "failed",
            error_code="enqueue_failed",
            error_detail=str(exc),
        )
        if _wants_json():
            return jsonify(error="enqueue_failed", job_id=job_id), 503
        flash("The build worker is unavailable. Please try again.", "error")
        return redirect(url_for("cedh.job_status", job_id=job_id))

    status_url = url_for("cedh.job_status", job_id=job_id)
    if _wants_json():
        return (
            jsonify(job_id=job_id, status="queued", status_url=status_url),
            202,
            {"Location": status_url},
        )
    return redirect(status_url, code=303)


def _load_owned_job(job_id: str) -> dict:
    """Fetch a build job the current user may inspect, or abort."""
    row = db.BuildJobsRepo(_db_path()).get(job_id)
    if row is None:
        abort(404)
    if not _can_access(row.get("user_id")):
        abort(403)
    return row


def _job_payload(row: dict) -> dict:
    """Return the public representation without echoing private input."""
    payload = {
        key: row.get(key)
        for key in (
            "id",
            "status",
            "candidate_id",
            "error_code",
            "error_detail",
            "created_at",
            "started_at",
            "finished_at",
        )
    }
    if row.get("candidate_id"):
        payload["candidate_url"] = url_for(
            "cedh.candidate", candidate_id=row["candidate_id"]
        )
    return payload


@bp.route("/build/<job_id>")
def job_status(job_id: str):
    """Render or return one owner-scoped build job."""
    row = _load_owned_job(job_id)
    if _wants_json():
        return jsonify(_job_payload(row))
    if row["status"] == "done" and row.get("candidate_id"):
        return redirect(url_for("cedh.candidate", candidate_id=row["candidate_id"]))
    return render_template("cedh/job.html", job=row)


@bp.route("/build/<job_id>.json")
def job_status_json(job_id: str):
    """Return one owner-scoped build job as JSON."""
    return jsonify(_job_payload(_load_owned_job(job_id)))


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
    """Download the ``decklab-deck-candidate.v1`` document."""
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
