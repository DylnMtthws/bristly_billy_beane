"""Independent edge-case checks for the remaining-issues review."""

import pytest

from sabermetrics import db
from sabermetrics.deck_documents import DeckDocumentRepo, DeckNotFound
from sabermetrics.research import ResearchRepo
from scripts.setup_db import setup_database


@pytest.fixture
def corpus(tmp_path):
    path = tmp_path / "review.db"
    setup_database(path)
    rows = [
        ("zero", "Zero", "0", "Creature — Human", '["U"]', 1, 0),
        ("large", "Large", "100", "Creature — Giant", "[]", 1, 0),
        ("star", "Star", "*", "Creature — Human", "[]", 1, 0),
        ("formula", "Formula", "1+*", "Creature — Human", "[]", 1, 0),
        ("negative", "Negative", "-1", "Creature — Human", "[]", 1, 0),
        ("fraction", "Fraction", "0.5", "Creature — Human", "[]", 1, 0),
        (
            "commander",
            "Alphabetical Commander",
            "2",
            "Legendary Creature — Human",
            '["U"]',
            1,
            1,
        ),
        ("banned", "Banned Commander", "5", "Legendary Creature — Human", "[]", 0, 1),
    ]
    with db.connect(path) as conn:
        conn.executemany(
            "INSERT INTO cards(id,oracle_id,name,power,toughness,type_line,color_identity,"
            "is_legal_in_99,is_legal_commander,cmc,rarity) VALUES(?,?,?,?,?,?,?,?,?,2,'rare')",
            [
                (i, "oracle-" + i, n, p, p, t, c, legal, cmd)
                for i, n, p, t, c, legal, cmd in rows
            ],
        )
        conn.commit()
    return path


def ids(data):
    return {row["id"] for row in data["results"]}


def test_commander_eligibility_uses_front_characteristics_and_explicit_exceptions():
    from sabermetrics.card_discovery import imported_commander_eligible as eligible

    assert eligible("Legendary Creature — Human", "")
    assert not eligible("Legendary Planeswalker — Test", "")
    assert eligible("Legendary Planeswalker — Test", "Test can be your commander.")
    assert not eligible("Enchantment // Legendary Creature — Test", "")
    assert eligible("Legendary Artifact — Vehicle", "", "8", "8")
    assert eligible("Legendary Artifact — Spacecraft", "", "10", "10")
    assert not eligible("Legendary Artifact — Spacecraft", "")
    assert eligible("Legendary Enchantment — Background", "")


def test_stat_ranges_distinguish_large_unknown_and_negative(corpus):
    repo = ResearchRepo(corpus)
    assert ids(repo.cards("", power_min_bound=10, power_max_bound=10)) == {"large"}
    assert ids(repo.cards("", power_min_bound=0, power_max_bound=0)) == {"zero"}
    assert ids(repo.cards("", power_min_bound=0, power_max_bound=1)) == {
        "zero",
        "fraction",
    }
    assert {"star", "formula", "negative"} <= ids(
        repo.cards("", power_min_bound=0, power_max_bound=10)
    )


def test_subtypes_do_not_match_card_types_and_repeated_colors_are_idempotent(corpus):
    repo = ResearchRepo(corpus)
    assert ids(repo.cards("", sub_type="Creature")) == set()
    assert "zero" in ids(repo.cards("", sub_type="Human"))
    assert ids(repo.cards("", colors=["U", "U"], color_mode="exactly")) == ids(
        repo.cards("", colors=["U"], color_mode="exactly")
    )


def test_catalog_includes_unplayed_legal_commander_and_excludes_banned(corpus):
    repo = ResearchRepo(corpus)
    assert "banned" not in ids(repo.cards(""))
    assert ids(repo.commander_catalog()) == {"commander"}


def test_visibility_is_owner_only_and_public_projection_is_revocable(corpus):
    users = db.UsersRepo(corpus)
    owner = users.create(
        email="owner@example.test", display_name="Public display", status="active"
    )
    other = users.create(
        email="other@example.test", display_name="Other", status="active"
    )
    repo = DeckDocumentRepo(corpus)
    deck = repo.create(
        owner, title="Public candidate", commander_card_ids=["commander"]
    )
    assert repo.list_public()["results"] == []
    with pytest.raises(DeckNotFound):
        repo.get_public(deck)
    with pytest.raises(DeckNotFound):
        repo.set_visibility(other, deck, "public")
    repo.set_visibility(owner, deck, "public")
    projected = repo.get_public(deck)
    assert projected["display_name"] == "Public display"
    assert (
        not {"owner_id", "email", "notes", "events", "mutations", "share_token"}
        & projected.keys()
    )
    assert ids(repo.list_public()) == {deck}
    repo.set_visibility(owner, deck, "private")
    assert repo.list_public()["results"] == []
    with pytest.raises(DeckNotFound):
        repo.get_public(deck)
