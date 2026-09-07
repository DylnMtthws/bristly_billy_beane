"""Editable Deck Lab documents use isolated, owner-scoped transactions."""

import json

import pytest

from sabermetrics import db
from sabermetrics.deck_documents import (
    DeckDocumentRepo,
    DeckNotFound,
    InvalidCommand,
    RevisionConflict,
)
from scripts.setup_db import setup_database


@pytest.fixture
def deck_db(tmp_path):
    path = tmp_path / "documents.db"
    setup_database(path)
    owner = db.UsersRepo(path).create(
        email="owner@example.test", display_name="Owner", status="active"
    )
    other = db.UsersRepo(path).create(
        email="other@example.test", display_name="Other", status="active"
    )
    with db.connect(path) as conn:
        cards = [
            (
                "commander",
                "oracle-commander",
                "Kinnan Test",
                "{G}{U}",
                2,
                "Legendary Creature — Human Druid",
                "Mana ability text",
                '["G","U"]',
                1,
                1,
            ),
            (
                "ring",
                "oracle-ring",
                "Sol Ring",
                "{1}",
                1,
                "Artifact",
                "Add mana",
                "[]",
                0,
                1,
            ),
            (
                "island",
                "oracle-island",
                "Island",
                "",
                0,
                "Basic Land — Island",
                "{T}: Add {U}",
                '["U"]',
                0,
                1,
            ),
            (
                "partner",
                "oracle-partner",
                "Partner Test",
                "{U}",
                1,
                "Legendary Creature — Human",
                "Partner",
                '["U"]',
                1,
                1,
            ),
            (
                "partner-two",
                "oracle-partner-two",
                "Second Partner Test",
                "{G}",
                1,
                "Legendary Creature — Elf",
                "Partner",
                '["G"]',
                1,
                1,
            ),
            (
                "dwarves",
                "oracle-dwarves",
                "Seven Dwarves",
                "{1}{R}",
                2,
                "Creature — Dwarf",
                "A deck can have up to seven cards named Seven Dwarves.",
                '["G"]',
                0,
                1,
            ),
        ]
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,color_identity,
             is_legal_commander,is_legal_in_99) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            cards,
        )
        conn.commit()
    return path, owner, other


def test_create_edit_and_idempotent_retry(deck_db):
    path, owner, _ = deck_db
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner, title="Kinnan", commander_card_id="commander")
    initial = repo.get(owner, deck_id)
    assert initial["preferences"]["density"] == "compact"
    unsorted = initial["zones"][0]
    packet = [
        {"type": "create_zone", "name": "Fast Mana", "zone_id": "fast"},
        {"type": "add_card", "card_id": "ring", "zone_id": unsorted["id"]},
    ]
    changed = repo.apply_commands(
        owner, deck_id, expected_revision=0, mutation_id="mutation-1", commands=packet
    )
    assert changed["revision"] == 1
    assert changed["validation"]["commander_count"] == 1
    assert changed["validation"]["library_count"] == 1
    retried = repo.apply_commands(
        owner, deck_id, expected_revision=0, mutation_id="mutation-1", commands=packet
    )
    assert retried["revision"] == 1
    assert [entry["name"] for entry in retried["entries"]].count("Sol Ring") == 1


def test_playmat_dimensions_are_persisted_and_bounded(deck_db):
    path, owner, _ = deck_db
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner)
    initial = repo.get(owner, deck_id)["presentation"]
    assert initial["canvas_width"] == 1600
    assert initial["canvas_height"] == 900
    document = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=0,
        mutation_id="expand-playmat",
        commands=[
            {
                "type": "update_presentation",
                "canvas_width": 9999,
                "canvas_height": 9999,
                "surface": "night-ritual",
            },
            {
                "type": "move_zone",
                "zone_id": repo.get(owner, deck_id)["zones"][0]["id"],
                "x": 9999,
                "y": 9999,
            },
        ],
    )
    assert document["presentation"]["canvas_width"] == 2400
    assert document["presentation"]["canvas_height"] == 1800
    assert document["presentation"]["surface"] == "night-ritual"
    assert document["zones"][0]["x"] == 2220
    assert document["zones"][0]["y"] == 1700


def test_document_hydrates_artwork_added_to_the_card_catalog(deck_db):
    path, owner, _ = deck_db
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner, commander_card_id="commander")
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE cards SET image_uri=? WHERE id='commander'",
            ("https://images.example.test/commander.jpg",),
        )
        conn.commit()
    commander = repo.get(owner, deck_id)["entries"][0]
    assert commander["image_uri"] == "https://images.example.test/commander.jpg"


def test_serial_quantity_adjustments_do_not_drop_fast_clicks(deck_db):
    path, owner, _ = deck_db
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner)
    unsorted = repo.get(owner, deck_id)["zones"][0]
    document = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=0,
        mutation_id="add-ring",
        commands=[{"type": "add_card", "card_id": "ring", "zone_id": unsorted["id"]}],
    )
    entry_id = document["entries"][0]["id"]
    for revision in (1, 2):
        document = repo.apply_commands(
            owner,
            deck_id,
            expected_revision=revision,
            mutation_id=f"increment-{revision}",
            commands=[{"type": "adjust_quantity", "entry_id": entry_id, "delta": 1}],
        )
    assert document["entries"][0]["quantity"] == 3


def test_card_roles_can_be_updated_from_the_decklist(deck_db):
    path, owner, _ = deck_db
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner)
    unsorted = repo.get(owner, deck_id)["zones"][0]
    document = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=0,
        mutation_id="add-role-card",
        commands=[{"type": "add_card", "card_id": "ring", "zone_id": unsorted["id"]}],
    )
    entry_id = document["entries"][0]["id"]
    changed = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=1,
        mutation_id="set-card-role",
        commands=[{"type": "set_role", "entry_id": entry_id, "role": "ramp"}],
    )
    assert changed["entries"][0]["role"] == "ramp"
    with pytest.raises(InvalidCommand):
        repo.apply_commands(
            owner,
            deck_id,
            expected_revision=2,
            mutation_id="invalid-card-role",
            commands=[{"type": "set_role", "entry_id": entry_id, "role": "anything"}],
        )


def test_revision_and_owner_are_enforced(deck_db):
    path, owner, other = deck_db
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner)
    with pytest.raises(DeckNotFound):
        repo.get(other, deck_id)
    with pytest.raises(RevisionConflict) as conflict:
        repo.apply_commands(
            owner,
            deck_id,
            expected_revision=9,
            mutation_id="stale",
            commands=[{"type": "rename_deck", "title": "Nope"}],
        )
    assert conflict.value.current_revision == 0


def test_library_stats_are_stable_across_filtered_views(deck_db):
    path, owner, _ = deck_db
    repo = DeckDocumentRepo(path)
    recent_id = repo.create(owner, title="Recent")
    old_id = repo.create(owner, title="Old")
    repo.apply_commands(
        owner,
        recent_id,
        expected_revision=0,
        mutation_id="favorite-recent",
        commands=[{"type": "toggle_favorite"}],
    )
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE deck_documents SET updated_at=datetime('now','-2 days') WHERE id=?",
            (recent_id,),
        )
        conn.execute(
            "UPDATE deck_documents SET updated_at='2020-01-01T00:00:00' WHERE id=?",
            (old_id,),
        )
        conn.commit()

    assert repo.library_stats(owner) == {
        "total_count": 2,
        "favorite_count": 1,
        "recent_count": 1,
        "edited_week_count": 1,
    }
    assert len(repo.list_for_owner(owner, favorite=True)) == 1
    assert repo.library_stats(owner)["total_count"] == 2


def test_deck_tags_share_a_canonical_global_vocabulary(deck_db):
    path, owner, other = deck_db
    repo = DeckDocumentRepo(path)
    first = repo.create(owner, title="First", commander_card_id="commander")
    second = repo.create(other, title="Second")

    first_document = repo.apply_commands(
        owner,
        first,
        expected_revision=0,
        mutation_id="tag-first",
        commands=[{"type": "add_tag", "name": " Turbo "}],
    )
    second_document = repo.apply_commands(
        other,
        second,
        expected_revision=0,
        mutation_id="tag-second",
        commands=[{"type": "add_tag", "name": "turbo"}],
    )

    assert first_document["tags"] == second_document["tags"]
    assert first_document["tags"][0]["name"] == "Turbo"
    assert repo.search_tags("TUR")[0] == {
        "id": "tag-turbo",
        "name": "Turbo",
        "usage_count": 2,
    }
    library_item = repo.list_for_owner(owner)[0]
    assert library_item["color_identity"] == ["U", "G"]
    assert library_item["tags"] == first_document["tags"]

    removed = repo.apply_commands(
        owner,
        first,
        expected_revision=1,
        mutation_id="untag-first",
        commands=[{"type": "remove_tag", "tag_id": "tag-turbo"}],
    )
    assert removed["tags"] == []
    assert repo.search_tags("turbo")[0]["usage_count"] == 1

    with pytest.raises(InvalidCommand, match="letters, numbers"):
        repo.apply_commands(
            owner,
            first,
            expected_revision=2,
            mutation_id="invalid-tag",
            commands=[{"type": "add_tag", "name": "bad <tag>"}],
        )


def test_validation_supports_partner_pairs_and_quantity_exceptions(deck_db):
    path, owner, _ = deck_db
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner, commander_card_id="partner")
    document = repo.get(owner, deck_id)
    unsorted = document["zones"][0]
    document = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=0,
        mutation_id="partner-and-exception",
        commands=[
            {"type": "add_card", "card_id": "partner-two", "is_commander": True},
            {
                "type": "add_card",
                "card_id": "dwarves",
                "zone_id": unsorted["id"],
                "quantity": 7,
            },
        ],
    )
    assert document["validation"]["commander_count"] == 2
    assert not any(
        "recognized legal pair" in issue or "singleton" in issue
        for issue in document["validation"]["issues"]
    )


def test_deleting_zone_moves_cards_to_unsorted(deck_db):
    path, owner, _ = deck_db
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner)
    repo.apply_commands(
        owner,
        deck_id,
        expected_revision=0,
        mutation_id="build-zone",
        commands=[
            {"type": "create_zone", "name": "Fast Mana", "zone_id": "fast"},
            {"type": "add_card", "card_id": "ring", "zone_id": "fast"},
        ],
    )
    changed = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=1,
        mutation_id="delete-zone",
        commands=[{"type": "delete_zone", "zone_id": "fast"}],
    )
    unsorted = next(zone for zone in changed["zones"] if zone["name"] == "Unsorted")
    ring = next(entry for entry in changed["entries"] if entry["name"] == "Sol Ring")
    assert ring["zone_id"] == unsorted["id"]
    with pytest.raises(InvalidCommand):
        repo.apply_commands(
            owner,
            deck_id,
            expected_revision=2,
            mutation_id="delete-unsorted",
            commands=[{"type": "delete_zone", "zone_id": unsorted["id"]}],
        )


def test_generated_import_is_non_destructive_and_unique(deck_db):
    path, owner, _ = deck_db
    with db.connect(path) as conn:
        conn.execute(
            """INSERT INTO generated_decks
            (id,commander_id,owner_id,deck_name,cards_json,generated_at) VALUES(?,?,?,?,?,?)""",
            (
                "generated-1",
                "commander",
                owner,
                "Generated Kinnan",
                json.dumps(
                    [
                        {"card_id": "ring", "name": "Sol Ring", "slot_role": "ramp"},
                        {"card_id": "ring", "name": "Sol Ring", "slot_role": "ramp"},
                    ]
                ),
                "2025-01-02 03:04:05",
            ),
        )
        conn.execute(
            "INSERT INTO deck_feedback(id,user_id,deck_id,verdict,comment) VALUES(?,?,?,?,?)",
            ("feedback-1", owner, "generated-1", "up", "Keep this source feedback"),
        )
        conn.commit()
    repo = DeckDocumentRepo(path)
    first = repo.import_generated(owner, "generated-1")
    assert repo.import_generated(owner, "generated-1") == first
    document = repo.get(owner, first)
    assert document["created_at"] == "2025-01-02 03:04:05"
    assert (
        next(e for e in document["entries"] if e["name"] == "Sol Ring")["quantity"] == 2
    )
    with db.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM generated_decks").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM deck_feedback").fetchone()[0] == 1


def test_document_migration_is_repeatable_and_preserves_data(deck_db):
    path, owner, _ = deck_db
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner, title="Before rerun")
    with db.connect(path) as conn:
        before = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("users", "cards", "deck_documents", "deck_zones")
        }
    setup_database(path)
    setup_database(path)
    with db.connect(path) as conn:
        after = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("users", "cards", "deck_documents", "deck_zones")
        }
    assert after == before
    assert repo.get(owner, deck_id)["title"] == "Before rerun"


def test_share_is_read_only_and_revocable(deck_db):
    path, owner, _ = deck_db
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner, title="Shared")
    token = repo.create_share(owner, deck_id)
    assert repo.get_shared(token)["title"] == "Shared"
    repo.revoke_shares(owner, deck_id)
    with pytest.raises(DeckNotFound):
        repo.get_shared(token)


def test_delete_is_owner_scoped_and_cascades_deck_data(deck_db):
    path, owner, other = deck_db
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner, title="Delete me", commander_card_id="commander")
    document = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=0,
        mutation_id="tag-before-delete",
        commands=[{"type": "add_tag", "name": "Midrange"}],
    )
    token = repo.create_share(owner, deck_id)
    with db.connect(path) as conn:
        conn.execute(
            "UPDATE deck_presentations SET custom_surface_path=? WHERE deck_id=?",
            ("/tmp/deck-lab-test-playmat.png", deck_id),
        )
        conn.commit()

    with pytest.raises(DeckNotFound):
        repo.delete(other, deck_id)
    assert repo.get(owner, deck_id)["revision"] == document["revision"]

    assert repo.delete(owner, deck_id) == "/tmp/deck-lab-test-playmat.png"
    with pytest.raises(DeckNotFound):
        repo.get(owner, deck_id)
    with pytest.raises(DeckNotFound):
        repo.get_shared(token)
    with db.connect(path) as conn:
        for table in (
            "deck_entries",
            "deck_zones",
            "deck_tag_assignments",
            "deck_share_grants",
        ):
            assert (
                conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE deck_id=?", (deck_id,)
                ).fetchone()[0]
                == 0
            )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM activity_events "
                "WHERE subject_id=? AND action='deck.deleted'",
                (deck_id,),
            ).fetchone()[0]
            == 1
        )


def test_export_is_deterministic(deck_db):
    path, owner, _ = deck_db
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner, title="List", commander_card_id="commander")
    unsorted = repo.get(owner, deck_id)["zones"][0]
    document = repo.apply_commands(
        owner,
        deck_id,
        expected_revision=0,
        mutation_id="add",
        commands=[{"type": "add_card", "card_id": "ring", "zone_id": unsorted["id"]}],
    )
    assert (
        repo.export_text(document)
        == "// List\n\nCommander\n1 Kinnan Test\n\nUnsorted\n1 Sol Ring\n"
    )
