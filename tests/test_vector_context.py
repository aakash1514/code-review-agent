"""Vector search (embeddings + Qdrant) is optional context for a review but
required for a reindex: a review carries on without it and says so, while a
reindex fails loudly."""
import asyncio
import json
import textwrap
from unittest.mock import MagicMock

import pytest

import api.webhook as webhook
import rag.indexer
from models.findings import AgentReport, ReviewReport
from pipeline.job_store import get_job, try_start_job
from pipeline.reindexer import index_repo_from_local_path
from pipeline.synthesizer import synthesize_markdown
from rag.knowledge_graph import KnowledgeGraph
from rag.retriever import SmartRetriever, short_error

REPO = "octo/repo"
SHA = "d" * 40
PATCH = "@@ -0,0 +1,5 @@\n+def helper():\n+    return 1\n+\n+def main():\n+    return helper()\n"
CREDITS_ERROR = RuntimeError("402 Client Error: Payment Required\nYou have no remaining credits.")


def _failing_indexer():
    indexer = MagicMock()
    indexer.search.side_effect = CREDITS_ERROR
    return indexer


def _graph(tmp_path) -> KnowledgeGraph:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(textwrap.dedent("""
        def helper():
            return 1

        def main():
            return helper()
    """), encoding="utf-8")
    kg = KnowledgeGraph()
    kg.build_from_repo(str(repo))
    return kg


def test_short_error_is_one_line_with_type():
    assert short_error(CREDITS_ERROR) == "RuntimeError: 402 Client Error: Payment Required"
    assert short_error(ValueError()) == "ValueError"
    assert len(short_error(ValueError("x" * 500))) == 160


def test_retriever_keeps_call_graph_context_when_vector_search_fails(tmp_path):
    retriever = SmartRetriever(_graph(tmp_path), _failing_indexer())

    context = retriever.retrieve_for_diff(REPO, "app.py", PATCH, ["helper", "main"])

    assert context["similar_patterns"] == []
    assert context["vector_context_error"] == "RuntimeError: 402 Client Error: Payment Required"
    assert {c["fn_key"] for c in context["call_chain"]} == {"app.py::helper"}
    assert {d["fn_key"] for d in context["dependents"]} == {"app.py::main"}
    assert "Called by" in retriever.build_agent_context_string(context)


def test_retriever_without_an_indexer_reports_why(tmp_path):
    retriever = SmartRetriever(KnowledgeGraph(), None, indexer_error="ConnectionError: qdrant down")
    context = retriever.retrieve_for_diff(REPO, "app.py", PATCH, [])
    assert context["vector_context_error"] == "ConnectionError: qdrant down"


def test_load_retriever_survives_indexer_construction_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no knowledge graph file here either

    def broken_indexer():
        raise ConnectionError("qdrant unreachable")
    monkeypatch.setattr(webhook, "CodebaseIndexer", broken_indexer)

    retriever = webhook._load_retriever(REPO)

    context = retriever.retrieve_for_diff(REPO, "app.py", PATCH, [])
    assert context["vector_context_error"] == "ConnectionError: qdrant unreachable"


def test_review_completes_with_a_warning_when_embedding_fails(monkeypatch, tmp_path):
    github = MagicMock()
    github.get_installation_token.return_value = "installation-token"
    github.get_pr_files.return_value = [{"filename": "app.py", "patch": PATCH},
                                        {"filename": "other.py", "patch": PATCH}]
    github.get_pr_diff.return_value = PATCH
    for name, mock in [("get_installation_token", github.get_installation_token),
                       ("get_pr_files", github.get_pr_files), ("get_pr_diff", github.get_pr_diff),
                       ("set_commit_status", github.set_commit_status), ("post_pr_review", github.post_pr_review)]:
        monkeypatch.setattr(webhook, name, mock)
    monkeypatch.setattr(webhook, "_load_retriever", lambda repo: SmartRetriever(_graph(tmp_path), _failing_indexer()))

    agent_contexts = []

    async def fake_agents(changed_file, diff_snippet, retrieved_context, on_agent_complete=None):
        agent_contexts.append(retrieved_context)
        return [AgentReport(agent_name="security", summary="ok")]
    monkeypatch.setattr(webhook, "run_agents_parallel", fake_agents)

    try_start_job(REPO, 7, SHA)
    asyncio.run(webhook.run_review_pipeline(REPO, 7, SHA))

    job = get_job(REPO, SHA)
    assert job["status"] == "complete", job["error"]
    review = ReviewReport.model_validate_json(job["result_json"])
    # One warning per distinct reason, even though both files hit it.
    assert review.warnings == ["vector context unavailable: RuntimeError: 402 Client Error: Payment Required"]
    assert len(agent_contexts) == 2  # the agents still ran, for every file
    comment = github.post_pr_review.call_args.args[2]
    assert "vector context unavailable: RuntimeError: 402" in comment
    assert github.set_commit_status.call_args_list[-1].args[2] == "success"


def test_comment_has_no_warning_block_when_vector_context_worked():
    review = ReviewReport(pr_number=1, repo=REPO, commit_sha=SHA)
    assert "vector context" not in synthesize_markdown(review)


def test_reindex_still_fails_loudly_when_embedding_fails(monkeypatch, tmp_path):
    repo_dir = tmp_path / "checkout"
    repo_dir.mkdir()
    (repo_dir / "app.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(rag.indexer, "QdrantClient", MagicMock())

    def no_credits(self, texts):
        raise CREDITS_ERROR
    monkeypatch.setattr(rag.indexer.CodebaseIndexer, "_embed", no_credits)

    with pytest.raises(RuntimeError, match="402"):
        index_repo_from_local_path(str(repo_dir), REPO, kg_output=str(tmp_path / "kg.json"))


def test_stored_review_json_keeps_warnings():
    review = ReviewReport(pr_number=1, repo=REPO, commit_sha=SHA, warnings=["vector context unavailable: x"])
    assert json.loads(review.model_dump_json())["warnings"] == ["vector context unavailable: x"]
