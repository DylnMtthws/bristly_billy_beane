"""Profile cache round-trips for real commanders (follow-up to criterion 4).

The finding in criterion 4 was that every real build pays for Sonnet profile
synthesis. Investigation showed the *cache mechanism is correct* — the reason
was simply that no real profile had ever been stored (the seed rows in
commander_profiles use synthetic UUIDs that match no card). This test locks in
the correct behavior: once a profile is stored under a real commander_id, the
next build hits the cache for free (no API call).
"""

import sqlite3
from pathlib import Path

import pytest

DB = Path("data/sabermetrics.db")


@pytest.mark.skipif(not DB.exists(), reason="needs card DB")
def test_profile_cache_round_trips_for_real_commander(build_db, canned_profile) -> None:
    from sabermetrics.reasoning.profiler import ProfileManager, ProfileRequest

    cid = sqlite3.connect(str(build_db)).execute(
        "SELECT id FROM cards WHERE is_legal_commander = 1 LIMIT 1"
    ).fetchone()[0]

    mgr = ProfileManager(build_db)
    # No real profile cached yet (the seed rows are orphaned fake UUIDs).
    assert mgr._get_cached_profile(cid, None) is None

    # Store a profile under the real commander_id.
    mgr._store_profile(canned_profile(cid, ["G"]).profile, None, None)

    # Now it hits — direct lookup and the full generate_profile path.
    got = mgr._get_cached_profile(cid, None)
    assert got is not None and got.commander_id == cid

    result = mgr.generate_profile(ProfileRequest(commander_id=cid))
    assert result.cache_hit is True
    assert result.generation_cost_usd == 0.0  # no API call on a hit

    # A different commander still misses (cache is keyed correctly).
    assert mgr._get_cached_profile("no-such-commander", None) is None
