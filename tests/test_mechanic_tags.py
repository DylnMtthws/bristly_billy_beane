"""R1: the mechanic tag library, measured rather than asserted.

The plan's acceptance for R1 is four claims, and each one is a test here:

* at least 25 tags across ``cost:*`` and ``mana:*``
* every tag at 0.95 precision or better **on real oracle text**
* a coverage report of what carries no tag, grouped by type line
* a rebuild that is deterministic and content-hashed

The second is the one that needs care. ``TagDefinition.__post_init__`` can only
check that a definition *claims* a number above the floor; a claim is not a
measurement. :class:`TestEveryTagIsMeasured` recomputes precision from the
checked-in fixture cards — real Scryfall oracle text, materialised by
``scripts/fetch_tag_corpus.py`` — and fails on any drift between the declared
figure and the measured one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sabermetrics.mechanics.tags import (
    ALL_TAGS,
    MINIMUM_FIXTURES,
    PRECISION_FLOOR,
    SHIPPED_FAMILIES,
    TAG_LIBRARY_SHA256,
    AllOf,
    AnyOf,
    CardView,
    Cost,
    FaceView,
    Keyword,
    ManaValue,
    Not,
    TagDefinition,
    Text,
    TypeLine,
    always_yields_span,
    by_family,
    by_id,
    library_sha256,
)
from sabermetrics.mechanics.text import is_phyrexian, mana_symbols, mask_reminder_text
from sabermetrics.substrate import tag_store, tagging
from sabermetrics.substrate.corpus import (
    InMemoryCorpusSource,
    JsonCorpusSource,
    SnapshotIdentity,
    card_view_from_mapping,
)

FIXTURE_CARDS = (
    Path(__file__).resolve().parents[1] / "fixtures" / "mechanics" / "tag_cards.json"
)

#: R1 ships two families; the plan's acceptance number is 25 tags across them.
MINIMUM_SHIPPED_TAGS = 25
R1_REQUIRED_TAG_IDS = frozenset(
    {
        "cost:additional_nonmana_cost",
        "cost:affinity",
        "cost:alternative_cast_cost",
        "cost:convoke",
        "cost:cost_reduction",
        "cost:dash",
        "cost:delve",
        "cost:emerge",
        "cost:escape",
        "cost:evoke",
        "cost:flashback",
        "cost:foretell",
        "cost:free_alternative_cost",
        "cost:improvise",
        "cost:madness",
        "cost:morph",
        "cost:phyrexian_mana",
        "mana:adds_two_or_more",
        "mana:any_color",
        "mana:land_to_battlefield",
        "mana:land_to_hand",
        "mana:mana_dork",
        "mana:mana_filter",
        "mana:mana_rock",
        "mana:produces_mana",
        "mana:ritual",
        "mana:treasure_producer",
        "mana:untaps_land",
    }
)


@pytest.fixture(scope="module")
def fixture_corpus() -> JsonCorpusSource:
    return JsonCorpusSource(FIXTURE_CARDS)


@pytest.fixture(scope="module")
def fixture_index(fixture_corpus: JsonCorpusSource) -> dict[str, CardView]:
    return fixture_corpus.by_name()


def _card(**kwargs) -> CardView:
    base = {"oracle_id": "id", "name": "Test Card", "type_line": "Instant"}
    base.update(kwargs)
    return CardView(**base)


def _definition(**kwargs) -> TagDefinition:
    base = {
        "id": "cost:probe",
        "version": "1.0",
        "description": "A description long enough to be a claim about a card.",
        "limitations": "States what it misses, at sufficient length to be real.",
        "predicate": Text(r"probe"),
        "positive_fixtures": tuple(f"P{i}" for i in range(12)),
        "negative_fixtures": tuple(f"N{i}" for i in range(8)),
    }
    base.update(kwargs)
    return TagDefinition(**base)


# --- text helpers ----------------------------------------------------------


class TestReminderMasking:
    """Reminder text is the largest source of false positives, and masking it
    must not move a single offset — a span has to index back into the real
    printed text."""

    def test_masking_preserves_length(self):
        text = "Cascade (When you cast this spell, exile cards...) Deal 3 damage."
        assert len(mask_reminder_text(text)) == len(text)

    def test_masking_blanks_the_parenthetical(self):
        text = "Convoke (Your creatures can help cast this spell.)"
        masked = mask_reminder_text(text)
        assert "help cast this spell" not in masked
        assert masked.startswith("Convoke ")

    def test_masking_handles_nested_parentheses(self):
        text = "Foo (outer (inner) more) bar"
        masked = mask_reminder_text(text)
        assert masked == "Foo" + " " * 22 + "bar"
        assert len(masked) == len(text)

    def test_masking_leaves_unparenthesised_text_alone(self):
        text = "Counter target spell."
        assert mask_reminder_text(text) == text

    def test_a_span_taken_from_masked_text_reads_back_from_the_original(self):
        original = "Cascade (exile cards without paying its mana cost) Draw a card."
        card = _card(oracle_text=original)
        match = _definition(predicate=Text(r"draw a card")).evaluate(card)
        assert match is not None
        span = match.matched_span
        assert original[span.start : span.end] == span.text == "Draw a card"


class TestManaSymbols:
    def test_splits_a_printed_cost(self):
        assert mana_symbols("{1}{U/P}{U}") == ("1", "U/P", "U")

    def test_empty_and_none_are_legal(self):
        """Lands and modal double-faced cards publish an empty card-level cost."""
        assert mana_symbols(None) == ()
        assert mana_symbols("") == ()

    @pytest.mark.parametrize(
        "symbol,expected",
        [("U/P", True), ("P", True), ("2/U/P", True), ("W/U", False), ("3", False)],
    )
    def test_phyrexian_detection(self, symbol, expected):
        assert is_phyrexian(symbol) is expected


# --- the predicate algebra -------------------------------------------------


class TestPredicates:
    def test_text_searches_faces_when_the_card_level_is_empty(self):
        """A modal double-faced card publishes no card-level oracle text.

        A predicate that only read the card level would silently see nothing,
        which is the worst shape of failure: a success return over fewer rows.
        """
        card = _card(
            oracle_text=None,
            faces=(FaceView(name="Front", oracle_text="Add {G}."),),
        )
        match = _definition(predicate=Text(r"add \{G\}")).evaluate(card)
        assert match is not None
        assert match.matched_span.field == "faces[0].oracle_text"

    def test_cost_reads_the_printed_mana_cost(self):
        card = _card(mana_cost="{1}{U/P}")
        assert _definition(predicate=Cost(r"\{[^{}]*/P\}")).evaluate(card) is not None

    def test_keyword_matches_the_published_array_exactly(self):
        card = _card(keywords=("Flying", "Convoke"), oracle_text="convoke matters")
        match = _definition(predicate=Keyword("convoke")).evaluate(card)
        assert match is not None
        assert match.matched_span.field == "keywords"
        assert match.matched_span.text == "Convoke"
        assert _definition(predicate=Keyword("Delve")).evaluate(card) is None

    def test_anyof_takes_the_first_matching_child(self):
        card = _card(oracle_text="alpha beta")
        match = _definition(predicate=AnyOf(Text(r"beta"), Text(r"alpha"))).evaluate(
            card
        )
        assert match is not None
        assert match.matched_span.text == "beta"

    def test_allof_puts_the_first_child_span_first(self):
        """The first span is what a player is shown, so child order is meaning."""
        card = _card(oracle_text="Add {C}{C}.", type_line="Artifact")
        match = _definition(
            predicate=AllOf(Text(r"add \{C\}\{C\}"), TypeLine(r"Artifact"))
        ).evaluate(card)
        assert match is not None
        assert match.matched_span.field == "oracle_text"

    def test_not_inverts_and_contributes_no_span(self):
        card = _card(oracle_text="Add {C}.")
        predicate = AllOf(Text(r"add"), Not(Text(r"sacrifice")))
        match = _definition(predicate=predicate).evaluate(card)
        assert match is not None
        assert len(match.spans) == 1

    def test_mana_value_bounds(self):
        predicate = AllOf(Text(r"add"), ManaValue(maximum=1.0))
        assert (
            _definition(predicate=predicate).evaluate(
                _card(oracle_text="Add {C}.", mana_value=1.0)
            )
            is not None
        )
        assert (
            _definition(predicate=predicate).evaluate(
                _card(oracle_text="Add {C}.", mana_value=2.0)
            )
            is None
        )


class TestSpanIsStructurallyRequired:
    """``matched_span`` is non-optional, and it is enforced at construction.

    A tag row that asserts a mechanic with nothing to point at is an
    unattributable claim, which is the thing a tag exists not to be.
    """

    @pytest.mark.parametrize(
        "node,expected",
        [
            (Text(r"a"), True),
            (TypeLine(r"a"), True),
            (Cost(r"a"), True),
            (Keyword("Flying"), True),
            (ManaValue(maximum=1.0), False),
            (Not(Text(r"a")), False),
            (AllOf(Text(r"a"), ManaValue(maximum=1.0)), True),
            (AllOf(ManaValue(maximum=1.0), Not(Text(r"a"))), False),
            (AnyOf(Text(r"a"), Text(r"b")), True),
            (AnyOf(Text(r"a"), ManaValue(maximum=1.0)), False),
            (AnyOf(), False),
        ],
    )
    def test_always_yields_span(self, node, expected):
        assert always_yields_span(node) is expected

    def test_a_spanless_predicate_cannot_be_defined(self):
        with pytest.raises(ValueError, match="without a matched span"):
            _definition(predicate=ManaValue(maximum=1.0))

    def test_unknown_node_type_is_rejected_rather_than_assumed(self):
        with pytest.raises(TypeError):
            always_yields_span(object())  # type: ignore[arg-type]


class TestADefinitionMustEarnItsPlace:
    """The rules that stop a tag shipping. Each has a reason in the plan."""

    def test_empty_limitations_is_refused(self):
        with pytest.raises(ValueError, match="limitations is required"):
            _definition(limitations="")

    def test_boilerplate_short_limitations_is_refused(self):
        with pytest.raises(ValueError, match="limitations is required"):
            _definition(limitations="may miss some")

    def test_too_few_fixtures_is_refused(self):
        with pytest.raises(ValueError, match="need at least"):
            _definition(positive_fixtures=("a", "b"), negative_fixtures=("c",))

    def test_one_sided_fixtures_are_refused(self):
        with pytest.raises(ValueError, match="both positive and negative"):
            _definition(
                positive_fixtures=tuple(f"P{i}" for i in range(20)),
                negative_fixtures=(),
            )

    def test_a_name_cannot_be_both_positive_and_negative(self):
        with pytest.raises(ValueError, match="both positive and negative"):
            _definition(
                positive_fixtures=tuple(f"P{i}" for i in range(12)),
                negative_fixtures=("P0",) + tuple(f"N{i}" for i in range(8)),
            )

    def test_confidence_below_the_floor_is_refused(self):
        with pytest.raises(ValueError, match="precision floor"):
            _definition(confidence=0.9)

    @pytest.mark.parametrize("bad", ["phyrexian", "cost:", "COST:X", "spell:free"])
    def test_a_malformed_or_unknown_family_id_is_refused(self, bad):
        with pytest.raises(ValueError, match="tag id"):
            _definition(id=bad)

    def test_a_malformed_version_is_refused(self):
        with pytest.raises(ValueError, match="tag version"):
            _definition(version="1")


# --- the shipped library ---------------------------------------------------


class TestTheShippedLibrary:
    def test_r1_ships_both_families(self):
        assert SHIPPED_FAMILIES == ("cost", "mana")
        assert by_family("cost"), "cost:* is the family the driving example needs"
        assert by_family("mana"), "mana:* is the second R1 family"

    def test_at_least_the_acceptance_count_of_tags(self):
        assert len(ALL_TAGS) >= MINIMUM_SHIPPED_TAGS, (
            f"R1 acceptance is >= {MINIMUM_SHIPPED_TAGS} tags across two "
            f"families; the library has {len(ALL_TAGS)}"
        )

    def test_the_completed_r1_catalogue_cannot_silently_shrink(self):
        """A nonempty family is not evidence that the family stayed complete."""
        shipped = {definition.id for definition in ALL_TAGS}
        assert R1_REQUIRED_TAG_IDS <= shipped

    def test_every_family_is_declared_shipped(self):
        assert {d.family for d in ALL_TAGS} <= set(SHIPPED_FAMILIES)

    def test_ids_are_unique(self):
        ids = [d.id for d in ALL_TAGS]
        assert len(ids) == len(set(ids))

    def test_by_id_resolves_and_raises_on_a_miss(self):
        assert by_id(ALL_TAGS[0].id) is ALL_TAGS[0]
        with pytest.raises(KeyError):
            by_id("cost:not_a_tag")

    def test_an_unshipped_family_reports_absence_rather_than_erroring(self):
        """``draw:*`` lands in R2-R5. Until then the honest answer is empty."""
        assert by_family("draw") == ()

    def test_every_tag_carries_specific_limitations(self):
        """Boilerplate is the failure this field exists to prevent."""
        boilerplate = {"may miss some cards", "none", "n/a", "unknown"}
        for definition in ALL_TAGS:
            assert definition.limitations.strip().casefold() not in boilerplate
            assert len(definition.limitations) >= 40, definition.id

    def test_every_tag_has_the_minimum_fixtures(self):
        for definition in ALL_TAGS:
            total = len(definition.positive_fixtures) + len(
                definition.negative_fixtures
            )
            assert total >= MINIMUM_FIXTURES, definition.id

    def test_the_library_hash_is_stable_and_order_independent(self):
        assert library_sha256(ALL_TAGS) == TAG_LIBRARY_SHA256
        assert library_sha256(tuple(reversed(ALL_TAGS))) == TAG_LIBRARY_SHA256

    def test_the_library_hash_moves_when_a_predicate_does(self):
        changed = (_definition(predicate=Text(r"other")),) + ALL_TAGS
        assert library_sha256(changed) != TAG_LIBRARY_SHA256


class TestEveryTagIsMeasured:
    """The 0.95 floor, recomputed from real oracle text.

    ``__post_init__`` checks the number a tag *claims*. This checks the number
    it *gets*, and treats any gap between the two as a failure rather than as a
    rounding difference — a declared precision that nothing recomputes is a
    claim nobody checks.
    """

    def test_every_fixture_name_resolves(self, fixture_index):
        missing: list[str] = []
        for definition in ALL_TAGS:
            for name in definition.positive_fixtures + definition.negative_fixtures:
                if name not in fixture_index:
                    missing.append(f"{definition.id}: {name}")
        assert not missing, (
            "fixture names that resolve to no card:\n"
            + "\n".join(sorted(missing))
            + "\n\nRegenerate with: python scripts/fetch_tag_corpus.py "
            "--names-from-tags --out fixtures/mechanics/tag_cards.json"
        )

    def test_every_tag_clears_the_precision_floor(self, fixture_index):
        scores = tagging.measure_all(ALL_TAGS, fixture_index)
        below = [s for s in scores if s.precision < PRECISION_FLOOR]
        assert not below, tagging.render_scores(scores)

    def test_no_tag_misses_its_own_positives(self, fixture_index):
        """Perfect precision over nothing is perfect and useless."""
        scores = tagging.measure_all(ALL_TAGS, fixture_index)
        missed = [s for s in scores if s.recall < 1.0]
        assert not missed, tagging.render_scores(scores)

    def test_declared_confidence_matches_the_measurement(self, fixture_index):
        drift = [
            f"{s.tag_id}: declares {s.declared_confidence:.4f}, "
            f"measures {s.precision:.4f}"
            for s in tagging.measure_all(ALL_TAGS, fixture_index)
            if abs(s.precision - s.declared_confidence) > 1e-9
        ]
        assert not drift, "\n".join(drift)

    def test_the_negative_pool_is_mostly_mechanically_adjacent(self, fixture_index):
        """Negatives should be near misses, and that is a human judgement.

        Whether *this* negative is a near miss for *that* predicate cannot be
        decided by a test — it is the same kind of claim as a golden question's
        ``required_oracle_ids``, which the spec (§10.3) is explicit about not
        pretending to machine-check. Faking the check would be worse than not
        having it: it would read as a guarantee and provide none.

        What is checkable is the shape of the pool as a whole. A negative
        fixture that carries some other shipped tag is a card with a mechanic
        the library can see, which is weak evidence it sits near something; a
        pool that was mostly vanilla filler would fail this and should. The
        per-tag judgement stays with the author and the reviewer, and each
        tag's ``limitations`` is where the boundary it drew is written down.
        """
        negatives = {
            name for definition in ALL_TAGS for name in definition.negative_fixtures
        }
        cards = [
            fixture_index[name] for name in sorted(negatives) if name in fixture_index
        ]
        tagged = sum(
            1
            for card in cards
            if any(definition.evaluate(card) is not None for definition in ALL_TAGS)
        )
        assert cards
        fraction = tagged / len(cards)
        assert fraction >= 0.5, (
            f"only {tagged}/{len(cards)} negative fixtures carry any shipped tag; "
            "the negative pool looks like filler rather than near misses"
        )


class TestSpansPointAtRealText:
    """Every span must read back from the card it claims to describe.

    This is what makes a tag auditable rather than an assertion. It is checked
    over a whole build rather than on one example, because an off-by-one in the
    masking would be invisible on a card with no reminder text.
    """

    def test_every_span_slices_back_to_its_own_text(self, fixture_corpus):
        cards = {card.oracle_id: card for card in fixture_corpus.iter_cards()}
        result = tagging.build(fixture_corpus, ALL_TAGS)
        assert result.rows, "the fixture corpus should produce tag rows"
        for row in result.rows:
            span = json.loads(row.matched_span)
            card = cards[row.oracle_id]
            field = span["field"]
            if field.startswith("faces["):
                index = int(field[len("faces[") : field.index("]")])
                source = getattr(card.faces[index], field.rsplit(".", 1)[1])
            elif field == "keywords":
                source = ", ".join(card.keywords)
            else:
                source = getattr(card, field)
            assert source is not None, f"{row.tag_id} span names an empty field"
            assert (
                source[span["start"] : span["end"]] == span["text"]
            ), f"{row.tag_id} on {card.name}: span does not read back"


# --- the build -------------------------------------------------------------


class TestTheBuildIsDeterministic:
    def test_two_builds_agree_byte_for_byte(self, fixture_corpus):
        first = tagging.build(fixture_corpus, ALL_TAGS)
        second = tagging.build(fixture_corpus, ALL_TAGS)
        assert first.rows == second.rows
        assert first.content_sha256 == second.content_sha256

    def test_definition_order_does_not_change_the_output(self, fixture_corpus):
        forward = tagging.build(fixture_corpus, ALL_TAGS)
        backward = tagging.build(fixture_corpus, tuple(reversed(ALL_TAGS)))
        assert forward.rows == backward.rows
        assert forward.content_sha256 == backward.content_sha256

    def test_rows_are_sorted_by_card_then_tag(self, fixture_corpus):
        rows = tagging.build(fixture_corpus, ALL_TAGS).rows
        assert list(rows) == sorted(rows, key=lambda r: (r.oracle_id, r.tag_id))

    def test_the_content_hash_excludes_the_snapshot(self):
        """A nightly corpus refresh must not read as a library change."""
        card = _card(oracle_id="x", oracle_text="probe here")
        definition = _definition()
        one = tagging.build(
            InMemoryCorpusSource((card,), source_view="a"), (definition,)
        )
        two = tagging.build(
            InMemoryCorpusSource((card,), source_view="b"), (definition,)
        )
        assert one.content_sha256 == two.content_sha256
        assert one.snapshot.sha256() != two.snapshot.sha256()
        assert one.rows[0].snapshot_hash != two.rows[0].snapshot_hash

    def test_a_changed_predicate_changes_the_content_hash(self):
        card = _card(oracle_id="x", oracle_text="probe here")
        source = InMemoryCorpusSource((card,))
        one = tagging.build(source, (_definition(),))
        two = tagging.build(source, (_definition(predicate=Text(r"here")),))
        assert one.content_sha256 != two.content_sha256


class TestCoverageIsReported:
    def test_untagged_cards_are_grouped_by_type_line(self):
        source = InMemoryCorpusSource(
            (
                _card(oracle_id="1", oracle_text="probe", type_line="Instant"),
                _card(oracle_id="2", oracle_text="nothing", type_line="Instant"),
                _card(
                    oracle_id="3",
                    oracle_text="nothing",
                    type_line="Legendary Artifact Creature — Golem",
                ),
            )
        )
        report = tagging.build(source, (_definition(),)).coverage
        assert report.total_cards == 3
        assert report.tagged_cards == 1
        assert dict(report.untagged_by_type) == {"Instant": 1, "Artifact Creature": 1}

    def test_supertypes_and_subtypes_are_dropped_from_the_group(self):
        card = _card(type_line="Legendary Snow Creature — Elf Druid")
        assert tagging.type_group(card) == "Creature"

    def test_a_card_with_no_recognised_type_is_named_rather_than_dropped(self):
        """Stickers, Conspiracies and pre-modern ``Summon`` lines land here.

        They are real cards with an unrecognised type, not malformed rows, so
        the group is named for that rather than for an empty line.
        """
        assert tagging.type_group(_card(type_line="")) == "(no recognised card type)"
        assert (
            tagging.type_group(_card(type_line="Summon Dragon"))
            == "(no recognised card type)"
        )

    def test_the_rendered_report_states_the_untagged_section(self, fixture_corpus):
        report = tagging.build(fixture_corpus, ALL_TAGS).coverage
        text = tagging.render_coverage(report)
        assert "untagged cards by type line" in text
        assert "carrying no tag" in text

    def test_a_truncated_tail_says_how_much_it_hid(self):
        cards = tuple(
            _card(oracle_id=str(i), type_line=t)
            for i, t in enumerate(
                ["Instant", "Sorcery", "Creature", "Land", "Artifact"]
            )
        )
        report = tagging.build(InMemoryCorpusSource(cards), (_definition(),)).coverage
        text = tagging.render_coverage(report, top=2)
        assert "further groups" in text

    def test_a_tag_that_matched_nothing_is_called_out(self):
        source = InMemoryCorpusSource((_card(oracle_id="1", oracle_text="nothing"),))
        text = tagging.render_coverage(tagging.build(source, (_definition(),)).coverage)
        assert "matched nothing" in text


# --- corpus sources --------------------------------------------------------


class TestCorpusSources:
    def test_the_fixture_snapshot_names_its_provenance_honestly(self, fixture_corpus):
        """It is a Scryfall export and it says so.

        Production tag builds read ``mtg_v1.card_any_medium``. A fixture
        snapshot that labelled itself with the production view would let a row
        built from it be mistaken for one built from the real corpus.
        """
        identity = fixture_corpus.identity()
        assert identity.source_view == "scryfall:oracle_cards"
        assert "mtg_v1" not in identity.source_view

    def test_an_unprovenanced_snapshot_says_so_rather_than_inventing_a_source(
        self, tmp_path
    ):
        path = tmp_path / "cards.json"
        path.write_text(json.dumps({"cards": []}), encoding="utf-8")
        assert (
            JsonCorpusSource(path).identity().source_view == "unattributed:cards.json"
        )

    def test_jsonl_takes_provenance_from_its_sidecar(self, tmp_path):
        path = tmp_path / "corpus.jsonl"
        path.write_text(
            json.dumps({"oracle_id": "1", "name": "A", "oracle_text": "probe"}) + "\n",
            encoding="utf-8",
        )
        path.with_suffix(".meta.json").write_text(
            json.dumps({"snapshot": {"source_view": "scryfall:oracle_cards"}}),
            encoding="utf-8",
        )
        source = JsonCorpusSource(path)
        assert source.identity().source_view == "scryfall:oracle_cards"
        assert [c.name for c in source.iter_cards()] == ["A"]

    def test_a_missing_snapshot_raises_rather_than_reading_as_empty(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            JsonCorpusSource(tmp_path / "absent.json")

    def test_by_name_indexes_the_front_face_and_the_joined_name(self, tmp_path):
        path = tmp_path / "cards.json"
        path.write_text(
            json.dumps({"cards": [{"oracle_id": "1", "name": "Fire // Ice"}]}),
            encoding="utf-8",
        )
        index = JsonCorpusSource(path).by_name()
        assert "Fire" in index and "Fire // Ice" in index

    def test_a_record_without_an_identity_is_refused(self):
        with pytest.raises(KeyError):
            card_view_from_mapping({"name": "No Oracle Id"})

    def test_the_view_carries_no_price_field(self):
        """ADR-025. A price field that exists becomes a tie-break."""
        assert not any("price" in field for field in CardView.__dataclass_fields__)

    def test_snapshot_identity_hash_changes_with_the_corpus(self):
        a = SnapshotIdentity(source_view="v", row_count=1)
        b = SnapshotIdentity(source_view="v", row_count=2)
        assert a.sha256() != b.sha256()
        assert a.sha256() == SnapshotIdentity(source_view="v", row_count=1).sha256()


# --- persistence -----------------------------------------------------------


class TestTagStore:
    def test_round_trip(self, tmp_path, fixture_corpus):
        db = tmp_path / "research.db"
        result = tagging.build(fixture_corpus, ALL_TAGS)
        assert tag_store.write_build(
            db, result, built_at="2026-09-09T00:00:00Z"
        ) == len(result.rows)
        rows = tag_store.read_rows(db)
        assert len(rows) == len(result.rows)
        assert rows[0].matched_span.startswith("{")

    def test_a_rebuild_replaces_rather_than_merges(self, tmp_path):
        """A card that stopped matching must stop being reported."""
        db = tmp_path / "research.db"
        card = _card(oracle_id="1", oracle_text="probe here")
        source = InMemoryCorpusSource((card,))
        tag_store.write_build(db, tagging.build(source, (_definition(),)))
        assert len(tag_store.read_rows(db)) == 1
        tag_store.write_build(
            db, tagging.build(source, (_definition(predicate=Text(r"absent")),))
        )
        assert tag_store.read_rows(db) == []

    def test_the_build_is_recorded_with_its_hashes_and_coverage(
        self, tmp_path, fixture_corpus
    ):
        db = tmp_path / "research.db"
        result = tagging.build(fixture_corpus, ALL_TAGS)
        tag_store.write_build(db, result, built_at="2026-09-09T00:00:00Z")
        recorded = tag_store.latest_build(db)
        assert recorded is not None
        assert recorded["content_sha256"] == result.content_sha256
        assert recorded["library_sha256"] == result.library_sha256
        assert recorded["source_view"] == "scryfall:oracle_cards"
        coverage = json.loads(str(recorded["coverage_json"]))
        assert coverage["total_cards"] == result.coverage.total_cards
        assert coverage["untagged_by_type"]

    def test_an_unbuilt_database_reports_absence_not_an_empty_corpus(self, tmp_path):
        assert tag_store.latest_build(tmp_path / "fresh.db") is None

    def test_filtering_by_tag(self, tmp_path, fixture_corpus):
        db = tmp_path / "research.db"
        result = tagging.build(fixture_corpus, ALL_TAGS)
        tag_store.write_build(db, result)
        tag_id = result.tag_ids[0]
        rows = tag_store.read_rows(db, tag_id=tag_id)
        assert rows and all(row.tag_id == tag_id for row in rows)
