"""Text embeddings for the RAG index, behind one interface.

settings.EMBEDDING_PROVIDER picks the backend:
  "local"  (default) runs the model in-process with sentence-transformers on
           CPU: no API credits, no network after the first model download.
  "hf_api" calls the Hugging Face Inference API with HF_TOKEN.

Both produce the same vectors for the same text (the model's own pooling,
L2-normalised, as the Inference API returns them), so an index built with
one can be searched with the other.
"""
import threading
import time

import numpy as np

from config import settings

EMBED_MODEL = "ibm-granite/granite-embedding-97m-multilingual-r2"
EMBED_DIM = 384
PROVIDERS = ("local", "hf_api")


class EmbeddingConfigError(RuntimeError):
    """Misconfiguration that no retry fixes: unknown provider, wrong dimension."""


class EmbeddingDimensionError(EmbeddingConfigError):
    pass


def _checked(vectors: list[list[float]], expected_count: int) -> list[list[float]]:
    if len(vectors) != expected_count:
        raise EmbeddingDimensionError(f"expected {expected_count} embeddings, got {len(vectors)}")
    for vec in vectors:
        if len(vec) != EMBED_DIM:
            raise EmbeddingDimensionError(
                f"{EMBED_MODEL} returned {len(vec)}-dim vectors; the Qdrant collection expects {EMBED_DIM}"
            )
    return vectors


class Embedder:
    name = "base"

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


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
        return _checked(np.asarray(vectors).tolist(), len(texts))


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
        return _checked(vectors, len(texts))

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


_embedders: dict[str, Embedder] = {}
_embedders_lock = threading.Lock()


def get_embedder(provider: str | None = None) -> Embedder:
    """The process-wide embedder for `provider` (default: the setting)."""
    provider = provider or getattr(settings, "EMBEDDING_PROVIDER", "local")
    if provider not in PROVIDERS:
        raise EmbeddingConfigError(f"EMBEDDING_PROVIDER must be one of {PROVIDERS}, got {provider!r}")
    with _embedders_lock:
        if provider not in _embedders:
            _embedders[provider] = LocalEmbedder() if provider == "local" else HFApiEmbedder()
        return _embedders[provider]


def verify_embedder(embedder: Embedder | None = None) -> Embedder:
    """Startup check: embeds one probe text and raises EmbeddingDimensionError
    unless it comes back as one EMBED_DIM vector. For "local" this also loads
    the model, so the first review doesn't pay for it."""
    embedder = embedder or get_embedder()
    embedder.embed(["embedding dimension check"])
    return embedder
