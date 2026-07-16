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
    class_name: str | None = None  # None for module-level functions


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

    @staticmethod
    def _iter_functions_with_class(tree: ast.AST):
        """
        Yields (function_node, enclosing_class_name_or_None) for every
        function/async function def in the tree, tracking the innermost
        enclosing class. This is what lets self.foo() calls be scoped to
        the caller's own class instead of matching every function named
        "foo" anywhere in the repo — see the name-collision note on
        _extract_calls below.
        """
        results = []

        class _Visitor(ast.NodeVisitor):
            def __init__(self):
                self.class_stack: list[str] = []

            def visit_ClassDef(self, node):
                self.class_stack.append(node.name)
                self.generic_visit(node)
                self.class_stack.pop()

            def _visit_fn(self, node):
                current_class = self.class_stack[-1] if self.class_stack else None
                results.append((node, current_class))
                self.generic_visit(node)

            def visit_FunctionDef(self, node):
                self._visit_fn(node)

            def visit_AsyncFunctionDef(self, node):
                self._visit_fn(node)

        _Visitor().visit(tree)
        return results

    def _register_functions(self, file_path: str, repo_path: str) -> None:
        rel_path = os.path.relpath(file_path, repo_path).replace(os.sep, "/")
        try:
            source = open(file_path, encoding="utf-8").read()
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            return

        for node, class_name in self._iter_functions_with_class(tree):
            fn_key = f"{rel_path}::{node.name}"
            # NOTE — known, still-open caveat, distinct from the call-scoping
            # fix below: fn_key is file+name only, not file+class+name. Two
            # DIFFERENT classes with a same-named method in the SAME FILE
            # will collide here and silently overwrite each other in
            # self.nodes. Not fixed in this pass — changing fn_key's format
            # would also require updating rag/retriever.py's
            # find_fn_key_by_name_and_file (and re-indexing), which is a
            # wider-blast-radius change than the call-resolution accuracy
            # fix this pass is scoped to. Flagging explicitly rather than
            # silently leaving it undocumented.
            self.nodes[fn_key] = FunctionNode(
                fn_key=fn_key, name=node.name, file_path=rel_path, class_name=class_name,
            )
            self._name_index.setdefault(node.name, []).append(fn_key)

    def _extract_calls(self, file_path: str, repo_path: str) -> None:
        rel_path = os.path.relpath(file_path, repo_path).replace(os.sep, "/")
        try:
            source = open(file_path, encoding="utf-8").read()
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            return

        imports = [n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)]

        for node, _class_name in self._iter_functions_with_class(tree):
            caller_key = f"{rel_path}::{node.name}"
            if caller_key not in self.nodes:
                continue
            caller_node = self.nodes[caller_key]
            caller_node.imports = imports
            caller_class = caller_node.class_name

            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue

                called_name = None
                is_self_call = False
                if isinstance(sub.func, ast.Name):
                    called_name = sub.func.id
                elif isinstance(sub.func, ast.Attribute):
                    called_name = sub.func.attr
                    if isinstance(sub.func.value, ast.Name) and sub.func.value.id == "self":
                        is_self_call = True

                if called_name is None:
                    continue

                candidates = self._name_index.get(called_name, [])

                # Name-collision fix: without this, self.save() would link
                # to EVERY function named "save" anywhere in the repo,
                # including unrelated classes' save() methods. Scope to the
                # caller's own class when possible. Falls back to the old
                # unscoped match if no same-class candidate exists (e.g. an
                # inherited method defined on a base class elsewhere) — a
                # missing edge is safer than a wrong one, but we'd still
                # rather show something than nothing when we can't
                # disambiguate further. Calls through other objects
                # (obj.method(), not self.method()) stay unscoped, same as
                # before — genuinely needs type inference to fix properly,
                # out of scope here.
                if is_self_call and caller_class is not None:
                    same_class = [
                        key for key in candidates
                        if self.nodes[key].class_name == caller_class
                    ]
                    if same_class:
                        candidates = same_class

                for candidate_key in candidates:
                    if candidate_key == caller_key:
                        continue
                    caller_node.calls.append(candidate_key)
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