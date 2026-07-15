# rag/chunker.py

import ast
from dataclasses import dataclass, field


@dataclass
class CodeChunk:
    chunk_type: str          # "function" | "class" | "module_fallback"
    name: str                 # function/class name
    file_path: str
    start_line: int
    end_line: int
    source: str                # the actual code text
    fn_key: str = field(default="")   # unique key: "file_path::name"

    def __post_init__(self):
        if not self.fn_key:
            self.fn_key = f"{self.file_path}::{self.name}"


def chunk_python_file(file_path: str, source: str) -> list[CodeChunk]:
    """
    Parses a Python file and returns one chunk per top-level function/class.
    Falls back to whole-file chunking if the file doesn't parse (e.g. syntax error,
    or genuinely not valid Python — shouldn't happen often but must not crash the indexer).
    """
    chunks: list[CodeChunk] = []
    lines = source.splitlines()

    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Fallback: treat the whole file as one chunk rather than skipping it silently.
        chunks.append(CodeChunk(
            chunk_type="module_fallback",
            name=file_path,
            file_path=file_path,
            start_line=1,
            end_line=len(lines),
            source=source,
        ))
        return chunks

    for node in ast.walk(tree):
        # Only top-level functions/classes — nested functions get pulled in as part
        # of their parent's source, which keeps context coherent (a helper function
        # inside another function isn't meaningful in isolation anyway).
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not _is_top_level(node, tree):
                continue
            start = node.lineno
            end = node.end_lineno or start
            snippet = "\n".join(lines[start - 1:end])
            chunk_type = "class" if isinstance(node, ast.ClassDef) else "function"
            chunks.append(CodeChunk(
                chunk_type=chunk_type,
                name=node.name,
                file_path=file_path,
                start_line=start,
                end_line=end,
                source=snippet,
            ))

    return chunks


def chunk_non_python_file(file_path: str, source: str, max_chars: int = 1500) -> list[CodeChunk]:
    """
    Fallback fixed-size chunking for non-Python files (JS, config files, etc).
    This is explicitly a lower-quality path — see gap #6: the knowledge graph
    only understands Python, so non-Python chunks get NO call-graph relationships,
    only vector search. Document this limitation, don't silently pretend it's equal.
    """
    lines = source.splitlines()
    chunks = []
    buffer, buffer_start = [], 1
    char_count = 0

    for i, line in enumerate(lines, start=1):
        buffer.append(line)
        char_count += len(line)
        if char_count >= max_chars:
            chunks.append(CodeChunk(
                chunk_type="module_fallback",
                name=f"{file_path}:{buffer_start}-{i}",
                file_path=file_path,
                start_line=buffer_start,
                end_line=i,
                source="\n".join(buffer),
            ))
            buffer, buffer_start, char_count = [], i + 1, 0

    if buffer:
        chunks.append(CodeChunk(
            chunk_type="module_fallback",
            name=f"{file_path}:{buffer_start}-{len(lines)}",
            file_path=file_path,
            start_line=buffer_start,
            end_line=len(lines),
            source="\n".join(buffer),
        ))
    return chunks


def _is_top_level(node: ast.AST, tree: ast.AST) -> bool:
    """Checks whether `node` is a direct child of the module (not nested inside another function/class)."""
    for parent in ast.walk(tree):
        if parent is node:
            continue
        if hasattr(parent, "body") and node in getattr(parent, "body", []):
            return isinstance(parent, ast.Module)
    return False


def chunk_file(file_path: str, source: str) -> list[CodeChunk]:
    """Entry point — routes to the right chunker based on file extension."""
    if file_path.endswith(".py"):
        return chunk_python_file(file_path, source)
    return chunk_non_python_file(file_path, source)