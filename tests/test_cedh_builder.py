"""Deterministic candidate construction, its filters and its legality repair."""

from __future__ import annotations

import pytest

from sabermetrics.cedh.builder import build_candidate
from sabermetrics.cedh.domain import BuildConstraints, Legality
from sabermetrics.cedh.errors import CandidateConstraintViolation
from sabermetrics.cedh.repositories import CardFacts, CorpusSnapshot


class _MutableCards:
    """A card repository wrapper whose facts and legality can be perturbed."""

    def __init__(self, inner):
        self._inner = inner
        self.legality_overrides: dict[str, Legality] = {}
        self.fact_overrides: dict[str, CardFacts] = {}

    def snapshot(self) -> CorpusSnapshot:
        return self._inner.snapshot()

    def get_by_oracle_ids(self, oracle_ids):
        found = self._inner.get_by_oracle_ids(oracle_ids)
        for oid, facts in self.fact_overrides.items():
            if oid in found:
                found[oid] = facts
        return found

    def resolve_names(self, names):
        return self._inner.resolve_names(names)

    def legality(self, oracle_ids, fmt="commander"):
        result = self._inner.legality(oracle_ids, fmt)
        for oid, status in self.legality_overrides.items():
            if oid in result:
                result[oid] = status
        return result


@pytest.fixture
def mutable_cards(cedh_cards):
    return _MutableCards(cedh_cards)


def _size(candidate) -> int:
    return sum(c.quantity for c in candidate.cards)


class TestInvariants:
    def test_builds_exactly_ninety_nine(self, kinnan_pack, cedh_cards):
        assert _size(build_candidate(kinnan_pack, cedh_cards)) == 99

    def test_commander_is_not_in_its_own_ninety_nine(self, kinnan_pack, cedh_cards):
        candidate = build_candidate(kinnan_pack, cedh_cards)
        ids = {c.oracle_id for c in candidate.cards}
        assert not ids & set(candidate.commander.oracle_ids)

    def test_no_duplicate_oracle_ids(self, kinnan_pack, cedh_cards):
        candidate = build_candidate(kinnan_pack, cedh_cards)
        ids = [c.oracle_id for c in candidate.cards]
        assert len(ids) == len(set(ids))

    def test_every_card_is_within_the_commanders_colour_identity(
        self, kinnan_pack, cedh_cards
    ):
        candidate = build_candidate(kinnan_pack, cedh_cards)
        commander_colors = set(candidate.commander.color_identity)
        facts = cedh_cards.get_by_oracle_ids([c.oracle_id for c in candidate.cards])
        for card in facts.values():
            assert set(card.color_identity) <= commander_colors

    def test_the_role_budget_is_met_exactly(self, kinnan_pack, cedh_cards):
        candidate = build_candidate(kinnan_pack, cedh_cards)
        assert candidate.role_counts == kinnan_pack.role_budget.targets

    def test_auto_includes_are_present(self, kinnan_pack, cedh_cards):
        candidate = build_candidate(kinnan_pack, cedh_cards)
        ids = {c.oracle_id for c in candidate.cards}
        assert set(kinnan_pack.auto_include) <= ids


class TestDeterminism:
    def test_two_builds_are_byte_identical(self, kinnan_pack, cedh_cards):
        from datetime import UTC, datetime

        stamp = datetime(2026, 9, 3, tzinfo=UTC)
        kwargs = {"candidate_id": "fixed", "generated_at": stamp}
        first = build_candidate(kinnan_pack, cedh_cards, **kwargs)
        second = build_candidate(kinnan_pack, cedh_cards, **kwargs)
        assert first.to_json() == second.to_json()

    def test_the_deck_hash_is_order_independent(self, kinnan_pack, cedh_cards):
        candidate = build_candidate(kinnan_pack, cedh_cards)
        shuffled = candidate.model_copy(
            update={"cards": tuple(reversed(candidate.cards))}
        )
        assert shuffled.deck_sha256 == candidate.deck_sha256


class TestLegalityFilters:
    def test_a_banned_card_is_excluded_and_the_reason_is_stated(
        self, kinnan_pack, mutable_cards
    ):
        victim = kinnan_pack.auto_include[0]
        mutable_cards.legality_overrides[victim] = Legality.BANNED
        candidate = build_candidate(kinnan_pack, mutable_cards)
        assert victim not in {c.oracle_id for c in candidate.cards}
        assert any("banned" in note for note in candidate.notes)
        assert _size(candidate) == 99

    def test_unknown_legality_is_excluded_and_distinguished_from_illegal(
        self, kinnan_pack, mutable_cards
    ):
        """We could not check is not the same claim as it is not legal."""
        victim = kinnan_pack.auto_include[0]
        mutable_cards.legality_overrides[victim] = Legality.UNKNOWN
        candidate = build_candidate(kinnan_pack, mutable_cards)
        assert victim not in {c.oracle_id for c in candidate.cards}
        assert any("no legality row" in note for note in candidate.notes)

    def test_an_out_of_identity_card_is_excluded(
        self, kinnan_pack, cedh_cards, mutable_cards
    ):
        victim = kinnan_pack.auto_include[0]
        facts = cedh_cards.get_by_oracle_ids([victim])[victim]
        mutable_cards.fact_overrides[victim] = facts.model_copy(
            update={"color_identity": ("B",)}
        )
        candidate = build_candidate(kinnan_pack, mutable_cards)
        assert victim not in {c.oracle_id for c in candidate.cards}
        assert any("colour identity" in note for note in candidate.notes)


class TestRepair:
    def test_a_shortfall_is_filled_with_basics_in_the_commanders_colours(
        self, kinnan_pack, cedh_cards
    ):
        """Basics are the only legal filler: duplicates of them are allowed."""
        thin = kinnan_pack.model_copy(
            update={
                "pool": dict(list(kinnan_pack.pool.items())[:80]),
                "auto_include": (),
            }
        )
        candidate = build_candidate(thin, cedh_cards)
        assert _size(candidate) == 99
        basics = [c for c in candidate.cards if c.quantity > 1]
        assert basics
        assert all(c.name in {"Forest", "Island"} for c in basics)
        assert any("basic land" in note for note in candidate.notes)

    def test_an_over_full_pool_is_trimmed_to_ninety_nine(self, kinnan_pack, cedh_cards):
        inflated = kinnan_pack.model_copy(
            update={
                "role_budget": kinnan_pack.role_budget.model_copy(
                    update={
                        "targets": {
                            **kinnan_pack.role_budget.targets,
                            "flex": kinnan_pack.role_budget.target_for("flex") + 20,
                        }
                    }
                )
            }
        )
        assert _size(build_candidate(inflated, cedh_cards)) == 99

    def test_an_unsatisfiable_pool_refuses_rather_than_returning_basics(
        self, kinnan_pack, cedh_cards
    ):
        """Handing back 89 Forests is a build that looks successful and is not."""
        starved = kinnan_pack.model_copy(
            update={
                "pool": dict(list(kinnan_pack.pool.items())[:10]),
                "auto_include": (),
            }
        )
        with pytest.raises(CandidateConstraintViolation, match="cannot produce a deck"):
            build_candidate(starved, cedh_cards)


class TestConstraints:
    def test_excluded_cards_never_appear(self, kinnan_pack, cedh_cards):
        victim = kinnan_pack.auto_include[0]
        candidate = build_candidate(
            kinnan_pack,
            cedh_cards,
            constraints=BuildConstraints(excluded_oracle_ids=frozenset({victim})),
        )
        assert victim not in {c.oracle_id for c in candidate.cards}
        assert _size(candidate) == 99

    def test_a_required_card_outside_the_pool_is_reported_not_added(
        self, kinnan_pack, cedh_cards
    ):
        candidate = build_candidate(
            kinnan_pack,
            cedh_cards,
            constraints=BuildConstraints(
                must_include_oracle_ids=frozenset({"not-in-this-pool"})
            ),
        )
        assert "not-in-this-pool" not in {c.oracle_id for c in candidate.cards}
        assert any("not in this strategy pack" in n for n in candidate.notes)

    def test_flex_slots_come_out_of_flex_first_and_never_out_of_lands(
        self, kinnan_pack, cedh_cards
    ):
        flex_target = kinnan_pack.role_budget.target_for("flex")
        candidate = build_candidate(
            kinnan_pack,
            cedh_cards,
            constraints=BuildConstraints(flex_slots=flex_target),
        )
        assert candidate.role_counts.get(
            "land", 0
        ) == kinnan_pack.role_budget.target_for("land")
        assert _size(candidate) == 99

    def test_a_card_cannot_be_both_required_and_excluded(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="both required and excluded"):
            BuildConstraints(
                must_include_oracle_ids=frozenset({"a"}),
                excluded_oracle_ids=frozenset({"a"}),
            )

    def test_an_unsatisfiable_exclusion_list_refuses_rather_than_building(
        self, kinnan_pack, cedh_cards
    ):
        most_of_the_pool = frozenset(list(kinnan_pack.pool)[:90])
        with pytest.raises(CandidateConstraintViolation, match="cannot produce a deck"):
            build_candidate(
                kinnan_pack,
                cedh_cards,
                constraints=BuildConstraints(excluded_oracle_ids=most_of_the_pool),
            )


class TestPriceIsNotAnInput:
    """cEDH is proxy-normal: the engine optimises for performance, not price.

    These assert the *absence* of a price signal as a mechanism rather than a
    convention. A price field that exists gets used eventually — as a tie-break,
    a soft penalty, or a column someone sorts by — so the tests are about there
    being nothing to reach for.
    """

    def test_build_constraints_has_no_budget(self):
        assert "budget_usd" not in BuildConstraints.model_fields
        assert "proxy_unrestricted" not in BuildConstraints.model_fields

    def test_a_caller_still_passing_a_budget_fails_loudly(self):
        """Pydantic's default is to ignore unknown fields, which would be worse.

        A silently dropped budget gives the caller a deck that ignored the
        constraint it asked for, and no way to tell.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="budget_usd"):
            BuildConstraints(budget_usd=500)

    def test_card_facts_carry_no_price(self, cedh_cards, kinnan_pack):
        from sabermetrics.cedh.repositories import CardFacts

        assert "prices_usd" not in CardFacts.model_fields
        facts = cedh_cards.get_by_oracle_ids(list(kinnan_pack.pool))
        for card in facts.values():
            assert not any("price" in field for field in card.model_dump())

    def test_the_live_card_query_does_not_select_prices(self):
        """Not fetched, so it cannot be read."""
        from sabermetrics.cedh import adapters_postgres

        assert "rep_prices" not in adapters_postgres._CARD_COLUMNS

    def test_the_builder_module_never_mentions_a_price(self):
        import ast
        from pathlib import Path

        tree = ast.parse(
            Path("src/sabermetrics/cedh/builder.py").read_text(encoding="utf-8")
        )
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert not {n for n in names if "price" in n.lower()}
        assert not {n for n in names if "budget" in n.lower() and n != "role_budget"}

    def test_the_most_expensive_staples_are_all_played(self, kinnan_pack, cedh_cards):
        """The point of the change, stated as behaviour.

        Tropical Island, Mox Diamond and Grim Monolith are the priciest cards in
        this pack and among the strongest. In a proxy-normal format they are
        auto-includes, and no build may drop one for costing money.
        """
        candidate = build_candidate(kinnan_pack, cedh_cards)
        played = {c.name for c in candidate.cards}
        for name in ("Tropical Island", "Mox Diamond", "Grim Monolith", "Mana Vault"):
            assert name in played

    def test_the_model_is_told_price_is_not_a_consideration(self):
        from sabermetrics.cedh.prompts import (
            COMPARE_SYSTEM,
            EXPLAIN_SYSTEM,
            INTENT_SYSTEM,
        )

        for prompt in (EXPLAIN_SYSTEM, COMPARE_SYSTEM, INTENT_SYSTEM):
            assert "Price is not a consideration" in prompt
            assert "proxy-normal" in prompt

    def test_the_model_has_no_budget_flag_to_report(self):
        from sabermetrics.cedh.responses import IntentClassification

        assert "wants_budget" not in IntentClassification.model_fields
        assert "wants_proxy_free" not in IntentClassification.model_fields


class TestOwnershipIsNotAnInput:
    """What a player already owns is not a constraint.

    Building a new deck means acquiring or proxying cards — the normal case,
    not a problem to route around. Preferring what is already in a binder is a
    price constraint wearing a different hat.
    """

    def test_build_constraints_has_no_collection(self):
        assert "owned_oracle_ids" not in BuildConstraints.model_fields

    def test_a_caller_still_passing_a_collection_fails_loudly(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="owned_oracle_ids"):
            BuildConstraints(owned_oracle_ids=frozenset({"a"}))

    def test_the_model_has_no_collection_flag_to_report(self):
        from sabermetrics.cedh.responses import IntentClassification

        assert "mentions_owned_cards" not in IntentClassification.model_fields

    def test_the_sort_key_sees_only_the_card(self):
        """Ordering takes the pack and the role. There is no user in it."""
        import inspect

        from sabermetrics.cedh import builder

        params = set(inspect.signature(builder._sort_key).parameters)
        assert params == {"oracle_id", "pack", "role"}

    def test_the_build_is_reproducible_from_the_pack_alone(
        self, kinnan_pack, cedh_cards
    ):
        """Two people asking for the same pack get the same 99.

        This is what makes the candidate hash mean something: it identifies a
        list, not a list-plus-whoever-asked-for-it.
        """
        from datetime import UTC, datetime

        kwargs = {
            "candidate_id": "fixed",
            "generated_at": datetime(2026, 9, 3, tzinfo=UTC),
        }
        alice = build_candidate(kinnan_pack, cedh_cards, **kwargs)
        bob = build_candidate(
            kinnan_pack, cedh_cards, constraints=BuildConstraints(), **kwargs
        )
        assert alice.deck_sha256 == bob.deck_sha256
        assert alice.to_json() == bob.to_json()


class TestWinPackages:
    def test_completeness_is_computed_from_the_built_list(
        self, kinnan_pack, cedh_cards
    ):
        candidate = build_candidate(kinnan_pack, cedh_cards)
        chosen = {c.oracle_id for c in candidate.cards}
        for package in candidate.win_packages:
            expected = set(package.piece_oracle_ids) <= chosen
            assert package.complete is expected

    def test_missing_pieces_are_named(self, kinnan_pack, mutable_cards):
        piece = kinnan_pack.primary_win_package.piece_oracle_ids[0]
        mutable_cards.legality_overrides[piece] = Legality.BANNED
        candidate = build_candidate(kinnan_pack, mutable_cards)
        primary = candidate.win_packages[0]
        assert primary.complete is False
        assert piece in primary.missing_oracle_ids


class TestCandidateDocument:
    def test_the_document_is_self_describing(self, kinnan_pack, cedh_cards):
        document = build_candidate(kinnan_pack, cedh_cards).to_document()
        assert document["schema"] == "cedh-deck-candidate.v1"
        assert document["provenance"]["pack_id"] == "kinnan_basalt"
        assert document["provenance"]["card_snapshot"]
        assert len(document["deck_sha256"]) == 64

    def test_cards_are_addressed_by_oracle_id(self, kinnan_pack, cedh_cards):
        document = build_candidate(kinnan_pack, cedh_cards).to_document()
        assert all(card["oracle_id"] for card in document["cards"])

    def test_a_document_that_is_not_ninety_nine_cannot_be_constructed(
        self, kinnan_pack, cedh_cards
    ):
        """The size invariant is enforced where candidates are constructed.

        Constructed, not copied: ``model_copy`` deliberately skips validators,
        so this exercises the path the builder actually takes.
        """
        from pydantic import ValidationError

        candidate = build_candidate(kinnan_pack, cedh_cards)
        fields = candidate.model_dump()
        fields["cards"] = list(candidate.cards[:50])
        with pytest.raises(ValidationError, match="99 cards"):
            type(candidate)(**fields)

    def test_a_duplicate_card_cannot_be_constructed(self, kinnan_pack, cedh_cards):
        from pydantic import ValidationError

        candidate = build_candidate(kinnan_pack, cedh_cards)
        fields = candidate.model_dump()
        fields["cards"] = [*list(candidate.cards[:98]), candidate.cards[0]]
        with pytest.raises(ValidationError, match="duplicate oracle_ids"):
            type(candidate)(**fields)

    def test_the_commander_cannot_appear_in_its_own_ninety_nine(
        self, kinnan_pack, cedh_cards
    ):
        from pydantic import ValidationError

        candidate = build_candidate(kinnan_pack, cedh_cards)
        fields = candidate.model_dump()
        commander_card = candidate.cards[0].model_dump()
        commander_card["oracle_id"] = candidate.commander.oracle_ids[0]
        fields["cards"] = [commander_card, *list(candidate.cards[1:])]
        with pytest.raises(ValidationError, match="commander appears"):
            type(candidate)(**fields)
