"""Regressions from build7 (worst deck generated) + SME scoring rulings."""

from pathlib import Path

from sabermetrics.models.template import DeckTemplate
from sabermetrics.pipeline.generators.ramp import RampPackageGenerator
from sabermetrics.reasoning.fit import FitScorer


def _template(**kw):
    base = dict(land_count=36, ramp_count=10, draw_count=8, removal_count=6,
                board_wipe_count=2, differentiator_slots=37, avg_cmc_target=3.0)
    base.update(kw)
    return DeckTemplate(**base)


def test_score_cards_batch_is_a_class_method():
    """The vet method existed only as a dead nested function in build7:
    an AttributeError was swallowed and the whole deck shipped unvetted."""
    assert callable(getattr(FitScorer, "score_cards_batch", None))


def test_pool_index_gates_table_candidates():
    """Candidate-table rows not in the filtered pool are dropped, and pool
    flags are inherited -- the bypass that admitted an $87 Mana Vault."""
    mana_vault = {"id": "mv", "name": "Mana Vault", "type_line": "Artifact",
                  "oracle_text": "{T}: Add {C}{C}{C}.", "price_usd": 87.65,
                  "cmc": 1, "_cvar_score": 0.9, "role_tags": '["ramp"]'}
    signet = {"id": "sig", "name": "Orzhov Signet", "type_line": "Artifact",
              "oracle_text": "{1}, {T}: Add {W}{B}.", "price_usd": 0.3,
              "cmc": 2, "_cvar_score": 0.5, "role_tags": '["ramp"]'}
    pool_index = {"Orzhov Signet": signet}  # Mana Vault excluded upstream

    gen = RampPackageGenerator(Path("/nonexistent.db"))
    result = gen.generate(
        color_identity=["W", "B"], target_count=3, budget_remaining=100.0,
        template=_template(), already_placed=[],
        role_tag_pool=[mana_vault, signet],
        commander_colors=["W", "B"], avg_cmc=3.0,
        pool_index=pool_index,
    )
    names = {a.card["name"] for a in result}
    assert "Mana Vault" not in names
    assert "Orzhov Signet" in names


def test_cheap_commander_reduces_ramp():
    from tests.test_template_deriver import _make_mock_profile
    from sabermetrics.reasoning.template_deriver import derive_deck_template

    profile = _make_mock_profile()
    profile.card_analysis.mana_cost = "{1}{W}{B}"  # Eriette-class 3-CMC
    cheap = derive_deck_template(profile)
    profile.card_analysis.mana_cost = "{2}{B}{R}"  # 4-CMC baseline
    baseline = derive_deck_template(profile)
    # Sequencing-cost ruling: cheap commanders drop 2 ramp slots.
    assert cheap.ramp_count == baseline.ramp_count - 3  # -2 rule, -1 cmdr term


def test_one_sided_wipe_outscores_uniform_in_low_creature_deck():
    from sabermetrics.pipeline.generators.removal import RemovalPackageGenerator

    one_sided = {"id": "v75", "name": "Vault 75 Style", "type_line": "Enchantment",
                 "oracle_text": "Destroy all creatures with power 4 or greater.",
                 "price_usd": 1.0, "cmc": 4, "_cvar_score": 0.5,
                 "role_tags": '["board_wipe"]'}
    uniform = {"id": "wr", "name": "Uniform Wrath", "type_line": "Sorcery",
               "oracle_text": "Destroy all creatures.",
               "price_usd": 1.0, "cmc": 4, "_cvar_score": 0.5,
               "role_tags": '["board_wipe"]'}
    gen = RemovalPackageGenerator(Path("/nonexistent.db"))
    result = gen.generate(
        color_identity=["W", "B"], target_count=0, budget_remaining=50.0,
        template=_template(type_targets={"enchantment": 36, "creature": 21}),
        already_placed=[], role_tag_pool=[uniform, one_sided],
        board_wipe_target=1, commander_colors=["W", "B"], avg_cmc=3.0,
    )
    wipes = [a.card["name"] for a in result]
    assert "Vault 75 Style" in wipes
    assert "Uniform Wrath" not in wipes


def test_fill_infrastructure_smoke():
    """Drives the REAL _fill_infrastructure path: build8 crashed with a
    NameError here that no unit test caught (they call generators directly)."""
    import sqlite3
    from pathlib import Path
    import pytest
    from sabermetrics.pipeline.deck_builder import DeckBuilder, DeckBuildRequest
    from sabermetrics.pipeline.trace import GenerationTracer

    db = Path("data/sabermetrics.db")
    if not db.exists():
        pytest.skip("no local DB")
    row = sqlite3.connect(db).execute(
        "SELECT id FROM cards WHERE name='Eriette of the Charmed Apple' "
        "AND is_legal_commander=1 LIMIT 1").fetchone()
    if row is None:
        pytest.skip("Eriette not in DB")

    b = DeckBuilder(db)
    b._tracer = GenerationTracer(generation_id="smoke")
    req = DeckBuildRequest(commander_id=row[0])
    cmdr = b._validate_request(req)
    # Minimal candidate pool: enough shape to exercise the generator plumbing.
    cands = b._load_role_tags(b._filter_candidates(req, cmdr))[:400]
    for c in cands:
        c.setdefault("_cvar_score", 0.5)
    from sabermetrics.models.template import DeckTemplate
    template = DeckTemplate(
        land_count=36, ramp_count=8, draw_count=6, removal_count=6,
        board_wipe_count=2, differentiator_slots=37, avg_cmc_target=3.0,
    )
    infra, used = b._fill_infrastructure(cands, cmdr, req, template)
    assert isinstance(infra, list) and used >= 0


def test_batch_vet_salvages_truncated_json():
    """Build9: truncated output failed the whole-array parse and every card
    defaulted to 5 -- the vet fired blanks. Complete objects are salvaged."""
    from pathlib import Path
    from unittest.mock import MagicMock, patch
    from sabermetrics.reasoning.fit import FitScorer

    truncated = ('[{"name": "Card A", "fit_score": 2, "reasoning": "bad"}, '
                 '{"name": "Card B", "fit_score": 8, "reasoning": "good"}, '
                 '{"name": "Card C", "fit_sco')  # cut off mid-object
    cards = [{"name": n, "type_line": "Enchantment", "price_usd": 1.0}
             for n in ("Card A", "Card B", "Card C")]
    fake = MagicMock()
    fake.call_with_cache.return_value = MagicMock(content=truncated)
    with patch("sabermetrics.reasoning.client.AnthropicClient") as C:
        C.get_instance.return_value = fake
        out = FitScorer(Path("/nonexistent.db")).score_cards_batch(
            cards=cards, profile_summary="t")
    scores = {c["name"]: r.fit_score for c, r in out}
    assert scores["Card A"] == 2      # salvaged verdict, not a default 5
    assert scores["Card B"] == 8
    assert scores["Card C"] == 5      # genuinely lost -> neutral default
