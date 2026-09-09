"""Library /build no longer surfaces retired strategy-pack leftovers."""

from __future__ import annotations

from sabermetrics import db
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True


def test_library_omits_strategy_pack_and_ready_to_edit_sections(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    path = tmp_path / "library-legacy.db"
    setup_database(path)
    user = db.UsersRepo(path).create(
        email="library@example.test",
        display_name="Library",
        role="user",
        status="active",
    )
    with db.connect(path) as conn:
        conn.execute("""INSERT INTO cards
            (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99,image_uri)
            VALUES('kinnan','oracle-kinnan','Kinnan Test','{G}{U}',2,
             'Legendary Creature — Human Druid','Mana text','["G","U"]',1,1,NULL)""")
        conn.execute(
            """INSERT INTO generated_decks
            (id, commander_id, owner_id, deck_name, cards_json)
            VALUES ('legacy-generated', 'kinnan', ?, 'Leftover Generated Deck', '[]')""",
            (user,),
        )
        conn.execute(
            """INSERT INTO build_jobs (id, user_id, status, request_json)
            VALUES ('job-legacy', ?, 'running', '{}')""",
            (user,),
        )
        conn.commit()
    db.CedhCandidatesRepo(path).save(
        candidate_id="cand-legacy",
        owner_id=user,
        pack_id="kinnan_basalt",
        commander_key="kinnan",
        commander_name="Leftover Candidate Commander",
        deck_sha256="sha256:" + ("a" * 64),
        candidate_json="{}",
        simulation_status="complete",
    )

    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    _login(client, user)

    created = client.post("/build/new", json={"title": "Editable draft"})
    assert created.status_code == 201

    html = client.get("/build").get_data(as_text=True)
    assert "Editable draft" in html
    assert "New deck" in html
    assert "Strategy pack builds" not in html
    assert "Ready to edit" not in html
    assert "Leftover Generated Deck" not in html
    assert "Leftover Candidate Commander" not in html
    assert "generated list" not in html
    assert "Open in builder" not in html
    assert "View progress" not in html
    assert "Review failure" not in html
    assert "/build/import/generated/" not in html
    assert "/build/import/candidate/" not in html
    assert "job-legacy" not in html
