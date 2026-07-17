import os
import shutil
import tempfile
from contextlib import contextmanager

from rag.chunker import chunk_file
from rag.knowledge_graph import KnowledgeGraph, kg_path_for_repo
from rag.indexer import CodebaseIndexer
from tools.github_tools import get_repo_tree, get_full_file_content

EXCLUDED_DIRS = {".git", "venv", "env", ".venv", "node_modules", "__pycache__", "dist", "build"}
INDEXABLE_EXTENSIONS = (".py", ".js", ".ts")


def _is_excluded(path: str) -> bool:
    parts = path.split("/")
    return any(part in EXCLUDED_DIRS for part in parts[:-1])


@contextmanager
def _fetch_repo_snapshot(repo: str, ref: str, token: str):
    """
    Downloads every indexable file at `ref` into a fresh temp directory,
    mirroring the repo's relative paths, so the EXISTING local-path indexing
    logic (KnowledgeGraph.build_from_repo, the chunking walk below) can run
    completely unchanged against it — deliberately reusing the same code
    path scripts/index_repo.py uses for a real checkout, rather than
    maintaining a second "index from remote content" pipeline that could
    silently drift from the real one over time.
    """
    tmp_dir = tempfile.mkdtemp(prefix="kg_rebuild_")
    try:
        tree = get_repo_tree(repo, ref, token)
        fetched = 0
        for entry in tree:
            path = entry["path"]
            if not path.endswith(INDEXABLE_EXTENSIONS) or _is_excluded(path):
                continue
            try:
                content = get_full_file_content(repo, path, ref, token)
            except Exception as e:
                print(f"  [reindex] skipping {path}: {type(e).__name__}: {e}")
                continue
            local_path = os.path.join(tmp_dir, path)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(content)
            fetched += 1
        print(f"  [reindex] fetched {fetched} file(s) at {ref[:7]} for {repo}")
        yield tmp_dir
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def index_repo_from_local_path(path: str, repo: str, kg_output: str | None = None) -> dict:
    """
    Shared core: build the knowledge graph + chunk/embed/index into Qdrant
    from a LOCAL directory. Used identically by scripts/index_repo.py (a
    real checkout on disk) and rebuild_index_for_repo below (a temp
    directory populated from GitHub via _fetch_repo_snapshot) — one
    indexing code path, two different ways of getting files onto disk first.
    """
    kg_output = kg_output or kg_path_for_repo(repo)

    kg = KnowledgeGraph()
    kg.build_from_repo(path)
    kg.save(kg_output)

    all_chunks = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for fname in files:
            if not fname.endswith(INDEXABLE_EXTENSIONS):
                continue
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, path).replace(os.sep, "/")
            try:
                source = open(full_path, encoding="utf-8").read()
            except UnicodeDecodeError:
                continue
            all_chunks.extend(chunk_file(rel_path, source))

    indexer = CodebaseIndexer()
    indexer.delete_repo(repo)  # full rebuild — clear stale points first
    indexer.index_chunks(all_chunks, repo=repo)

    return {"functions": len(kg.nodes), "chunks": len(all_chunks)}


def rebuild_index_for_repo(repo: str, ref: str, token: str) -> dict:
    """
    Entry point for the push-webhook trigger (gap #6): fetches the repo's
    file tree at `ref` from GitHub directly, no local clone needed, then
    runs it through the same indexing logic as the CLI script.
    """
    with _fetch_repo_snapshot(repo, ref, token) as tmp_dir:
        return index_repo_from_local_path(tmp_dir, repo)