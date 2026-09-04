"""The optional legacy stack fails clearly when it is not installed."""

import sys

import pytest


def test_embedding_service_names_the_legacy_extra(monkeypatch):
    from sabermetrics.analytics.embeddings import EmbeddingService

    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with pytest.raises(RuntimeError, match=r"install sabermetrics\[legacy\]"):
        EmbeddingService()._load_model()


def test_reference_indexer_names_the_legacy_extra(monkeypatch, tmp_path):
    from sabermetrics.reference_layer.indexer import EmbeddingIndexer

    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with pytest.raises(RuntimeError, match=r"install sabermetrics\[legacy\]"):
        EmbeddingIndexer(tmp_path / "index.db")._get_model()
