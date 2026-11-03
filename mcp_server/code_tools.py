"""search_code (Qdrant) and the call-graph tools (knowledge graph from the
local clone)."""
import difflib
import threading

from rag.indexer import CodebaseIndexer

from mcp_server.errors import ToolInputError
from mcp_server.repos import get_graph, require_repo

MAX_SEARCH_RESULTS = 20
EXCERPT_CHARS = 600
SOURCE_CHARS = 4000
MAX_MATCHES = 10   # definitions returned for one name
MAX_EDGES = 50     # callers or callees listed per definition

CALL_GRAPH_NOTE = ("Static, name-based call graph (Python only): a call through another object "
                   "(obj.save()) links to every function with that name.")

_indexer: CodebaseIndexer | None = None
_indexer_lock = threading.Lock()


def get_indexer() -> CodebaseIndexer:
    """One CodebaseIndexer (Qdrant client + embedder) per process."""
    global _indexer
    with _indexer_lock:
        if _indexer is None:
            _indexer = CodebaseIndexer()
        return _indexer


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + " …"


def search_code(repo: str, query: str, limit: int = 5) -> dict:
    name = require_repo(repo)
    if not query.strip():
        raise ToolInputError("query is empty.")
    limit = max(1, min(int(limit), MAX_SEARCH_RESULTS))
    indexer = get_indexer()
    hits = indexer.search_scored(query, repo=name, limit=limit)
    results = [{
        "file": p.get("file_path"),
        "name": p.get("name"),
        "type": p.get("chunk_type"),
        "lines": f"{p.get('start_line')}-{p.get('end_line')}",
        "score": round(score, 4) if score is not None else None,
        "excerpt": _truncate(p.get("source", ""), EXCERPT_CHARS),
    } for p, score in hits]
    out = {"repo": name, "collection": indexer.collection, "results": results}
    if not results:
        out["note"] = (f"No chunks for {name} in Qdrant collection {indexer.collection}. search_code needs "
                       f"the repo indexed with the same embedding model ({indexer.embedder.name}); "
                       f"index it with scripts/index_repo.py.")
    return out


def _matches(repo: str, name: str, refresh: bool):
    graph = get_graph(repo, refresh=refresh)
    keys = graph.find(name)
    if not keys:
        known = sorted({n.name for n in graph.kg.nodes.values()})
        close = difflib.get_close_matches(name.rpartition(".")[2].rpartition("::")[2], known, n=5)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise ToolInputError(f"Function {name!r} not found in {graph.repo} "
                             f"({len(graph.kg.nodes)} functions from {graph.root}).{hint}")
    return graph, keys


def get_function(repo: str, name: str, refresh: bool = False) -> dict:
    graph, keys = _matches(repo, name, refresh)
    matches = []
    for key in keys[:MAX_MATCHES]:
        source, truncated = graph.source(key, SOURCE_CHARS)
        matches.append({**graph.describe(key), "source": source, "source_truncated": truncated})
    return {"repo": graph.repo, "matches": matches, "total_matches": len(keys)}


def _call_edges(repo: str, name: str, direction: str, refresh: bool) -> dict:
    """direction "callers" (who calls name) or "callees" (what name calls)."""
    graph, keys = _matches(repo, name, refresh)
    matches = []
    for key in keys[:MAX_MATCHES]:
        node = graph.kg.nodes[key]
        linked = [k for k in dict.fromkeys(node.called_by if direction == "callers" else node.calls)
                  if k in graph.kg.nodes]
        edges = []
        for other in linked[:MAX_EDGES]:
            caller, called = (other, key) if direction == "callers" else (key, other)
            edges.append({**graph.describe(other), "call_lines": graph.lines_calling(caller, called)})
        matches.append({"function": graph.describe(key), direction: edges, "count": len(linked),
                        "truncated": len(linked) > MAX_EDGES})
    return {"repo": graph.repo, "matches": matches, "note": CALL_GRAPH_NOTE}


def get_callers(repo: str, name: str, refresh: bool = False) -> dict:
    return _call_edges(repo, name, "callers", refresh)


def get_callees(repo: str, name: str, refresh: bool = False) -> dict:
    return _call_edges(repo, name, "callees", refresh)
