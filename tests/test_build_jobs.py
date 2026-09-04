"""W5 asynchronous cEDH build lifecycle, migration, quota, and ownership."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sabermetrics import db
from sabermetrics.ui import cedh_routes
from sabermetrics.ui.app import create_app
from scripts.migrate_build_jobs import migrate
from scripts.setup_db import setup_database


class ImmediateExecutor:
    """Run submitted work inline so job state is deterministic in tests."""

    def submit(self, function, *args):
        function(*args)
        return object()


class HoldingExecutor:
    """Accept work without starting it."""

    def submit(self, function, *args):
        self.call = (function, args)
        return object()


class BrokenExecutor:
    def submit(self, function, *args):
        raise RuntimeError("pool is shut down")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "jobs.db"
    setup_database(path)
    return path


@pytest.fixture
def app(db_path, monkeypatch):
    monkeypatch.setattr(cedh_routes, "_BUILD_EXECUTOR", ImmediateExecutor())
    app = create_app(db_path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
    )
    return app


def _create_user(db_path: Path, email: str, *, role: str = "user") -> str:
    return db.UsersRepo(db_path).create(
        email=email,
        display_name=email.split("@")[0],
        role=role,
        status="active",
        password_hash=db.hash_password("password123"),
    )


def _client(app, db_path: Path, email: str, *, role: str = "user"):
    _create_user(db_path, email, role=role)
    client = app.test_client()
    response = client.post("/login", data={"email": email, "password": "password123"})
    assert response.status_code == 302
    return client


def _post_json(client, **overrides):
    payload = {"pack_id": "kinnan_basalt"}
    payload.update(overrides)
    return client.post("/lab/build", json=payload)


class TestMigration:
    def test_fresh_database_has_the_exact_job_columns(self, db_path):
        with sqlite3.connect(db_path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(build_jobs)")}
        assert columns == {
            "id",
            "user_id",
            "status",
            "request_json",
            "candidate_id",
            "error_code",
            "error_detail",
            "created_at",
            "started_at",
            "finished_at",
        }

    def test_migration_is_additive_idempotent_and_preserves_existing_data(
        self, tmp_path
    ):
        path = tmp_path / "existing.db"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE marker (value TEXT)")
            conn.execute("INSERT INTO marker VALUES ('keep me')")
        migrate(path)
        migrate(path)
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT value FROM marker").fetchone()[0] == "keep me"
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type='table' AND name='build_jobs'"
                ).fetchone()[0]
                == 1
            )


class TestLifecycle:
    def test_json_post_returns_202_job_and_success_runs_every_phase(
        self, app, db_path, monkeypatch
    ):
        statuses = []
        original = db.BuildJobsRepo.set_status

        def record(repo, job_id, status, **kwargs):
            statuses.append(status)
            return original(repo, job_id, status, **kwargs)

        monkeypatch.setattr(db.BuildJobsRepo, "set_status", record)
        client = _client(app, db_path, "owner@example.com")
        response = _post_json(client)
        assert response.status_code == 202
        assert response.headers["Location"] == response.json["status_url"]

        job = client.get(f"{response.json['status_url']}.json").json
        assert job["status"] == "done"
        assert job["candidate_url"].startswith("/lab/candidate/")
        assert job["started_at"] and job["finished_at"]
        assert statuses == ["running", "simulating", "explaining", "done"]

    def test_full_flow_uses_fixture_simulator_and_stub_model(
        self,
        app,
        db_path,
        monkeypatch,
        cedh_cards,
        cedh_meta_populated,
        cedh_simulator,
    ):
        from sabermetrics.cedh.factory import LabModes
        from sabermetrics.cedh.gateway_fixture import FixtureGateway
        from sabermetrics.cedh.lab import build_lab

        gateway = FixtureGateway(
            {
                "cedh:evidence_summary": {
                    "summary": "Kinnan appeared in the fixture.",
                    "events_cited": 5,
                    "decks_cited": 42,
                    "caveats": ["fixture evidence"],
                    "chunk_ids": ["meta-presence"],
                },
                "cedh:explanation": {
                    "game_plan": "Assemble Basalt Monolith under Kinnan.",
                    "primary_line": "Resolve and protect the engine.",
                    "weaknesses": ["interaction"],
                    "key_oracle_ids": [],
                    "mulligan_guidance": "Keep fast mana.",
                },
            }
        )
        lab = build_lab(
            cards=cedh_cards,
            meta=cedh_meta_populated,
            simulator=cedh_simulator,
            gateway=gateway,
        )

        def stub_factory(**kwargs):
            return lab, LabModes("fixture", "fixture", "fixture", "fixture")

        monkeypatch.setattr(cedh_routes, "build_default_lab", stub_factory)
        client = _client(app, db_path, "owner@example.com")
        response = _post_json(client)
        job = client.get(f"{response.json['status_url']}.json").json
        row = db.CedhCandidatesRepo(db_path).get(job["candidate_id"])
        assert job["status"] == "done"
        assert row["simulation_status"] == "simulated"
        assert row["explanation_json"] is not None
        assert {call.call_type for call in gateway.requests} == {
            "cedh:evidence_summary",
            "cedh:explanation",
        }

    def test_html_post_redirects_to_a_pollable_status_page(
        self, app, db_path, monkeypatch
    ):
        monkeypatch.setattr(cedh_routes, "_BUILD_EXECUTOR", HoldingExecutor())
        client = _client(app, db_path, "owner@example.com")
        response = client.post("/lab/build", data={"pack_id": "kinnan_basalt"})
        assert response.status_code == 303
        status = client.get(response.headers["Location"])
        assert status.status_code == 200
        assert b"Status:" in status.data and b"queued" in status.data

    def test_done_html_status_renders_the_candidate_page(self, app, db_path):
        client = _client(app, db_path, "owner@example.com")
        response = _post_json(client)
        status = client.get(response.json["status_url"], follow_redirects=True)
        assert status.status_code == 200
        assert b"The 99" in status.data

    def test_unsupported_request_fails_visibly_without_using_quota(self, app, db_path):
        client = _client(app, db_path, "owner@example.com")
        response = _post_json(client, pack_id="not-a-pack")
        job = client.get(f"{response.json['status_url']}.json").json
        assert job["status"] == "failed"
        assert job["error_code"] == "unsupported"
        owner = db.UsersRepo(db_path).get_by_email("owner@example.com")
        assert db.CedhCandidatesRepo(db_path).count_this_month(owner["id"]) == 0

    def test_worker_exception_is_persisted_and_does_not_use_quota(
        self, app, db_path, monkeypatch
    ):
        def explode(**kwargs):
            raise RuntimeError("factory failed")

        monkeypatch.setattr(cedh_routes, "build_default_lab", explode)
        client = _client(app, db_path, "owner@example.com")
        response = _post_json(client)
        job = client.get(f"{response.json['status_url']}.json").json
        assert job["status"] == "failed"
        assert job["error_code"] == "build_failed"
        assert job["error_detail"] == "factory failed"
        owner = db.UsersRepo(db_path).get_by_email("owner@example.com")
        assert db.CedhCandidatesRepo(db_path).count_this_month(owner["id"]) == 0

    def test_enqueue_failure_is_persisted_and_reported(self, app, db_path, monkeypatch):
        monkeypatch.setattr(cedh_routes, "_BUILD_EXECUTOR", BrokenExecutor())
        client = _client(app, db_path, "owner@example.com")
        response = _post_json(client)
        assert response.status_code == 503
        job = db.BuildJobsRepo(db_path).get(response.json["job_id"])
        assert job["status"] == "failed"
        assert job["error_code"] == "enqueue_failed"

    def test_queued_job_does_not_use_quota(self, app, db_path, monkeypatch):
        monkeypatch.setattr(cedh_routes, "_BUILD_EXECUTOR", HoldingExecutor())
        client = _client(app, db_path, "owner@example.com")
        assert _post_json(client).status_code == 202
        owner = db.UsersRepo(db_path).get_by_email("owner@example.com")
        assert db.CedhCandidatesRepo(db_path).count_this_month(owner["id"]) == 0

    @pytest.mark.parametrize(
        "status", ["queued", "running", "simulating", "explaining"]
    )
    def test_restart_fails_every_nonterminal_job(self, db_path, status):
        owner = _create_user(db_path, f"{status}@example.com")
        jobs = db.BuildJobsRepo(db_path)
        job_id = jobs.create(user_id=owner, request_json="{}")
        if status != "queued":
            jobs.set_status(job_id, status)

        create_app(db_path)

        job = jobs.get(job_id)
        assert job["status"] == "failed"
        assert job["error_code"] == "interrupted"
        assert job["finished_at"]


class TestErrorsAndOwnership:
    def test_invalid_request_is_400_json(self, app, db_path):
        client = _client(app, db_path, "owner@example.com")
        response = _post_json(client, window_days="not-an-integer")
        assert response.status_code == 400
        assert response.json["error"] == "invalid_request"

    def test_quota_rejection_is_429_json(self, app, db_path):
        client = _client(app, db_path, "owner@example.com")
        owner = db.UsersRepo(db_path).get_by_email("owner@example.com")
        db.UsersRepo(db_path).set_quota(owner["id"], 0)
        response = _post_json(client)
        assert response.status_code == 429
        assert response.json["error"] == "quota_exhausted"

    def test_another_user_cannot_see_job_html_or_json(self, app, db_path):
        owner = _client(app, db_path, "owner@example.com")
        response = _post_json(owner)
        status_url = response.json["status_url"]
        other = _client(app, db_path, "other@example.com")
        assert other.get(status_url).status_code == 403
        assert other.get(f"{status_url}.json").status_code == 403

    def test_admin_can_see_job_and_missing_job_is_404(self, app, db_path):
        owner = _client(app, db_path, "owner@example.com")
        response = _post_json(owner)
        admin = _client(app, db_path, "admin@example.com", role="admin")
        assert admin.get(f"{response.json['status_url']}.json").status_code == 200
        assert admin.get("/lab/build/missing.json").status_code == 404
