"""Research metrics state their real cohorts and missing-data coverage."""

from datetime import date, timedelta

from sabermetrics import db
from sabermetrics.research import ResearchRepo
from scripts.setup_db import setup_database


def test_commander_metrics_and_inclusion_denominators(tmp_path):
    path = tmp_path / "research.db"
    setup_database(path)
    today = date.today()
    current = today.isoformat()
    prior = (today - timedelta(days=100)).isoformat()
    with db.connect(path) as conn:
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,color_identity,is_legal_commander,is_legal_in_99)
            VALUES(?,?,?,?,?,?,?,?)""",
            [
                ("kinnan", "ok", "Kinnan", 2, "Legendary Creature", '["G","U"]', 1, 1),
                ("tymna", "ot", "Tymna", 3, "Legendary Creature", '["W","B"]', 1, 1),
                ("ring", "or", "Sol Ring", 1, "Artifact", "[]", 0, 1),
            ],
        )
        conn.executemany(
            "INSERT INTO decks(id,source,source_id,commander_id) VALUES(?,?,?,?)",
            [
                ("d1", "test", "1", "kinnan"),
                ("d2", "test", "2", "kinnan"),
                ("d3", "test", "3", "tymna"),
                ("old", "test", "4", "kinnan"),
            ],
        )
        conn.execute(
            "INSERT INTO deck_cards(deck_id,card_id,quantity,is_commander) VALUES('d1','ring',1,0)"
        )
        conn.executemany(
            """INSERT INTO tournament_results
            (id,tournament_id,deck_id,commander_id,standing,tournament_date)
            VALUES(?,?,?,?,?,?)""",
            [
                ("r1", "event-1", "d1", "kinnan", 8, current),
                ("r2", "event-2", "d2", "kinnan", None, current),
                ("r3", "event-2", "d3", "tymna", 20, current),
                ("r4", "event-old", "old", "kinnan", 1, prior),
            ],
        )
        conn.commit()

    repo = ResearchRepo(path)
    data = repo.commanders(query="Kinnan", window_days=90)
    assert data["recorded_entries"] == 3
    row = data["results"][0]
    assert row["entries"] == 2
    assert row["meta_share"] == 2 / 3
    assert row["finish_coverage"] == 1
    assert row["top16_rate"] == 1.0

    all_time = repo.commanders(query="Kinnan", window_days=0)
    assert all_time["recorded_entries"] == 4
    assert all_time["window_days"] == 0
    assert all_time["results"][0]["entries"] == 3
    assert all_time["results"][0]["trend"] is None

    detail = repo.commander_detail("kinnan", window_days=90)
    assert detail is not None
    assert detail["inclusion_denominator"] == 1
    representative = detail["representative_list"]
    assert representative is not None
    assert representative["complete"] is False
    assert representative["groups"][0]["cards"][0]["name"] == "Sol Ring"
    assert representative["groups"][0]["cards"][0]["quantity"] == 1
    assert detail["inclusions"] == []
    assert detail["metrics"]["average_nonland_mv"] == 1.0
    assert detail["metrics"]["mv_list_count"] == 1

    filtered = repo.commanders(
        colors=["G", "U"], color_mode="exact", mana_max=2, meta_min=0.6
    )
    assert [item["name"] for item in filtered["results"]] == ["Kinnan"]
    assert repo.commanders(meta_min=0.7)["results"] == []

    cards = repo.cards(
        "",
        type_line="Artifact",
        mana_operator="eq",
        mana_value=1,
        color_mode="exact",
        colors=["C"],
    )
    assert [item["name"] for item in cards["results"]] == ["Sol Ring"]
