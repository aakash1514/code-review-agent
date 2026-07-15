import time
import hashlib
import numpy as np
from huggingface_hub import InferenceClient
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue
from rag.chunker import CodeChunk
from config import settings

COLLECTION_NAME = "codebase"
EMBED_DIM = 384
EMBED_MODEL = "ibm-granite/granite-embedding-97m-multilingual-r2"


def _stable_point_id(repo: str, fn_key: str) -> int:
    """
    Includes repo in the hash input, not just fn_key. Without this, two
    different repos with an identically-named function in an identically-
    named file (e.g. "utils.py::helper") would hash to the SAME Qdrant point
    ID and silently overwrite each other's vector on upsert — a quieter
    version of the same cross-repo data-isolation bug that caused search()
    to return another repo's code as "similar patterns" (see repo field
    below for the other half of that fix).
    """
    digest = hashlib.sha256(f"{repo}::{fn_key}".encode()).hexdigest()
    return int(digest[:12], 16)


class CodebaseIndexer:
    def __init__(self):
        self.client = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
        self.hf_client = InferenceClient(provider="hf-inference", api_key=settings.HF_TOKEN)

    def _embed(self, texts: list[str], max_retries: int = 3) -> list[list[float]]:
        embeddings = []
        for text in texts:
            for attempt in range(max_retries):
                try:
                    result = self.hf_client.feature_extraction(text, model=EMBED_MODEL)
                    arr = np.array(result)

                    if arr.ndim == 1:
                        vec = arr.tolist()
                    elif arr.ndim == 2:
                        vec = arr.mean(axis=0).tolist()
                    else:
                        raise ValueError(f"Unexpected embedding shape: {arr.shape}")

                    embeddings.append(vec)
                    break
                except Exception as e:
                    if attempt == max_retries - 1:
                        print(f"    Final error for text (first 50 chars: {text[:50]!r}): {type(e).__name__}: {e}")
                        raise
                    wait = 5 * (attempt + 1)
                    print(f"    Embed call failed ({type(e).__name__}: {e}), retrying in {wait}s...")
                    time.sleep(wait)
        return embeddings

    def ensure_collection(self) -> None:
        existing = [c.name for c in self.client.get_collections().collections]
        if COLLECTION_NAME in existing:
            info = self.client.get_collection(COLLECTION_NAME)
            current_dim = info.config.params.vectors.size
            if current_dim != EMBED_DIM:
                print(f"    Collection dim mismatch (has {current_dim}, need {EMBED_DIM}) — recreating.")
                self.client.delete_collection(COLLECTION_NAME)
                existing.remove(COLLECTION_NAME)

        if COLLECTION_NAME not in existing:
            self.client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
            )
            # "repo" is the field the cross-repo isolation fix depends on —
            # every search() call below filters on it. Indexed as keyword
            # for exact-match filtering, same as the other payload fields.
            for field_name in ["repo", "file_path", "chunk_type", "name", "fn_key"]:
                self.client.create_payload_index(COLLECTION_NAME, field_name, field_schema="keyword")

    def index_chunks(self, chunks: list[CodeChunk], repo: str, batch_size: int = 20) -> None:
        """
        repo is required (not optional) — this collection is now shared
        across all repos the App is installed on, and every point must be
        tagged with which repo it belongs to, or search() has no way to
        scope results to the right codebase (this was the original bug:
        querying code-review-agent-test returned chunks from the
        code-review-agent project itself, since both lived in one
        untagged collection).
        """
        self.ensure_collection()
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            texts = [c.source for c in batch]
            vectors = self._embed(texts)

            points = [
                PointStruct(
                    id=_stable_point_id(repo, c.fn_key),
                    vector=vec,
                    payload={
                        "repo": repo,
                        "file_path": c.file_path,
                        "chunk_type": c.chunk_type,
                        "name": c.name,
                        "fn_key": c.fn_key,
                        "start_line": c.start_line,
                        "end_line": c.end_line,
                        "source": c.source,
                    },
                )
                for c, vec in zip(batch, vectors)
            ]
            self.client.upsert(collection_name=COLLECTION_NAME, points=points)
            print(f"    Indexed {min(i + batch_size, len(chunks))}/{len(chunks)} chunks")

    def search(
        self,
        query_text: str,
        repo: str,
        limit: int = 5,
        file_filter: str | None = None,
    ) -> list[dict]:
        """
        repo is required. Every query is scoped to a single repo's chunks —
        this is the other half of the cross-repo isolation fix; index_chunks
        tags points with repo, this filters on it, so a search against
        repo A can never return repo B's code even though they now share one
        physical Qdrant collection.
        """
        vector = self._embed([query_text])[0]

        must_conditions = [FieldCondition(key="repo", match=MatchValue(value=repo))]
        if file_filter:
            must_conditions.append(FieldCondition(key="file_path", match=MatchValue(value=file_filter)))
        query_filter = Filter(must=must_conditions)

        results = self.client.query_points(
            collection_name=COLLECTION_NAME,
            query=vector,
            limit=limit,
            query_filter=query_filter,
        )
        return [point.payload for point in results.points]