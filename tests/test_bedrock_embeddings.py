"""BedrockEmbedder (Titan Text Embeddings V2) and per-model Qdrant collections.
boto3's client is always mocked here; no AWS calls."""
import io
import json
from unittest.mock import MagicMock

import pytest

import rag.indexer
from rag.embeddings import (
    TITAN_MAX_CHARS, TITAN_MODEL, BedrockEmbedder, EmbeddingConfigError, EmbeddingDimensionError,
    HFApiEmbedder, LocalEmbedder, collection_name, get_embedder,
)


@pytest.fixture(autouse=True)
def no_aws_env(monkeypatch, tmp_path):
    """Tests must not depend on the developer's AWS setup: their shell may
    export these, and ~/.aws may hold an `aws login` or SSO profile whose
    credential provider needs extra packages or a browser. Point boto3 at
    empty config files and give it dummy static keys."""
    for name in ("AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE", "AWS_DEFAULT_PROFILE",
                 "BEDROCK_EMBED_REGION", "BEDROCK_EMBED_DIM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-credentials"))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret-key")


def fake_bedrock(dim: int = 512) -> MagicMock:
    """A bedrock-runtime client whose invoke_model returns a unit vector."""
    client = MagicMock()

    def invoke_model(**kwargs):
        vec = [0.0] * dim
        vec[0] = 1.0
        payload = {"embedding": vec, "inputTextTokenCount": 3}
        return {"body": io.BytesIO(json.dumps(payload).encode())}

    client.invoke_model.side_effect = invoke_model
    return client


def sent_bodies(client: MagicMock) -> list[dict]:
    return [json.loads(call.kwargs["body"]) for call in client.invoke_model.call_args_list]


def test_embeds_one_text_per_call_with_titan_v2_request():
    client = fake_bedrock()
    vectors = BedrockEmbedder(client=client).embed(["def a(): pass", "def b(): pass"])

    assert len(vectors) == 2 and all(len(v) == 512 for v in vectors)
    assert client.invoke_model.call_count == 2
    assert {c.kwargs["modelId"] for c in client.invoke_model.call_args_list} == {TITAN_MODEL}
    assert sent_bodies(client)[0] == {"inputText": "def a(): pass", "dimensions": 512, "normalize": True}


def test_empty_list_makes_no_calls():
    client = fake_bedrock()
    assert BedrockEmbedder(client=client).embed([]) == []
    client.invoke_model.assert_not_called()


def test_blank_and_oversized_inputs_are_made_valid():
    client = fake_bedrock()
    BedrockEmbedder(client=client).embed(["", "   ", "x" * (TITAN_MAX_CHARS * 3)])
    texts = [body["inputText"] for body in sent_bodies(client)]
    assert texts[0] == texts[1] == "(empty)"
    assert len(texts[2]) == TITAN_MAX_CHARS


def test_dimension_setting_is_sent_and_checked(monkeypatch):
    monkeypatch.setenv("BEDROCK_EMBED_DIM", "1024")
    client = fake_bedrock(dim=1024)
    embedder = BedrockEmbedder(client=client)
    assert embedder.dim == 1024
    assert len(embedder.embed(["x"])[0]) == 1024
    assert sent_bodies(client)[0]["dimensions"] == 1024


def test_wrong_vector_size_from_bedrock_raises():
    embedder = BedrockEmbedder(client=fake_bedrock(dim=256), dim=512)
    with pytest.raises(EmbeddingDimensionError, match="256-dim"):
        embedder.embed(["x"])


@pytest.mark.parametrize("bad", [384, "abc"])
def test_unsupported_dimension_is_a_config_error(bad):
    with pytest.raises(EmbeddingConfigError, match="BEDROCK_EMBED_DIM"):
        BedrockEmbedder(client=MagicMock(), dim=bad)


def test_get_embedder_builds_a_real_boto3_client_for_bedrock(monkeypatch, settings):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "bedrock")
    embedder = get_embedder()
    assert isinstance(embedder, BedrockEmbedder)
    assert embedder.region == "ap-south-1"  # default when nothing is set
    assert embedder.client.meta.region_name == "ap-south-1"
    assert get_embedder() is embedder  # process-wide


def test_bedrock_client_retries_throttling_adaptively():
    # A full-repo reindex makes hundreds of InvokeModel calls in a row; with
    # "standard"/5 attempts it failed halfway on a ThrottlingException.
    retries = BedrockEmbedder(region="ap-south-1").client.meta.config.retries
    assert retries["mode"] == "adaptive"
    assert retries["total_max_attempts"] == 11  # the first try + 10 retries


def test_region_falls_back_from_embed_region_to_aws_region(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    assert BedrockEmbedder(client=MagicMock()).region == "eu-west-1"
    monkeypatch.setenv("BEDROCK_EMBED_REGION", "us-east-1")
    assert BedrockEmbedder(client=MagicMock()).region == "us-east-1"


def test_each_model_family_gets_its_own_collection():
    assert collection_name(LocalEmbedder()) == "codebase_granite384"
    # Same model behind an API: same vectors, so the same collection.
    assert collection_name(HFApiEmbedder(client=MagicMock())) == "codebase_granite384"
    assert collection_name(BedrockEmbedder(client=MagicMock())) == "codebase_titan512"
    assert collection_name(BedrockEmbedder(client=MagicMock(), dim=1024)) == "codebase_titan1024"


def test_indexer_creates_the_titan_collection_with_the_titan_size(monkeypatch):
    qdrant_cls = MagicMock()
    monkeypatch.setattr(rag.indexer, "QdrantClient", qdrant_cls)
    qdrant = qdrant_cls.return_value
    qdrant.get_collections.return_value.collections = []

    indexer = rag.indexer.CodebaseIndexer(embedder=BedrockEmbedder(client=fake_bedrock()))
    indexer.ensure_collection()

    kwargs = qdrant.create_collection.call_args.kwargs
    assert kwargs["collection_name"] == "codebase_titan512"
    assert kwargs["vectors_config"].size == 512


def test_indexer_searches_the_collection_of_its_embedder(monkeypatch):
    qdrant_cls = MagicMock()
    monkeypatch.setattr(rag.indexer, "QdrantClient", qdrant_cls)
    qdrant = qdrant_cls.return_value
    qdrant.query_points.return_value.points = []

    rag.indexer.CodebaseIndexer(embedder=BedrockEmbedder(client=fake_bedrock())).search("q", repo="o/r")
    rag.indexer.CodebaseIndexer(embedder=LocalEmbedder()).search("q", repo="o/r")

    used = [c.kwargs["collection_name"] for c in qdrant.query_points.call_args_list]
    assert used == ["codebase_titan512", "codebase_granite384"]