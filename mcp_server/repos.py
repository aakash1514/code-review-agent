"""The repos the MCP tools may touch (MCP_REPOS), and each one's call graph,
built lazily from its local clone and cached for the life of the process.

  MCP_REPOS  "owner/name=C:/path/to/clone" entries, separated by ";" or
             newlines. The path is optional ("owner/name" alone): search and
             the GitHub tools work without a clone, the call-graph tools don't.

A repo that isn't listed is refused, so the App's installation token is only
ever used on the repos the user chose.
"""
import ast
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from rag.knowledge_graph import KnowledgeGraph

from mcp_server.errors import ToolInputError

REPO_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def parse_repos(value: str) -> dict[str, str | None]:
    """{"owner/name": clone path or None} from an MCP_REPOS value."""
    repos: dict[str, str | None] = {}
    for entry in re.split(r"[;\r\n]+", value or ""):
        entry = entry.strip().strip("\"'")
        if not entry:
            continue
        name, _, path = entry.partition("=")
        name, path = name.strip(), path.strip().strip("\"'")
        if not REPO_NAME.match(name):
            raise ToolInputError(f"MCP_REPOS entry {entry!r}: expected owner/name or owner/name=<clone path>")
        repos[name] = path or None
    return repos


def configured_repos() -> dict[str, str | None]:
    return parse_repos(os.getenv("MCP_REPOS", ""))


def require_repo(repo: str) -> str:
    """repo's name as configured (GitHub names are case-insensitive), or a
    ToolInputError naming the repos that are configured."""
    repos = configured_repos()
    if not repos:
        raise ToolInputError("No repos configured: set MCP_REPOS, e.g. owner/name=C:/path/to/clone.")
    for name in repos:
        if name.lower() == (repo or "").strip().lower():
            return name
    raise ToolInputError(f"Unknown repo {repo!r}. Configured in MCP_REPOS: {', '.join(repos)}.")


def require_clone(repo: str) -> tuple[str, Path]:
    name = require_repo(repo)
    path = configured_repos()[name]
    if not path:
        raise ToolInputError(f"Repo {name} has no clone path in MCP_REPOS; the call-graph tools need one "
                             f"({name}=C:/path/to/clone).")
    root = Path(path).expanduser()
    if not root.is_dir():
        raise ToolInputError(f"Clone path for {name} does not exist or is not a directory: {root}")
    return name, root


def _called_name(call: ast.Call) -> str | None:
    """The name a call resolves on, as KnowledgeGraph._extract_calls reads it."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


@dataclass
class RepoGraph:
    """A repo's KnowledgeGraph plus what it doesn't keep: each function's
    lines and the lines of its calls, from a second AST pass over the same
    files (same fn_keys; last definition wins, as in the graph)."""
    repo: str
    root: Path
    kg: KnowledgeGraph
    spans: dict[str, tuple[str | None, int, int]] = field(default_factory=dict)  # fn_key -> (class, start, end)
    call_lines: dict[str, list[tuple[str, int]]] = field(default_factory=dict)   # fn_key -> [(called name, line)]
    built_at: float = 0.0

    def index_lines(self) -> None:
        for dirpath, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in KnowledgeGraph.EXCLUDED_DIRS]
            for f in files:
                if f.endswith(".py"):
                    self._index_file(Path(dirpath) / f)

    def _index_file(self, path: Path) -> None:
        rel = path.relative_to(self.root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            return
        for node, class_name in KnowledgeGraph._iter_functions_with_class(tree):
            key = f"{rel}::{node.name}"
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            self.spans[key] = (class_name, start, node.end_lineno or node.lineno)
            self.call_lines[key] = [(name, sub.lineno) for sub in ast.walk(node)
                                    if isinstance(sub, ast.Call) and (name := _called_name(sub))]

    def find(self, name: str) -> list[str]:
        """fn_keys for "name", "Class.name" or "path/file.py::name"."""
        name = name.strip()
        if "::" in name:
            return [name] if name in self.kg.nodes else []
        cls, _, fn = name.rpartition(".")
        return sorted(k for k, n in self.kg.nodes.items()
                      if n.name == fn and (not cls or n.class_name == cls))

    def describe(self, fn_key: str) -> dict:
        node = self.kg.nodes[fn_key]
        class_name, start, end = self.spans.get(fn_key, (node.class_name, 0, 0))
        return {"name": node.name, "class": class_name, "file": node.file_path,
                "line_start": start, "line_end": end}

    def source(self, fn_key: str, max_chars: int) -> tuple[str, bool]:
        """The function's source and whether it was cut at max_chars."""
        node = self.kg.nodes[fn_key]
        _, start, end = self.spans.get(fn_key, (None, 0, 0))
        if not start:
            return "", False
        lines = (self.root / node.file_path).read_text(encoding="utf-8").splitlines()
        text = "\n".join(lines[start - 1:end])
        return (text, False) if len(text) <= max_chars else (text[:max_chars] + "\n# ... (truncated)", True)

    def lines_calling(self, caller_key: str, called_key: str) -> list[int]:
        called = self.kg.nodes[called_key].name
        return sorted({line for name, line in self.call_lines.get(caller_key, []) if name == called})


_graphs: dict[str, RepoGraph] = {}
_graphs_lock = threading.Lock()


def get_graph(repo: str, refresh: bool = False) -> RepoGraph:
    """The repo's call graph, built from its clone on first use (in memory
    only: nothing is written to disk or Qdrant). refresh=True rebuilds it,
    e.g. after pulling new commits."""
    name, root = require_clone(repo)
    with _graphs_lock:
        graph = _graphs.get(name)
        if graph is None or refresh or graph.root != root:
            kg = KnowledgeGraph()
            kg.build_from_repo(str(root))
            graph = RepoGraph(repo=name, root=root, kg=kg, built_at=time.time())
            graph.index_lines()
            _graphs[name] = graph
            print(f"[mcp] call graph for {name}: {len(kg.nodes)} functions from {root}")
        return graph


def repo_for_file(file_path: str) -> tuple[str | None, str]:
    """(configured repo, path relative to its clone) for a file inside a
    clone: an absolute path under the clone root, or a relative path that
    exists in a clone (first match in MCP_REPOS order). (None, file_path)
    otherwise."""
    path = file_path.strip().replace("\\", "/")
    clones = [(name, Path(p).expanduser()) for name, p in configured_repos().items() if p]
    candidate = Path(path)
    for name, root in clones:
        if candidate.is_absolute():
            try:
                return name, candidate.resolve().relative_to(root.resolve()).as_posix()
            except (ValueError, OSError):
                continue
        elif (root / path).is_file():
            return name, path.removeprefix("./")
    return None, path.removeprefix("./")
