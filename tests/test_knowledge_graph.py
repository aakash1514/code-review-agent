import textwrap

import pytest

from rag.knowledge_graph import KnowledgeGraph


def _build(tmp_path, files: dict[str, str]) -> KnowledgeGraph:
    repo = tmp_path / "repo"
    for name, source in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding="utf-8")
    kg = KnowledgeGraph()
    kg.build_from_repo(str(repo))
    return kg


ALPHA = """
    class Alpha:
        def run(self):
            return self.save()

        def save(self):
            return "alpha"
"""

BETA = """
    class Beta:
        def run(self):
            return self.save()

        def save(self):
            return "beta"
"""


def test_self_calls_stay_inside_their_own_class(tmp_path):
    kg = _build(tmp_path, {"alpha.py": ALPHA, "beta.py": BETA})

    assert kg.nodes["alpha.py::run"].calls == ["alpha.py::save"]
    assert kg.nodes["beta.py::run"].calls == ["beta.py::save"]
    assert kg.nodes["alpha.py::save"].called_by == ["alpha.py::run"]
    assert kg.nodes["beta.py::save"].called_by == ["beta.py::run"]


def test_non_self_attribute_calls_fall_back_to_every_same_named_function(tmp_path):
    kg = _build(tmp_path, {
        "alpha.py": ALPHA,
        "beta.py": BETA,
        "main.py": """
            def persist(obj):
                return obj.save()
        """,
    })

    assert sorted(kg.nodes["main.py::persist"].calls) == ["alpha.py::save", "beta.py::save"]


def test_self_call_with_no_same_class_candidate_falls_back_unscoped(tmp_path):
    # Inherited method: Child.run calls self.save(), defined only on Base.
    kg = _build(tmp_path, {
        "base.py": """
            class Base:
                def save(self):
                    return 1
        """,
        "child.py": """
            from base import Base

            class Child(Base):
                def run(self):
                    return self.save()
        """,
    })

    assert kg.nodes["child.py::run"].calls == ["base.py::save"]


def test_plain_function_calls_and_imports(tmp_path):
    kg = _build(tmp_path, {
        "util.py": """
            import os

            def helper():
                return 1

            def main():
                return helper() + helper()
        """,
    })

    main = kg.nodes["util.py::main"]
    assert main.calls == ["util.py::helper", "util.py::helper"]  # one edge per call site
    assert main.imports == ["os"]
    assert kg.get_dependents("util.py::helper") == ["util.py::main", "util.py::main"]


def test_unparseable_files_are_skipped(tmp_path):
    kg = _build(tmp_path, {"ok.py": "def fine():\n    pass\n", "broken.py": "def nope(:\n"})
    assert list(kg.nodes) == ["ok.py::fine"]


@pytest.mark.xfail(strict=True, reason=(
    "Known issue #1 (deferred): fn_key is file + function name, not file + "
    "class + name, so Alpha.save and Beta.save in the SAME file share one key "
    "and one overwrites the other. Fix needs a key format change, a "
    "retriever update and a reindex."
))
def test_same_named_methods_in_one_file_get_separate_nodes(tmp_path):
    kg = _build(tmp_path, {"models.py": ALPHA + BETA})

    save_nodes = [n for n in kg.nodes.values() if n.name == "save"]
    assert {n.class_name for n in save_nodes} == {"Alpha", "Beta"}
