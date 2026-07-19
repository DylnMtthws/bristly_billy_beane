"""Tests for Phase 4 analytics layer (A4.1-A4.6)."""

import sqlite3
import time
from pathlib import Path

from sabermetrics.analytics.filters import (
    apply_hard_filters,
    filter_by_budget,
    filter_by_color_identity,
    filter_by_legality,
    filter_singleton_legal,
)
from sabermetrics.analytics.cvar import (
    CVARResult,
    ScoringContext,
    compute_cvar,
    compute_mana_efficiency_score,
    compute_price_efficiency,
    compute_synergy_score,
)
from sabermetrics.analytics.components import (
    ManaBaseScore,
    analyze_mana_base,
    count_board_wipes,
    count_card_draw,
    count_ramp_spells,
    count_removal,
)
from sabermetrics.analytics.brackets import BracketResult, classify_bracket
from sabermetrics.analytics.card_win_equity import wilson_lower_bound
from sabermetrics.analytics.embeddings import EmbeddingCache


# --- Filter tests (A4.1) ---

def test_color_identity_filter() -> None:
    """Color identity filter keeps only cards within commander colors."""
    cards = [
        {"name": "Llanowar Elves", "color_identity": '["G"]'},
        {"name": "Sol Ring", "color_identity": "[]"},
        {"name": "Counterspell", "color_identity": '["U"]'},
        {"name": "Korvold", "color_identity": '["B","R","G"]'},
        {"name": "Swords", "color_identity": '["W"]'},
    ]
    filtered = filter_by_color_identity(cards, ["B", "R", "G"])
    names = {c["name"] for c in filtered}
    assert "Llanowar Elves" in names
    assert "Sol Ring" in names
    assert "Korvold" in names
    assert "Counterspell" not in names  # U not in BRG
    assert "Swords" not in names  # W not in BRG


def test_legality_filter() -> None:
    """Legality filter keeps only legal-in-99 cards."""
    cards = [
        {"name": "Sol Ring", "is_legal_in_99": True},
        {"name": "Banned Card", "is_legal_in_99": False},
    ]
    filtered = filter_by_legality(cards)
    assert len(filtered) == 1
    assert filtered[0]["name"] == "Sol Ring"


def test_budget_filter() -> None:
    """Budget filter removes cards above the per-card ceiling.

    Ceiling is per_card_budget_fraction (0.25) of budget: $50 at $200. A $50
    premium staple (Smothering Tithe class) is admitted; a $78 card
    (Sheoldred class) is judged too much concentration and excluded.
    """
    cards = [
        {"name": "Cheap Card", "price_usd": 1.0},
        {"name": "Premium Staple", "price_usd": 50.0},
        {"name": "Too Concentrated", "price_usd": 78.0},
        {"name": "No Price Card"},
    ]
    filtered = filter_by_budget(cards, 200.0)
    names = {c["name"] for c in filtered}
    assert "Cheap Card" in names
    # Unknown price now EXCLUDES: a stale price snapshot let $87 Mana Vault
    # into a $50-ceiling pool as a floor-priced bargain.
    assert "No Price Card" not in names
    assert "Premium Staple" in names
    assert "Too Concentrated" not in names


def test_singleton_filter() -> None:
    """Singleton filter removes duplicate card names."""
    cards = [
        {"name": "Sol Ring"},
        {"name": "Sol Ring"},
        {"name": "Forest"},
        {"name": "Forest"},
    ]
    filtered = filter_singleton_legal(cards)
    assert len(filtered) == 3  # 1 Sol Ring + 2 Forests (basic)


def test_singleton_filter_keeps_cheapest_printing() -> None:
    """Singleton filter keeps the cheapest printing of duplicate cards."""
    cards = [
        {"name": "Sol Ring", "price_usd": 3.00},
        {"name": "Sol Ring", "price_usd": 1.00},
        {"name": "Arcane Signet", "price_usd": None},
        {"name": "Arcane Signet", "price_usd": 0.50},
    ]
    filtered = filter_singleton_legal(cards)
    by_name = {c["name"]: c for c in filtered}
    assert by_name["Sol Ring"]["price_usd"] == 1.00
    assert by_name["Arcane Signet"]["price_usd"] == 0.50


def test_apply_hard_filters_integration() -> None:
    """Integration test: apply_hard_filters reduces card pool (A4.1)."""
    db_path = Path("data/sabermetrics.db")
    if not db_path.exists():
        return  # Skip if no DB

    # Find Korvold (BRG commander)
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        "SELECT id FROM cards WHERE name LIKE 'Korvold%' "
        "AND is_legal_commander = 1 LIMIT 1"
    )
    row = cursor.fetchone()
    conn.close()

    if row is None:
        return  # Skip if Korvold not in DB

    korvold_id = row[0]
    candidates = apply_hard_filters(db_path, korvold_id, max_budget_usd=200.0)

    # A4.1: color identity should reduce ~104K legal → much fewer
    assert len(candidates) > 1000, f"Expected >1000 candidates, got {len(candidates)}"
    assert len(candidates) < 50000, f"Expected <50000 candidates, got {len(candidates)}"


# --- CVAR tests (A4.2) ---

def test_cvar_scoring_speed() -> None:
    """CVAR scoring runs in <100ms per card (A4.2)."""
    context = ScoringContext(
        commander_id="test-id",
        commander_name="Test Commander",
        commander_colors=["B", "R", "G"],
        commander_keywords=["Sacrifice"],
        commander_oracle_text="Whenever you sacrifice a permanent, draw a card.",
    )

    card = {
        "id": "test-card",
        "name": "Blood Artist",
        "oracle_text": "Whenever a creature dies, target player loses 1 life "
                       "and you gain 1 life.",
        "cmc": 2,
        "mana_cost": "{1}{B}",
        "color_identity": '["B"]',
        "keywords": "[]",
        "type_line": "Creature — Vampire",
        "rarity": "uncommon",
        "price_usd": 1.50,
    }

    # Time 100 calls
    start = time.time()
    for _ in range(100):
        result = compute_cvar(card, context)
    elapsed = time.time() - start

    assert elapsed < 10.0, f"100 CVAR calls took {elapsed:.2f}s (>10s)"
    assert isinstance(result, CVARResult)
    assert 0.0 <= result.composite_score <= 2.0
    assert 0.0 <= result.synergy_score <= 1.0
    assert 0.0 <= result.mana_efficiency_score <= 1.0


def test_cvar_synergy_scoring() -> None:
    """Synergy score is higher for cards that match commander mechanics."""
    context = ScoringContext(
        commander_id="test",
        commander_name="Korvold",
        commander_colors=["B", "R", "G"],
        commander_keywords=["Flying"],
        commander_oracle_text="Whenever you sacrifice a permanent, draw a card "
                              "and put a +1/+1 counter on Korvold.",
    )

    # Good synergy: sacrifice outlet
    good_card = {
        "oracle_text": "Sacrifice a creature: add one mana of any color.",
        "keywords": "[]", "color_identity": '["B","G"]',
        "type_line": "Creature", "cmc": 2, "rarity": "uncommon",
    }

    # Poor synergy: no meaningful overlap with sacrifice theme
    bad_card = {
        "oracle_text": "Vigilance. When this enters the battlefield, gain 3 life.",
        "keywords": '["Vigilance"]', "color_identity": '["W"]',
        "type_line": "Creature", "cmc": 3, "rarity": "common",
    }

    good_score = compute_synergy_score(good_card, context)
    bad_score = compute_synergy_score(bad_card, context)
    assert good_score > bad_score


def test_mana_efficiency_low_cmc_better() -> None:
    """Low-CMC cards score higher for mana efficiency."""
    low = {"cmc": 1, "type_line": "Instant", "oracle_text": "Draw a card."}
    high = {"cmc": 7, "type_line": "Sorcery", "oracle_text": "Draw cards."}

    assert compute_mana_efficiency_score(low) > compute_mana_efficiency_score(high)


def test_mana_efficiency_morph_creature_scores_higher() -> None:
    """Morph creature scores higher than plain creature with same printed CMC.

    A 6-CMC morph creature has effective CMC 3 (face-down for {3}),
    so it should score significantly better than a plain 6-CMC creature.
    """
    morph_6cmc = {
        "cmc": 6,
        "type_line": "Creature — Beast",
        "oracle_text": "Morph {4}{G}\nWhen this is turned face up, destroy target artifact.",
    }
    plain_6cmc = {
        "cmc": 6,
        "type_line": "Creature — Beast",
        "oracle_text": "When this enters the battlefield, destroy target artifact.",
    }
    morph_score = compute_mana_efficiency_score(morph_6cmc)
    plain_score = compute_mana_efficiency_score(plain_6cmc)
    # Morph effective CMC = 3 -> 0.75, plain CMC 6 -> 0.3
    assert morph_score > plain_score
    assert morph_score >= 0.7  # Effective CMC 3 -> base 0.75


def test_price_efficiency() -> None:
    """Cheaper cards score higher for price efficiency."""
    cheap = {"price_usd": 0.25}
    expensive = {"price_usd": 20.0}

    assert compute_price_efficiency(cheap) > compute_price_efficiency(expensive)


def test_price_efficiency_none_uses_floor() -> None:
    """Cards with no price get the same score as floor-priced cards."""
    from sabermetrics.analytics.cvar import PRICE_FLOOR_USD

    none_card = {}
    floor_card = {"price_usd": PRICE_FLOOR_USD}

    assert compute_price_efficiency(none_card) == compute_price_efficiency(floor_card)


def test_price_efficiency_zero_uses_floor() -> None:
    """Cards with $0 price get floor-price score, not the old special-case 1.0."""
    from sabermetrics.analytics.cvar import PRICE_FLOOR_USD

    zero_card = {"price_usd": 0}
    floor_card = {"price_usd": PRICE_FLOOR_USD}

    # $0 and floor-priced cards must produce identical scores
    assert compute_price_efficiency(zero_card) == compute_price_efficiency(floor_card)

    # At a higher avg_price where floor doesn't saturate to 1.0,
    # verify the floor is actually applied (not treated as $0 → division by zero)
    score = compute_price_efficiency(zero_card, avg_price=0.06)
    assert 0.0 < score <= 1.0


# --- Component tests (A4.6) ---

def test_count_ramp_spells() -> None:
    """Ramp counter detects mana acceleration cards."""
    cards = [
        {"type_line": "Artifact", "oracle_text": "{T}: Add {G}.", "keywords": "[]"},
        {"type_line": "Creature", "oracle_text": "{T}: Add one mana of any color.", "keywords": "[]"},
        {"type_line": "Sorcery", "oracle_text": "Search your library for a basic land card and put it onto the battlefield.", "keywords": "[]"},
        {"type_line": "Creature", "oracle_text": "Flying", "keywords": '["Flying"]'},
    ]
    assert count_ramp_spells(cards) >= 3


def test_count_card_draw() -> None:
    """Draw counter detects card draw effects."""
    cards = [
        {"type_line": "Instant", "oracle_text": "Draw two cards."},
        {"type_line": "Enchantment", "oracle_text": "Whenever a creature enters the battlefield, draw a card."},
        {"type_line": "Creature", "oracle_text": "Flying, vigilance"},
    ]
    assert count_card_draw(cards) >= 2


def test_count_removal() -> None:
    """Removal counter detects targeted removal."""
    cards = [
        {"type_line": "Instant", "oracle_text": "Destroy target creature."},
        {"type_line": "Instant", "oracle_text": "Exile target artifact or enchantment."},
        {"type_line": "Instant", "oracle_text": "Counter target spell."},
        {"type_line": "Creature", "oracle_text": "Haste"},
    ]
    assert count_removal(cards) >= 3


def test_count_board_wipes() -> None:
    """Board wipe counter detects mass removal."""
    cards = [
        {"type_line": "Sorcery", "oracle_text": "Destroy all creatures."},
        {"type_line": "Sorcery", "oracle_text": "Exile all artifacts and enchantments."},
        {"type_line": "Creature", "oracle_text": "Trample"},
    ]
    assert count_board_wipes(cards) >= 2


def test_analyze_mana_base() -> None:
    """Mana base analysis produces valid score."""
    cards = []
    # 36 lands
    for _ in range(12):
        cards.append({"type_line": "Basic Land — Forest", "name": "Forest",
                       "oracle_text": "{T}: Add {G}.", "cmc": 0})
    for _ in range(12):
        cards.append({"type_line": "Basic Land — Swamp", "name": "Swamp",
                       "oracle_text": "{T}: Add {B}.", "cmc": 0})
    for _ in range(12):
        cards.append({"type_line": "Basic Land — Mountain", "name": "Mountain",
                       "oracle_text": "{T}: Add {R}.", "cmc": 0})
    # Some ramp
    for _ in range(10):
        cards.append({"type_line": "Artifact", "name": "Sol Ring",
                       "oracle_text": "{T}: Add {C}{C}.", "cmc": 1,
                       "keywords": "[]"})

    result = analyze_mana_base(cards, ["B", "R", "G"])
    assert isinstance(result, ManaBaseScore)
    assert result.total_lands == 36
    assert result.score > 0


# --- Bracket tests (A4.5) ---

def test_bracket_precon() -> None:
    """Precon-like deck should classify as bracket 1 or 2."""
    cards = []
    for i in range(60):
        cards.append({
            "name": f"Vanilla Creature {i}",
            "type_line": "Creature",
            "oracle_text": "",
            "cmc": 4,
            "keywords": "[]",
            "color_identity": '["G"]',
        })
    for i in range(40):
        cards.append({
            "name": "Forest",
            "type_line": "Basic Land — Forest",
            "oracle_text": "{T}: Add {G}.",
            "cmc": 0,
            "keywords": "[]",
        })

    result = classify_bracket(cards)
    assert isinstance(result, BracketResult)
    assert result.bracket <= 2, f"Precon deck got bracket {result.bracket}"


def test_bracket_high_power() -> None:
    """Deck with fast mana + tutors should classify as bracket 4+."""
    cards = [
        {"name": "Sol Ring", "type_line": "Artifact", "oracle_text": "{T}: Add {C}{C}.", "cmc": 1, "keywords": "[]"},
        {"name": "Mana Crypt", "type_line": "Artifact", "oracle_text": "{T}: Add {C}{C}.", "cmc": 0, "keywords": "[]"},
        {"name": "Mana Vault", "type_line": "Artifact", "oracle_text": "{T}: Add {C}{C}{C}.", "cmc": 1, "keywords": "[]"},
        {"name": "Demonic Tutor", "type_line": "Sorcery", "oracle_text": "Search your library for a card, put it into your hand.", "cmc": 2, "keywords": "[]"},
        {"name": "Vampiric Tutor", "type_line": "Instant", "oracle_text": "Search your library for a card, put it on top.", "cmc": 1, "keywords": "[]"},
        {"name": "Mystical Tutor", "type_line": "Instant", "oracle_text": "Search your library for an instant or sorcery card.", "cmc": 1, "keywords": "[]"},
    ]
    # Add filler
    for i in range(94):
        cards.append({
            "name": f"Card {i}",
            "type_line": "Creature",
            "oracle_text": "",
            "cmc": 2,
            "keywords": "[]",
        })

    result = classify_bracket(cards)
    assert result.bracket >= 4, f"High power deck got bracket {result.bracket}"


# --- Wilson CI test (A4.4) ---

def test_wilson_lower_bound() -> None:
    """Wilson confidence interval computes correctly."""
    # 50% win rate with 100 samples should give lower bound < 0.5
    lb = wilson_lower_bound(50, 100)
    assert 0.3 < lb < 0.5

    # 0 successes, 0 total should give 0
    assert wilson_lower_bound(0, 0) == 0.0

    # 100% win rate with few samples should have wide interval
    lb_small = wilson_lower_bound(5, 5)
    lb_large = wilson_lower_bound(500, 500)
    assert lb_small < lb_large  # More samples = tighter interval


# --- Embedding cache test (D4.8) ---

def test_embedding_cache() -> None:
    """Embedding cache respects max size and LRU eviction."""
    import numpy as np

    cache = EmbeddingCache(max_size=3)
    cache.put("a", np.array([1.0]))
    cache.put("b", np.array([2.0]))
    cache.put("c", np.array([3.0]))
    assert cache.size == 3

    # Adding 4th should evict 'a'
    cache.put("d", np.array([4.0]))
    assert cache.size == 3
    assert cache.get("a") is None
    assert cache.get("b") is not None


# --- Mana efficiency impact tests ---

def test_mana_efficiency_impact_beats_vanilla() -> None:
    """A 6-CMC board wipe scores higher than a 2-CMC vanilla creature."""
    board_wipe = {
        "cmc": 6,
        "type_line": "Sorcery",
        "oracle_text": "Destroy all creatures.",
    }
    vanilla = {
        "cmc": 2,
        "type_line": "Creature — Bear",
        "oracle_text": "",
    }
    wipe_score = compute_mana_efficiency_score(board_wipe)
    vanilla_score = compute_mana_efficiency_score(vanilla)
    assert wipe_score > vanilla_score, (
        f"Board wipe ({wipe_score:.2f}) should beat vanilla ({vanilla_score:.2f})"
    )


def test_mana_efficiency_role_tags_used() -> None:
    """Card with role_tags gets impact-appropriate score from tags."""
    draw_engine = {
        "cmc": 3,
        "type_line": "Enchantment",
        "oracle_text": "Whenever an opponent casts a spell, you may pay {1}. "
                       "If you don't, that player draws a card.",
        "role_tags": '["draw"]',
    }
    # With draw role tag -> 1.3 multiplier, CMC 3 base 0.70 -> 0.91
    score = compute_mana_efficiency_score(draw_engine)
    assert score > 0.85, f"Draw engine with role tag should score >0.85, got {score:.2f}"

    # Same card without role tags falls back to oracle text
    no_tags = dict(draw_engine)
    no_tags.pop("role_tags")
    score_no_tags = compute_mana_efficiency_score(no_tags)
    # "draws a card" in oracle text -> medium-high 1.3 fallback
    assert score_no_tags > 0.80, (
        f"Draw engine via text fallback should score >0.80, got {score_no_tags:.2f}"
    )


def test_mana_efficiency_cheap_instant_premium() -> None:
    """Cheap high-impact instants get the graduated +0.3 bonus.

    Swords to Plowshares pattern: 1-mana instant exile should score
    higher than the same effect as a sorcery, and higher than a cheap
    instant without meaningful impact.
    """
    swords = {
        "cmc": 1,
        "type_line": "Instant",
        "oracle_text": "Exile target creature. Its controller gains life "
                       "equal to its power.",
    }
    # Same effect as sorcery
    sorcery_exile = {
        "cmc": 1,
        "type_line": "Sorcery",
        "oracle_text": "Exile target creature. Its controller gains life "
                       "equal to its power.",
    }
    # Cheap instant without impact
    weak_instant = {
        "cmc": 1,
        "type_line": "Instant",
        "oracle_text": "Target creature gains first strike until end of turn.",
    }

    swords_score = compute_mana_efficiency_score(swords)
    sorcery_score = compute_mana_efficiency_score(sorcery_exile)
    weak_score = compute_mana_efficiency_score(weak_instant)

    # Swords should beat the sorcery version (instant premium)
    assert swords_score > sorcery_score, (
        f"Swords ({swords_score:.2f}) should beat sorcery ({sorcery_score:.2f})"
    )
    # Swords should beat weak instant (impact matters)
    assert swords_score > weak_score, (
        f"Swords ({swords_score:.2f}) should beat weak instant ({weak_score:.2f})"
    )
    # The sorcery version still benefits from impact multiplier
    assert sorcery_score > weak_score, (
        f"Sorcery exile ({sorcery_score:.2f}) should beat weak instant ({weak_score:.2f})"
    )


def test_counter_archetype_rules_fire():
    """Sweep fix #3: counters archetype had zero rule coverage.

    A counter producer must rule-match Hardened Scales ("that many plus
    one") and Branching Evolution ("twice that many") via the
    text_contains_any clause; an unrelated card must not.
    """
    from sabermetrics.analytics.synergy_matrix import (
        _card_matches_clause,
        _load_synergy_rules,
    )

    rules = {r["id"]: r for r in _load_synergy_rules()}
    scaling = rules["counter_sources_with_scaling"]

    producer = {"oracle_text": "Put a +1/+1 counter on target creature."}
    hardened = {"oracle_text": "If one or more +1/+1 counters would be put on a creature you control, that many plus one +1/+1 counters are put on it instead."}
    branching = {"oracle_text": "If one or more +1/+1 counters would be put on a creature you control, twice that many +1/+1 counters are put on that creature instead."}
    unrelated = {"oracle_text": "Counter target spell."}

    assert _card_matches_clause(producer, scaling["trigger"])
    assert _card_matches_clause(hardened, scaling["payoff"])
    assert _card_matches_clause(branching, scaling["payoff"])
    assert not _card_matches_clause(unrelated, scaling["payoff"])

    prolif = rules["proliferate_with_counter_permanents"]
    assert _card_matches_clause(
        {"oracle_text": "At the beginning of your end step, proliferate."},
        prolif["trigger"],
    )
