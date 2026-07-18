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
