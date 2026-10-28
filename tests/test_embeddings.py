import sys
import types
from unittest.mock import MagicMock

import numpy as np
import pytest
from fastapi.testclient import TestClient

import rag.embeddings as embeddings
import rag.indexer
from rag.embeddings import (
    EMBED_DIM, EMBED_MODEL, EmbeddingConfigError, EmbeddingDimensionError, HFApiEmbedder, LocalEmbedder,
    get_embedder, verify_embedder,
)
# Imported before the autouse fixture swaps it for the fake model.
from rag.embeddings import _load_sentence_transformer as real_load_sentence_transformer


# --- Provider selection ------------------------------------------------------

def test_default_provider_is_local(settings):
    assert settings.EMBEDDING_PROVIDER == "local"
    assert isinstance(get_embedder(), LocalEmbedder)


def test_missing_setting_falls_back_to_local(monkeypatch, settings):
    monkeypatch.delattr(type(settings), "EMBEDDING_PROVIDER")
    assert isinstance(get_embedder(), LocalEmbedder)


def test_hf_api_provider_selects_api_embedder(monkeypatch, settings):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "hf_api")
    assert isinstance(get_embedder(), HFApiEmbedder)


def test_unknown_provider_is_a_config_error(monkeypatch, settings):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")
    with pytest.raises(EmbeddingConfigError, match="EMBEDDING_PROVIDER"):
        get_embedder()


def test_embedder_is_created_once_per_provider():
    assert get_embedder("local") is get_embedder("local")


def test_indexer_uses_the_selected_embedder(monkeypatch, settings, fake_embedding_model):
    monkeypatch.setattr(rag.indexer, "QdrantClient", MagicMock())
    indexer = rag.indexer.CodebaseIndexer()

    indexer.search("def f(): pass", repo="octo/repo")

    assert isinstance(indexer.embedder, LocalEmbedder)
    assert fake_embedding_model.calls == [["def f(): pass"]]
    query_vector = indexer.client.query_points.call_args.kwargs["query"]
    assert len(query_vector) == EMBED_DIM


# --- LocalEmbedder -----------------------------------------------------------

def test_local_embedder_batch_encodes_normalised_vectors(fake_embedding_model):
    texts = [f"def f{i}(): return {i}" for i in range(5)]

    vectors = LocalEmbedder().embed(texts)

    assert fake_embedding_model.calls == [texts]  # one batch, not one call per text
    assert np.array(vectors).shape == (5, EMBED_DIM)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)


def test_local_embedder_with_no_texts_skips_the_model(fake_embedding_model):
    assert LocalEmbedder().embed([]) == []
    assert fake_embedding_model.calls == []


def test_model_is_loaded_once_on_cpu(monkeypatch):
    constructed = []

    class FakeST:
        def __init__(self, name, device, model_kwargs):
            constructed.append((name, device, model_kwargs))

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=FakeST))
    monkeypatch.setattr(embeddings, "_model", None)

    first = real_load_sentence_transformer()
    second = real_load_sentence_transformer()

    assert first is second
    assert constructed == [(EMBED_MODEL, "cpu", {"dtype": "float32"})]  # not the bfloat16 default


# --- Dimension check ---------------------------------------------------------

def test_verify_embedder_passes_for_384_dim(fake_embedding_model):
    assert isinstance(verify_embedder(), LocalEmbedder)


def test_verify_embedder_rejects_wrong_dimension(fake_embedding_model):
    fake_embedding_model.dim = 768
    with pytest.raises(EmbeddingDimensionError, match="768-dim"):
        verify_embedder()


def test_app_refuses_to_start_on_wrong_dimension(fake_embedding_model):
    from api.webhook import app
    fake_embedding_model.dim = 768
    with pytest.raises(EmbeddingDimensionError):
        with TestClient(app):
            pass


def test_app_starts_when_embeddings_are_merely_unavailable(monkeypatch):
    from api.webhook import app

    def offline():
        raise OSError("couldn't reach huggingface.co to download the model")
    monkeypatch.setattr(embeddings, "_load_sentence_transformer", offline)

    with TestClient(app) as client:
        assert client.post("/webhook").status_code == 401  # serving requests


# --- HFApiEmbedder -----------------------------------------------------------

def _http_error(status: int) -> Exception:
    err = RuntimeError(f"{status} error")
    err.response = types.SimpleNamespace(status_code=status)
    return err


def test_hf_api_embeds_a_batch_in_one_call():
    client = MagicMock()
    client.feature_extraction.return_value = np.ones((3, EMBED_DIM)).tolist()

    vectors = HFApiEmbedder(client=client).embed(["a", "b", "c"])

    assert len(vectors) == 3
    client.feature_extraction.assert_called_once_with(["a", "b", "c"], model=EMBED_MODEL)


def test_hf_api_token_level_output_falls_back_to_per_text_mean():
    client = MagicMock()
    token_level_batch = np.ones((2, 4, EMBED_DIM)).tolist()
    per_text = [np.full((4, EMBED_DIM), 2.0).tolist(), np.full((6, EMBED_DIM), 3.0).tolist()]
    client.feature_extraction.side_effect = [token_level_batch, *per_text]

    vectors = HFApiEmbedder(client=client).embed(["a", "b"])

    assert client.feature_extraction.call_count == 3
    assert vectors[0] == [2.0] * EMBED_DIM
    assert vectors[1] == [3.0] * EMBED_DIM


def test_hf_api_wrong_dimension_is_rejected():
    client = MagicMock()
    client.feature_extraction.return_value = np.ones((1, 768)).tolist()
    with pytest.raises(EmbeddingDimensionError):
        HFApiEmbedder(client=client).embed(["a"])


def test_hf_api_does_not_retry_out_of_credits(monkeypatch):
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: None)
    client = MagicMock()
    client.feature_extraction.side_effect = _http_error(402)

    with pytest.raises(RuntimeError, match="402"):
        HFApiEmbedder(client=client).embed(["a"])
    assert client.feature_extraction.call_count == 1


def test_hf_api_retries_server_errors(monkeypatch):
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: None)
    client = MagicMock()
    client.feature_extraction.side_effect = [_http_error(503), np.ones((1, EMBED_DIM)).tolist()]

    assert len(HFApiEmbedder(client=client).embed(["a"])) == 1
    assert client.feature_extraction.call_count == 2
