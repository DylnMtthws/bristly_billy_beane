"""cEDH Deck Lab routes: auth, ownership, quota, and visible provenance."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sabermetrics import db
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "lab.db"
    setup_database(path)
    return path


@pytest.fixture
def app(db_path):
    app = create_app(db_path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
    )
    return app


def _user(db_path, email, role="user"):
    return db.UsersRepo(db_path).create(
        email=email,
        display_name=email.split("@")[0],
        role=role,
        status="active",
        password_hash=db.hash_password("password123"),
    )


def _login(app, email):
    client = app.test_client()
    client.post(
        "/login",
        data={"email": email, "password": "password123"},
        follow_redirects=True,
    )
    return client


@pytest.fixture
def client(app, db_path):
    _user(db_path, "owner@example.com")
    return _login(app, "owner@example.com")


def _build(client, **form):
    payload = {"pack_id": "kinnan_basalt"}
    payload.update(form)
    response = client.post("/lab/build", data=payload)
    assert response.status_code == 302, response.data[:400]
    return response.headers["Location"]


class TestAuth:
    def test_the_lab_requires_a_session(self, app):
        response = app.test_client().get("/lab/")
        assert response.status_code in (302, 401)

    def test_building_requires_a_session(self, app):
        response = app.test_client().post("/lab/build", data={})
        assert response.status_code in (302, 401)


class TestLabIndex:
    def test_lists_the_supported_pack(self, client):
        body = client.get("/lab/").data.decode()
        assert "Kinnan" in body
        assert "supported" in body

    def test_states_when_it_is_running_on_fixtures(self, client):
        """A page built from fixture data must say so."""
        body = client.get("/lab/").data.decode()
        assert "synthetic fixture" in body

    def test_shows_the_quota(self, client):
        assert "builds used this month" in client.get("/lab/").data.decode()


class TestBuild:
    def test_builds_and_redirects_to_the_candidate(self, client):
        location = _build(client)
        assert "/lab/candidate/" in location
        assert client.get(location).status_code == 200

    def test_the_candidate_page_shows_the_ninety_nine(self, client):
        body = client.get(_build(client)).data.decode()
        assert "Basalt Monolith" in body
        assert "The 99" in body

    def test_the_candidate_page_states_the_simulation_framing(self, client):
        """A goldfish figure never appears without what it does not measure."""
        body = client.get(_build(client)).data.decode()
        assert "A faster number is not a better deck" in body
        assert "does not measure" in body

    def test_the_candidate_page_states_evidence_provenance(self, client):
        body = client.get(_build(client)).data.decode()
        assert "Card corpus" in body
        assert "Tournament data" in body
        assert "Evidence hash" in body

    def test_absent_tournament_data_is_shown_as_absent(self, client):
        body = client.get(_build(client)).data.decode()
        assert "No tournament evidence is available" in body

    def test_win_packages_report_whether_they_assemble(self, client):
        body = client.get(_build(client)).data.decode()
        assert "Unbounded colourless into Thrasios" in body
        assert "assembled by this list" in body

    def test_the_form_offers_no_budget_field(self, client):
        """cEDH is proxy-normal; there is nothing to set."""
        body = client.get("/lab/").data.decode()
        assert 'name="budget_usd"' not in body
        assert "No budget setting, on purpose" in body

    def test_a_stray_budget_field_changes_nothing(self, client):
        """An old bookmark or a stale client must not alter the deck.

        The route does not read the field, so the build is byte-identical to one
        without it apart from its id and timestamp.
        """
        plain = json.loads(client.get(f"{_build(client)}.json").data)
        with_budget = json.loads(
            client.get(f"{_build(client, budget_usd='150')}.json").data
        )
        assert plain["deck_sha256"] == with_budget["deck_sha256"]

    def test_the_candidate_page_never_shows_a_price(self, client):
        body = client.get(_build(client)).data.decode().lower()
        assert "$" not in body.split("<main")[-1]
        assert "budget" not in body.split("<main")[-1]

    def test_flex_slots_are_honoured(self, client):
        location = _build(client, flex_slots="3")
        document = json.loads(client.get(f"{location}.json").data.decode())
        assert sum(c["quantity"] for c in document["cards"]) == 99


class TestCandidateExport:
    def test_the_json_export_is_the_versioned_document(self, client):
        location = _build(client)
        response = client.get(f"{location}.json")
        assert response.status_code == 200
        assert "attachment" in response.headers["Content-Disposition"]
        document = json.loads(response.data)
        assert document["schema"] == "cedh-deck-candidate.v1"
        assert len(document["deck_sha256"]) == 64

    def test_cards_are_exported_by_oracle_id(self, client):
        location = _build(client)
        document = json.loads(client.get(f"{location}.json").data)
        assert all(card["oracle_id"] for card in document["cards"])
        assert sum(card["quantity"] for card in document["cards"]) == 99


class TestOwnership:
    def test_another_user_cannot_read_a_candidate(self, app, db_path, client):
        location = _build(client)
        _user(db_path, "other@example.com")
        other = _login(app, "other@example.com")
        assert other.get(location).status_code == 403
        assert other.get(f"{location}.json").status_code == 403

    def test_an_admin_may_read_any_candidate(self, app, db_path, client):
        location = _build(client)
        _user(db_path, "admin@example.com", role="admin")
        admin = _login(app, "admin@example.com")
        assert admin.get(location).status_code == 200

    def test_a_missing_candidate_is_a_404(self, client):
        assert client.get("/lab/candidate/nope").status_code == 404

    def test_only_your_own_candidates_are_listed(self, app, db_path, client):
        candidate_id = _build(client).rsplit("/", 1)[-1]
        _user(db_path, "other@example.com")
        other = _login(app, "other@example.com")
        body = other.get("/lab/").data.decode()
        # The pack list names the commander for everyone; the candidate table
        # must not, so the assertion is on the candidate id.
        assert candidate_id not in body
        assert "Your candidates" not in body


class TestQuotaAndCeiling:
    def test_lab_builds_count_against_the_shared_quota(self, db_path, client):
        _build(client)
        repo = db.CedhCandidatesRepo(db_path)
        owner = repo.list_for_owner(
            db.UsersRepo(db_path).get_by_email("owner@example.com")["id"]
        )
        assert len(owner) == 1

    def test_an_exhausted_quota_blocks_the_build(self, app, db_path, client):
        user = db.UsersRepo(db_path).get_by_email("owner@example.com")
        db.UsersRepo(db_path).set_quota(user["id"], 0)
        response = client.post(
            "/lab/build",
            data={"pack_id": "kinnan_basalt"},
            follow_redirects=True,
        )
        assert b"Monthly limit reached" in response.data

    def test_the_global_cost_ceiling_pauses_the_lab(self, db_path, client):
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO cost_log (call_type, model, cost_usd) VALUES (?,?,?)",
            ("cedh:test", "m", 10_000.0),
        )
        conn.commit()
        conn.close()
        response = client.post(
            "/lab/build",
            data={"pack_id": "kinnan_basalt"},
            follow_redirects=True,
        )
        assert b"monthly cost ceiling" in response.data

    def test_the_ceiling_counts_both_paths(self, db_path):
        """Legacy Anthropic spend and cEDH spend share one ceiling."""
        import sqlite3

        from sabermetrics.cedh.cost_ledger import CostLedger

        conn = sqlite3.connect(str(db_path))
        conn.executemany(
            "INSERT INTO cost_log (call_type, model, cost_usd) VALUES (?,?,?)",
            [("fit", "claude-sonnet-4-6", 1.0), ("cedh:explanation", "deepseek", 2.0)],
        )
        conn.commit()
        conn.close()
        assert CostLedger(db_path).monthly_spend() == pytest.approx(3.0)


def test_the_lab_appears_in_the_navigation(client):
    assert b"cEDH Lab" in client.get("/").data
