"""Text embeddings for the RAG index, behind one interface.

settings.EMBEDDING_PROVIDER picks the backend:
  "local"   (default) runs granite-embedding-97m in-process with
            sentence-transformers on CPU: no API credits, no network after
            the first model download. Needs requirements-local-embed.txt.
  "hf_api"  the same model through the Hugging Face Inference API (HF_TOKEN).
  "bedrock" Amazon Titan Text Embeddings V2 on Bedrock, through boto3 and the
            normal AWS credential chain (AWS_PROFILE locally, the ECS task
            role in the cloud).

"local" and "hf_api" produce the same vectors for the same text, so an index
built with one can be searched with the other. Titan vectors live in a
different space and have a different size, so every model family gets its
own Qdrant collection (see collection_name()) and the two never mix.
"""
import json
import os
import threading
import time

import numpy as np

from config import settings

# The granite model used by "local" and "hf_api". The names EMBED_MODEL and
# EMBED_DIM are kept because callers and tests import them; they describe
# this model only. Use embedder.model / embedder.dim for the active one.
EMBED_MODEL = "ibm-granite/granite-embedding-97m-multilingual-r2"
EMBED_DIM = 384

TITAN_MODEL = "amazon.titan-embed-text-v2:0"
TITAN_DIMS = (256, 512, 1024)
TITAN_DEFAULT_DIM = 512
# Titan V2 takes at most 8,192 tokens (and 50,000 characters) per input.
# Code can run below 3 characters per token, so cap well under that; the
# local model truncates long inputs too (at 512 tokens), so this is no loss.
TITAN_MAX_CHARS = 16_000

PROVIDERS = ("local", "hf_api", "bedrock")
COLLECTION_PREFIX = "codebase"


def _setting(name: str, default=None):
    """settings.<name>, else the environment variable, else default. New
    settings work before config.py declares them."""
    value = getattr(settings, name, None)
    if value in (None, ""):
        value = os.getenv(name)
    return default if value in (None, "") else value


class EmbeddingConfigError(RuntimeError):
    """Misconfiguration that no retry fixes: unknown provider, wrong dimension."""


class EmbeddingDimensionError(EmbeddingConfigError):
    pass


def _checked(vectors: list[list[float]], expected_count: int, model: str, dim: int) -> list[list[float]]:
    if len(vectors) != expected_count:
        raise EmbeddingDimensionError(f"expected {expected_count} embeddings, got {len(vectors)}")
    for vec in vectors:
        if len(vec) != dim:
            raise EmbeddingDimensionError(
                f"{model} returned {len(vec)}-dim vectors; the Qdrant collection expects {dim}"
            )
    return vectors


class Embedder:
    name = "base"
    model = EMBED_MODEL
    dim = EMBED_DIM
    # Names the vector space; part of the Qdrant collection name.
    index_tag = "granite384"

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


def collection_name(embedder: Embedder) -> str:
    """Qdrant collection for this embedder's vectors, e.g. codebase_granite384
    or codebase_titan512."""
    return f"{COLLECTION_PREFIX}_{embedder.index_tag}"


_model = None
_model_lock = threading.Lock()


def _load_sentence_transformer():
    """Loads the model once per process and reuses it; the first call
    downloads it from the Hugging Face Hub into the local cache. float32
    explicitly: sentence-transformers otherwise loads this model in
    bfloat16, which is slower on most CPUs and drifts from the float32
    vectors the Inference API produced for the existing index."""
    global _model
    with _model_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(EMBED_MODEL, device="cpu", model_kwargs={"dtype": "float32"})
        return _model


class LocalEmbedder(Embedder):
    name = "local"

    def __init__(self, batch_size: int = 32):
        self.batch_size = batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = _load_sentence_transformer().encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return _checked(np.asarray(vectors).tolist(), len(texts), self.model, self.dim)


class HFApiEmbedder(Embedder):
    name = "hf_api"

    def __init__(self, client=None, max_retries: int = 3):
        if client is None:
            from huggingface_hub import InferenceClient
            client = InferenceClient(provider="hf-inference", api_key=settings.HF_TOKEN)
        self.client = client
        self.max_retries = max_retries

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        result = np.array(self._call(texts))
        if result.ndim == 2 and result.shape[0] == len(texts):
            vectors = result.tolist()  # one pooled vector per text, in a single call
        else:
            # Token-level output instead of pooled vectors: fall back to one
            # call per text, so padding never leaks into the mean.
            vectors = [self._pool(np.array(self._call(text))) for text in texts]
        return _checked(vectors, len(texts), self.model, self.dim)

    @staticmethod
    def _pool(arr: np.ndarray) -> list[float]:
        if arr.ndim == 1:
            return arr.tolist()
        if arr.ndim == 2:
            return arr.mean(axis=0).tolist()
        raise ValueError(f"Unexpected embedding shape: {arr.shape}")

    def _call(self, inputs: str | list[str]):
        for attempt in range(self.max_retries):
            try:
                return self.client.feature_extraction(inputs, model=EMBED_MODEL)
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                retryable = status is None or status == 429 or status >= 500
                if not retryable or attempt == self.max_retries - 1:
                    raise
                wait = 5 * (attempt + 1)
                print(f"    Embed call failed ({type(e).__name__}: {e}), retrying in {wait}s...")
                time.sleep(wait)


class BedrockEmbedder(Embedder):
    """Titan Text Embeddings V2 through bedrock-runtime InvokeModel.

    Settings (settings.<name> or environment variable):
      BEDROCK_EMBED_DIM     256, 512 (default) or 1024
      BEDROCK_EMBED_REGION  region to call; defaults to AWS_REGION, then ap-south-1

    Titan embeds one text per call. Throttling and transient errors are
    retried by botocore in "adaptive" mode (10 retries): on top of retrying
    with backoff, it rate-limits the client itself once Bedrock starts
    throttling, so a full-repo reindex (hundreds of calls in a row) slows
    down instead of failing. "standard" mode with 5 retries gave up halfway
    through reindexing this repo with a ThrottlingException. Access-denied and
    validation errors are not retried, and surface to the caller.
    """
    name = "bedrock"
    model = TITAN_MODEL

    def __init__(self, client=None, dim: int | None = None, region: str | None = None):
        try:
            dim = int(dim or _setting("BEDROCK_EMBED_DIM", TITAN_DEFAULT_DIM))
        except (TypeError, ValueError):
            raise EmbeddingConfigError(f"BEDROCK_EMBED_DIM must be one of {TITAN_DIMS}") from None
        if dim not in TITAN_DIMS:
            raise EmbeddingConfigError(f"BEDROCK_EMBED_DIM must be one of {TITAN_DIMS}, got {dim}")
        self.dim = dim
        self.index_tag = f"titan{dim}"
        self.region = region or _setting("BEDROCK_EMBED_REGION") or _setting("AWS_REGION", "ap-south-1")
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client(
                "bedrock-runtime",
                region_name=self.region,
                config=Config(retries={"mode": "adaptive", "max_attempts": 10},
                              connect_timeout=5, read_timeout=30),
            )
        self.client = client

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return _checked([self._embed_one(t) for t in texts], len(texts), self.model, self.dim)

    def _embed_one(self, text: str) -> list[float]:
        # Titan rejects blank input; an empty chunk still needs a vector.
        text = text[:TITAN_MAX_CHARS] if text and text.strip() else "(empty)"
        body = {"inputText": text, "dimensions": self.dim, "normalize": True}
        response = self.client.invoke_model(
            modelId=self.model,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        return json.loads(response["body"].read())["embedding"]


_FACTORIES = {"local": LocalEmbedder, "hf_api": HFApiEmbedder, "bedrock": BedrockEmbedder}

_embedders: dict[str, Embedder] = {}
_embedders_lock = threading.Lock()


def get_embedder(provider: str | None = None) -> Embedder:
    """The process-wide embedder for `provider` (default: the setting)."""
    provider = provider or getattr(settings, "EMBEDDING_PROVIDER", "local")
    if provider not in PROVIDERS:
        raise EmbeddingConfigError(f"EMBEDDING_PROVIDER must be one of {PROVIDERS}, got {provider!r}")
    with _embedders_lock:
        if provider not in _embedders:
            _embedders[provider] = _FACTORIES[provider]()
        return _embedders[provider]


def verify_embedder(embedder: Embedder | None = None) -> Embedder:
    """Startup check: embeds one probe text and raises EmbeddingDimensionError
    unless it comes back as one vector of the embedder's dimension. For
    "local" this also loads the model, so the first review doesn't pay for it."""
    embedder = embedder or get_embedder()
    embedder.embed(["embedding dimension check"])
    return embedder