import ast
import json
import os
import re
from dataclasses import dataclass, field, asdict


@dataclass
class FunctionNode:
    fn_key: str
    name: str
    file_path: str
    calls: list[str] = field(default_factory=list)
    called_by: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


def kg_path_for_repo(repo: str, base_dir: str = ".") -> str:
    """
    Deterministic, filesystem-safe path for a given repo's knowledge graph
    JSON file. Replaces the old hardcoded "knowledge_graph.json" — that
    single fixed path meant indexing a second repo silently overwrote the
    first repo's graph on disk (exactly what happened testing against
    code-review-agent-test: it clobbered the main project's own 28-function
    graph). "owner/name" -> "owner_name" to keep it a valid filename on
    Windows and Unix alike.
    """
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", repo)
    return os.path.join(base_dir, f"knowledge_graph_{safe}.json")


class KnowledgeGraph:
    def __init__(self):
        self.nodes: dict[str, FunctionNode] = {}
        self._name_index: dict[str, list[str]] = {}

    EXCLUDED_DIRS = {".git", "venv", "env", ".venv", "node_modules", "__pycache__", "dist", "build"}

    def build_from_repo(self, repo_path: str) -> None:
        self.nodes.clear()
        self._name_index.clear()

        py_files = []
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in self.EXCLUDED_DIRS]
            for f in files:
                if f.endswith(".py"):
                    py_files.append(os.path.join(root, f))

        print(f"  Found {len(py_files)} Python files to analyze")

        print("  Registering functions...")
        for file_path in py_files:
            self._register_functions(file_path, repo_path)

        print("  Extracting call relationships...")
        for file_path in py_files:
            self._extract_calls(file_path, repo_path)

    def _register_functions(self, file_path: str, repo_path: str) -> None:
        rel_path = os.path.relpath(file_path, repo_path).replace(os.sep, "/")
        try:
            source = open(file_path, encoding="utf-8").read()
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            return

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_key = f"{rel_path}::{node.name}"
                self.nodes[fn_key] = FunctionNode(fn_key=fn_key, name=node.name, file_path=rel_path)
                self._name_index.setdefault(node.name, []).append(fn_key)

    def _extract_calls(self, file_path: str, repo_path: str) -> None:
        rel_path = os.path.relpath(file_path, repo_path).replace(os.sep, "/")
        try:
            source = open(file_path, encoding="utf-8").read()
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            return

        imports = [n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)]

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            caller_key = f"{rel_path}::{node.name}"
            if caller_key not in self.nodes:
                continue
            self.nodes[caller_key].imports = imports

            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue

                called_name = None
                if isinstance(sub.func, ast.Name):
                    called_name = sub.func.id
                elif isinstance(sub.func, ast.Attribute):
                    called_name = sub.func.attr

                if called_name is None:
                    continue

                for candidate_key in self._name_index.get(called_name, []):
                    if candidate_key == caller_key:
                        continue
                    self.nodes[caller_key].calls.append(candidate_key)
                    self.nodes[candidate_key].called_by.append(caller_key)

    def get_call_chain(self, fn_key: str, depth: int = 3) -> list[str]:
        visited, frontier = {fn_key}, [fn_key]
        for _ in range(depth):
            next_frontier = []
            for key in frontier:
                node = self.nodes.get(key)
                if not node:
                    continue
                for called in node.calls:
                    if called not in visited:
                        visited.add(called)
                        next_frontier.append(called)
            frontier = next_frontier
        visited.discard(fn_key)
        return list(visited)

    def get_dependents(self, fn_key: str) -> list[str]:
        node = self.nodes.get(fn_key)
        return node.called_by if node else []

    def find_fn_key_by_name_and_file(self, file_path: str, name: str) -> str | None:
        candidate = f"{file_path}::{name}"
        return candidate if candidate in self.nodes else None

    def save(self, path: str = "knowledge_graph.json") -> None:
        """
        NOTE: default path kept for backward compatibility with existing
        callers, but real callers should now pass kg_path_for_repo(repo) —
        see scripts/index_repo.py — to avoid one repo's graph silently
        overwriting another's.
        """
        data = {k: asdict(v) for k, v in self.nodes.items()}
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def load(self, path: str = "knowledge_graph.json") -> None:
        with open(path) as f:
            data = json.load(f)
        self.nodes = {k: FunctionNode(**v) for k, v in data.items()}
        self._name_index.clear()
        for key, node in self.nodes.items():
            self._name_index.setdefault(node.name, []).append(key)