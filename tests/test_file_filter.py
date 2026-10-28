import asyncio
from unittest.mock import MagicMock

import pytest

import api.webhook as webhook
import tools.model_client as mc
from agents.documentation_agent import run_documentation_agent
from models.findings import AgentReport, ReviewReport
from pipeline.file_filter import REASON_LIMIT, file_priority, plan_review_files
from pipeline.job_store import get_job, try_start_job
from pipeline.runner import run_agents_parallel
from pipeline.synthesizer import synthesize_markdown
from rag.knowledge_graph import KnowledgeGraph
from rag.retriever import SmartRetriever
from tools.model_client import count_llm_calls

PATCH = "@@ -1 +1 @@\n-x = 1\n+x = 2\n"


def f(name, patch=PATCH, status="modified"):
    entry = {"filename": name, "status": status}
    if patch is not None:
        entry["patch"] = patch
    return entry


def reasons(plan):
    return {s.filename: s.reason for s in plan.skipped}


# --- Skip rules ----------------------------------------------------------------

@pytest.mark.parametrize("name, pattern", [
    ("package-lock.json", "package-lock.json"),
    ("dashboard/package-lock.json", "package-lock.json"),
    ("yarn.lock", "yarn.lock"),
    ("pnpm-lock.yaml", "pnpm-lock.yaml"),
    ("poetry.lock", "poetry.lock"),
    ("Cargo.lock", "*.lock"),
])
def test_lockfiles_are_skipped(name, pattern):
    assert reasons(plan_review_files([f(name)])) == {name: f"skip pattern {pattern!r}"}


@pytest.mark.parametrize("name, pattern", [
    ("dist/app.js", "dist/"),
    ("dashboard/dist/assets/index.js", "dist/"),
    ("build/lib/x.py", "build/"),
    ("web/node_modules/react/index.js", "node_modules/"),
    ("static/vendor/jquery.min.js", "*.min.js"),
    ("static/app.js.map", "*.map"),
])
def test_generated_and_vendored_paths_are_skipped(name, pattern):
    assert reasons(plan_review_files([f(name)])) == {name: f"skip pattern {pattern!r}"}


def test_directory_patterns_match_whole_path_segments_only():
    plan = plan_review_files([f("distribution/app.py"), f("src/builder.py"), f("rebuild/x.py")])
    assert plan.skipped == []


@pytest.mark.parametrize("name", ["LICENSE", "LICENSE.md", "LICENSE.txt"])
def test_license_is_skipped(name):
    assert name in reasons(plan_review_files([f(name)]))


def test_removed_files_are_skipped():
    assert reasons(plan_review_files([f("old.py", status="removed")])) == {"old.py": "removed"}


def test_binary_or_patchless_files_are_skipped():
    plan = plan_review_files([f("logo.png", patch=None), f("empty.py", patch="")])
    assert reasons(plan) == {"logo.png": "binary or no diff", "empty.py": "binary or no diff"}


def test_patch_over_size_limit_is_skipped(monkeypatch, settings):
    monkeypatch.setattr(settings, "MAX_PATCH_CHARS", 100, raising=False)
    plan = plan_review_files([f("big.py", patch="+" * 101), f("ok.py", patch="+" * 100)])
    assert reasons(plan) == {"big.py": "patch too large (101 chars > 100)"}
    assert [t.filename for t in plan.to_review] == ["ok.py"]


def test_default_patch_limit_is_20000():
    plan = plan_review_files([f("big.py", patch="+" * 20001), f("ok.py", patch="+" * 20000)])
    assert reasons(plan) == {"big.py": "patch too large (20,001 chars > 20,000)"}


def test_skip_patterns_come_from_settings(monkeypatch, settings):
    monkeypatch.setattr(settings, "REVIEW_SKIP_PATTERNS", ["migrations/"], raising=False)
    plan = plan_review_files([f("app/migrations/0001.py"), f("yarn.lock")])
    assert reasons(plan) == {"app/migrations/0001.py": "skip pattern 'migrations/'"}
    assert [t.filename for t in plan.to_review] == ["yarn.lock"]


def test_skipped_files_keep_pr_order():
    plan = plan_review_files([f("b.lock"), f("a.png", patch=None), f("c.py", status="removed")])
    assert [s.filename for s in plan.skipped] == ["b.lock", "a.png", "c.py"]


# --- Docs-only routing ---------------------------------------------------------

@pytest.mark.parametrize("name, docs_only", [
    ("README.md", True), ("docs/guide.rst", True), ("notes.TXT", True),
    ("app.py", False), ("config.yaml", False), ("index.js", False),
])
def test_docs_files_are_marked_docs_only(name, docs_only):
    [target] = plan_review_files([f(name)]).to_review
    assert target.docs_only is docs_only


# --- Priority and file limit ----------------------------------------------------

def test_priority_is_python_then_code_then_config_then_docs():
    files = [f("README.md"), f("settings.yaml"), f("ui.tsx"), f("Dockerfile"), f("b.py"), f("a.py"), f("CHANGES.rst")]
    order = [t.filename for t in plan_review_files(files).to_review]
    assert order == ["b.py", "a.py", "ui.tsx", "settings.yaml", "Dockerfile", "README.md", "CHANGES.rst"]
    assert [file_priority(n) for n in order] == [0, 0, 1, 2, 2, 3, 3]


def test_file_limit_keeps_highest_priority_and_lists_the_rest(monkeypatch, settings):
    monkeypatch.setattr(settings, "MAX_FILES_PER_REVIEW", 2, raising=False)
    files = [f("README.md"), f("a.py"), f("config.json"), f("b.py"), f("yarn.lock")]

    plan = plan_review_files(files)

    assert [t.filename for t in plan.to_review] == ["a.py", "b.py"]
    assert reasons(plan) == {
        "yarn.lock": "skip pattern 'yarn.lock'",
        "config.json": REASON_LIMIT,
        "README.md": REASON_LIMIT,
    }


def test_default_file_limit_is_25():
    plan = plan_review_files([f(f"m{i:02}.py") for i in range(30)])
    assert len(plan.to_review) == 25
    assert [s.filename for s in plan.skipped] == [f"m{i:02}.py" for i in range(25, 30)]
    assert {s.reason for s in plan.skipped} == {REASON_LIMIT}


# --- PR comment ----------------------------------------------------------------

def test_comment_lists_skipped_files_in_a_collapsed_block():
    review = ReviewReport(pr_number=1, repo="o/r", commit_sha="abc", skipped_files=[
        {"filename": "yarn.lock", "reason": "skip pattern 'yarn.lock'"},
        {"filename": "big.py", "reason": REASON_LIMIT},
    ])
    md = synthesize_markdown(review)
    block = md[md.index("<details>"):]
    assert "<summary>2 file(s) not reviewed</summary>" in block
    assert "- `yarn.lock` — skip pattern 'yarn.lock'" in block
    assert "- `big.py` — not reviewed (limit)" in block
    assert block.rstrip().endswith("</details>")


def test_comment_has_no_block_when_nothing_was_skipped():
    assert "<details>" not in synthesize_markdown(ReviewReport(pr_number=1, repo="o/r", commit_sha="abc"))


# --- Pipeline ------------------------------------------------------------------

def test_pipeline_reviews_only_planned_files_with_the_right_agents(monkeypatch, capsys):
    pr_files = [f("app.py"), f("README.md"), f("package-lock.json"), f("gone.py", status="removed"),
                f("logo.png", patch=None), f("LICENSE")]
    github = MagicMock()
    github.get_pr_files.return_value = pr_files
    github.get_pr_diff.return_value = PATCH
    for name in ("get_installation_token", "get_pr_files", "get_pr_diff", "set_commit_status", "post_pr_review"):
        monkeypatch.setattr(webhook, name, getattr(github, name))
    indexer = MagicMock()
    indexer.search.return_value = []
    monkeypatch.setattr(webhook, "_load_retriever", lambda repo: SmartRetriever(KnowledgeGraph(), indexer))

    runs = []

    async def fake_agents(changed_file, diff_snippet, retrieved_context, on_agent_complete=None, agent_functions=None):
        runs.append((changed_file, agent_functions))
        return [AgentReport(agent_name="x")]
    monkeypatch.setattr(webhook, "run_agents_parallel", fake_agents)

    try_start_job("o/r", 1, "sha1")
    asyncio.run(webhook.run_review_pipeline("o/r", 1, "sha1"))

    assert runs == [("app.py", None), ("README.md", [run_documentation_agent])]
    review = ReviewReport.model_validate_json(get_job("o/r", "sha1")["result_json"])
    assert [s["filename"] for s in review.skipped_files] == ["package-lock.json", "gone.py", "logo.png", "LICENSE"]
    assert "4 file(s) not reviewed" in github.post_pr_review.call_args.args[2]
    out = capsys.readouterr().out
    assert "reviewing 2 file(s), skipping 4" in out
    assert "[review_finished] repo=o/r pr=1 commit=sha1 status=complete" in out and "llm_calls=0" in out


# --- LLM call counting -----------------------------------------------------------

def test_llm_calls_from_agent_threads_are_counted_per_review(monkeypatch, settings):
    monkeypatch.setattr(settings, "GROQ_MODEL_REASONING", "openai/gpt-oss-120b")
    resp = MagicMock(status_code=200, headers={})
    resp.json.return_value = {"choices": [{"finish_reason": "stop", "message": {"content": "[]"}}], "usage": {}}
    monkeypatch.setattr(mc.requests, "post", MagicMock(return_value=resp))

    def agent(changed_file, diff_snippet, retrieved_context):
        mc.model_client.reason(system="s", user="u", task="security")
        mc.model_client.reason(system="s", user="u", task="security")
        return AgentReport(agent_name="fake")

    async def review():
        with count_llm_calls() as counter:
            await run_agents_parallel("a.py", PATCH, "", agent_functions=[agent, agent, agent])
            return counter.calls

    assert asyncio.run(review()) == 6
    mc.model_client.reason(system="s", user="u", task="security")  # outside any review: not counted, no error