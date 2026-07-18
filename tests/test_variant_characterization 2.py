"""Tests for Phase 4b LLM variant characterization (no API calls)."""

from sabermetrics.analytics.cluster_valuation import (
    CardInclusion,
    ClusterValuation,
    CommanderValuation,
)
from sabermetrics.analytics.deck_clustering import DeckRecord
from sabermetrics.models.llm_responses import ClusterVariantsResponse
from sabermetrics.reasoning.variant_characterization import (
    _strip_json_fences,
    build_clusters_block,
)


def _valuation() -> CommanderValuation:
    staple = CardInclusion(
        card_name="Sol Ring", count=45, cluster_size=49, inclusion_rate=0.92,
        ci_low=0.8, ci_high=0.97, margin_of_error=0.08, reliable=True,
        lift_vs_rest=0.02,
    )
    distinct = CardInclusion(
        card_name="Viscera Seer", count=23, cluster_size=49, inclusion_rate=0.47,
        ci_low=0.33, ci_high=0.61, margin_of_error=0.14, reliable=True,
        lift_vs_rest=0.46,
    )
    return CommanderValuation(
        commander="Test Cmdr", n_decks=74, k=1, floor=20,
        clusters=[ClusterValuation(
            cluster_id=0, dominant_archetype="aristocrats", size=49,
            meets_floor=True, staples=[staple], distinctive=[distinct],
            low_confidence_count=3,
        )],
    )


def test_build_clusters_block_includes_stats_and_sample() -> None:
    members = {0: [
        DeckRecord(deck_id="d1", card_names=["Sol Ring", "Viscera Seer", "Korvold"],
                   popularity_rank=1),
        DeckRecord(deck_id="d2", card_names=["Command Tower"], popularity_rank=2),
    ]}
    block = build_clusters_block(_valuation(), members, sample_decks=1)
    assert "CLUSTER 0" in block and "aristocrats" in block
    assert "Sol Ring" in block and "92%" in block          # staple w/ inclusion
    assert "Viscera Seer" in block and "+46" in block       # distinctive w/ lift
    assert "popularity rank 1" in block                     # most-popular sample
    assert "popularity rank 2" not in block                 # only 1 sample


def test_build_clusters_block_flags_below_floor() -> None:
    v = _valuation()
    v.clusters[0].meets_floor = False
    block = build_clusters_block(v, {0: []})
    assert "BELOW validity floor" in block


def test_strip_json_fences() -> None:
    assert _strip_json_fences('{"a": 1}') == '{"a": 1}'
    fenced = '```json\n{"a": 1}\n```'
    assert _strip_json_fences(fenced).strip() == '{"a": 1}'


def test_response_model_parses_expected_shape() -> None:
    data = {
        "variants": [{
            "cluster_id": 0, "variant_name": "Sacrifice-value",
            "game_plan": "Grind value via sac loops.",
            "key_cards": ["Viscera Seer"], "differentiators": "More sac outlets.",
            "confidence": "Check the sample deck.",
        }],
        "overall_note": "Two hypotheses.",
    }
    resp = ClusterVariantsResponse(**data)
    assert resp.variants[0].variant_name == "Sacrifice-value"
    assert resp.variants[0].key_cards == ["Viscera Seer"]
