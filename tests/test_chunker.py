"""Python chunking for the vector index (rag/chunker.py)."""
from pathlib import Path

from rag.chunker import chunk_file

SOURCE = '''
import os

pick = lambda xs: xs[0] if xs else None


def top(a):
    def nested():
        return a
    return nested() if a else (lambda: None)()


class Box:
    value = 1 if os.name else 2

    def get(self):
        return (lambda: self.value)()


if os.name:
    def only_on_some_platforms():
        return 1
'''


def test_only_top_level_functions_and_classes_become_chunks():
    chunks = chunk_file("m.py", SOURCE)
    assert [(c.chunk_type, c.name) for c in chunks] == [("function", "top"), ("class", "Box")]


def test_lambdas_and_conditional_expressions_dont_crash_chunking():
    # Regression: this file made the push-to-main reindex fail with
    # "TypeError: argument of type 'Name' is not iterable".
    path = Path(__file__).resolve().parents[1] / "rag" / "knowledge_graph.py"
    chunks = chunk_file("rag/knowledge_graph.py", path.read_text())
    assert any(c.name == "KnowledgeGraph" for c in chunks)