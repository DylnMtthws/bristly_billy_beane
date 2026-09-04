"""Evidence retrieval: attribution, bounds, and honest denominators."""

from __future__ import annotations

import pytest

from sabermetrics.cedh.domain import MetagameWindow
from sabermetrics.cedh.evidence import (
    CURATED_DIR,
    EvidenceChunk,
    EvidenceService,
    content_hash,
    load_curated,
)
from sabermetrics.cedh.settings import EvidenceSettings


def _service(meta, cards, **settings):
    return EvidenceService(
        meta,
        settings=EvidenceSettings(**settings) if settings else None,
        card_snapshot=cards.snapshot(),
    )


class TestAttribution:
    def test_a_chunk_with_no_source_cannot_be_built(self):
        with pytest.raises(ValueError, match="no source"):
            EvidenceChunk.build(
                chunk_id="c",
                kind="curated_strategy",
                content="x",
                source="",
                commander_key="k",
                corpus_snapshot="s",
            )

    def test_a_chunk_with_no_locator_cannot_be_built(self):
        """An unattributable chunk becomes an unattributable claim."""
        with pytest.raises(ValueError, match="neither a source_url nor a source_id"):
            EvidenceChunk.build(
                chunk_id="c",
                kind="curated_strategy",
                content="x",
                source="Somewhere",
                commander_key="k",
                corpus_snapshot="s",
            )

    def test_a_well_formed_chunk_hashes_its_content(self):
        chunk = EvidenceChunk.build(
            chunk_id="c",
            kind="curated_strategy",
            content="hello",
            source="Notes",
            source_id="curated:notes",
            commander_key="k",
            corpus_snapshot="s",
        )
        assert chunk.content_sha256 == content_hash("hello")

    def test_every_chunk_in_a_real_package_is_attributed(
        self, cedh_meta_populated, cedh_cards, kinnan_pack
    ):
        package = _service(cedh_meta_populated, cedh_cards).build(kinnan_pack.commander)
        assert package.chunks
        for chunk in package.chunks:
            assert chunk.source
            assert chunk.source_url or chunk.source_id
            assert chunk.commander_key == kinnan_pack.commander.key
            assert chunk.corpus_snapshot
            assert chunk.content_sha256
            assert chunk.fetched_at or chunk.published_at


class TestUnavailableMeta:
    def test_absent_tournament_data_is_reported_not_hidden(
        self, cedh_meta_absent, cedh_cards, kinnan_pack
    ):
        package = _service(cedh_meta_absent, cedh_cards).build(kinnan_pack.commander)
        assert package.meta_available is False
        assert package.meta_detail
        assert package.events_seen == 0

    def test_curated_material_still_arrives_without_tournament_data(
        self, cedh_meta_absent, cedh_cards, kinnan_pack
    ):
        package = _service(cedh_meta_absent, cedh_cards).build(kinnan_pack.commander)
        assert package.chunks
        assert all(c.kind == "curated_strategy" for c in package.chunks)


class TestDenominators:
    def test_inclusion_chunks_carry_the_sample_size(
        self, cedh_meta_populated, cedh_cards, kinnan_pack
    ):
        """Popularity is not quality, and a rate without n is not a claim."""
        package = _service(cedh_meta_populated, cedh_cards).build(kinnan_pack.commander)
        inclusion = next(c for c in package.chunks if c.kind == "inclusion_fact")
        assert inclusion.sample_size == 42
        assert "n=42" in inclusion.content
        assert "not measures of card quality" in inclusion.content

    def test_presence_is_labelled_as_exposure_not_quality(
        self, cedh_meta_populated, cedh_cards, kinnan_pack
    ):
        package = _service(cedh_meta_populated, cedh_cards).build(kinnan_pack.commander)
        presence = next(c for c in package.chunks if c.chunk_id == "meta-presence")
        assert "not quality" in presence.content

    def test_the_rendered_block_always_states_the_window(
        self, cedh_meta_populated, cedh_cards, kinnan_pack
    ):
        package = _service(cedh_meta_populated, cedh_cards).build(
            kinnan_pack.commander, MetagameWindow(days=90, min_event_size=64)
        )
        assert "last 90d, events of 64+" in package.render()


class TestBounds:
    def test_chunk_count_is_capped_and_the_drop_is_reported(
        self, cedh_meta_populated, cedh_cards, kinnan_pack
    ):
        package = _service(cedh_meta_populated, cedh_cards, max_chunks=2).build(
            kinnan_pack.commander
        )
        assert len(package.chunks) == 2
        assert package.dropped_chunks > 0
        assert "omitted by the evidence bounds" in package.render()

    def test_total_characters_are_capped(
        self, cedh_meta_populated, cedh_cards, kinnan_pack
    ):
        package = _service(cedh_meta_populated, cedh_cards, max_total_chars=300).build(
            kinnan_pack.commander
        )
        assert sum(len(c.content) for c in package.chunks) <= 300

    def test_truncation_rehashes_the_content_actually_sent(
        self, cedh_meta_populated, cedh_cards, kinnan_pack
    ):
        """A hash describing text that was not sent is worse than no hash."""
        package = _service(
            cedh_meta_populated, cedh_cards, max_chars_per_chunk=80
        ).build(kinnan_pack.commander)
        for chunk in package.chunks:
            assert chunk.content_sha256 == content_hash(chunk.content)
            assert len(chunk.content) <= 80


class TestEvidenceHash:
    def test_the_same_evidence_hashes_the_same(
        self, cedh_meta_populated, cedh_cards, kinnan_pack
    ):
        service = _service(cedh_meta_populated, cedh_cards)
        a = service.build(kinnan_pack.commander)
        b = service.build(kinnan_pack.commander)
        assert a.evidence_hash == b.evidence_hash

    def test_a_different_window_is_a_different_claim(
        self, cedh_meta_populated, cedh_cards, kinnan_pack
    ):
        service = _service(cedh_meta_populated, cedh_cards)
        a = service.build(kinnan_pack.commander, MetagameWindow(days=180))
        b = service.build(kinnan_pack.commander, MetagameWindow(days=30))
        assert a.evidence_hash != b.evidence_hash

    def test_different_content_hashes_differently(
        self, cedh_meta_populated, cedh_meta_absent, cedh_cards, kinnan_pack
    ):
        a = _service(cedh_meta_populated, cedh_cards).build(kinnan_pack.commander)
        b = _service(cedh_meta_absent, cedh_cards).build(kinnan_pack.commander)
        assert a.evidence_hash != b.evidence_hash


class TestCuratedMaterial:
    def test_the_shipped_curated_documents_load(self):
        docs = load_curated()
        assert docs
        for doc in docs:
            assert doc.source
            assert doc.source_url or doc.source_id
            assert doc.sections

    def test_curated_material_lives_in_config_not_data(self):
        """Curated strategy material is reviewed and versioned, not scraped."""
        assert CURATED_DIR.parent.name == "config"

    def test_a_document_scoped_to_another_commander_is_not_included(
        self, cedh_meta_absent, cedh_cards, tmp_path
    ):
        import yaml

        from sabermetrics.cedh.domain import CommanderIdentity

        (tmp_path / "other.yaml").write_text(
            yaml.safe_dump(
                {
                    "title": "t",
                    "source": "s",
                    "source_id": "curated:other",
                    "commander_names": ["Someone Else"],
                    "sections": ["scoped away"],
                }
            )
        )
        service = EvidenceService(
            cedh_meta_absent,
            curated_dir=tmp_path,
            card_snapshot=cedh_cards.snapshot(),
        )
        package = service.build(
            CommanderIdentity(oracle_ids=("x",), names=("Kinnan, Bonder Prodigy",))
        )
        assert package.chunks == ()

    def test_a_document_matches_by_name_across_corpora(
        self, cedh_meta_absent, cedh_cards, kinnan_pack
    ):
        """A curated doc outlives any one corpus's oracle_ids."""
        package = _service(cedh_meta_absent, cedh_cards).build(kinnan_pack.commander)
        assert any(
            c.chunk_id.startswith("kinnan_basalt_primer") for c in package.chunks
        )


def test_an_empty_package_renders_as_explicitly_empty(
    cedh_meta_absent, cedh_cards, tmp_path
):
    from sabermetrics.cedh.domain import CommanderIdentity

    service = EvidenceService(
        cedh_meta_absent, curated_dir=tmp_path, card_snapshot=cedh_cards.snapshot()
    )
    package = service.build(CommanderIdentity(oracle_ids=("x",), names=("Nobody",)))
    assert "NO EVIDENCE AVAILABLE" in package.render()


def test_chunks_never_contain_the_whole_corpus(
    cedh_meta_populated, cedh_cards, kinnan_pack
):
    """The model must never be handed the card corpus."""
    package = EvidenceService(
        cedh_meta_populated, card_snapshot=cedh_cards.snapshot()
    ).build(kinnan_pack.commander)
    rendered = package.render()
    corpus_names = {
        f.name for f in cedh_cards.get_by_oracle_ids(list(kinnan_pack.pool)).values()
    }
    mentioned = sum(1 for name in corpus_names if name in rendered)
    assert mentioned < len(corpus_names)
    assert len(rendered) <= EvidenceSettings().max_total_chars + 2000
