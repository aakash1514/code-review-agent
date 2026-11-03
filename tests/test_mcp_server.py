"""The MCP server (mcp_server/): tool listing, each tool with mocks, the
error paths, review_patch through the real agents with a fake LLM, and
stdout isolation over a real stdio subprocess."""
import asyncio
import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest
import requests
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

import mcp_server.code_tools as code_tools
import mcp_server.github_reviews as github_reviews
import mcp_server.repos as repos
import mcp_server.review as review
from mcp_server.errors import ToolInputError
from mcp_server.server import TOOL_NAMES, build_server
from tools import model_client as mc

ROOT = Path(__file__).resolve().parent.parent
REPO = "octo/app"

DB_PY = '''\
def run_query(sql):
    return sql


class Store:
    def validate(self):
        return True

    @staticmethod
    def helper():
        return 1

    def save(self):
        self.validate()
        return run_query("INSERT")
'''

USERS_PY = '''\
from app.db import run_query


def get_user(name):
    """Looks a user up."""
    return run_query(name)


def list_users():
    rows = run_query("SELECT")
    return [get_user(r) for r in rows] + [run_query("x")]
'''


def _write_repo(root: Path) -> Path:
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("")
    (root / "app" / "db.py").write_text(DB_PY)
    (root / "app" / "users.py").write_text(USERS_PY)
    return root


@pytest.fixture
def clone(tmp_path, monkeypatch):
    """A tiny Git-less repo, configured as octo/app, plus octo/nopath without a clone."""
    root = _write_repo(tmp_path / "clone")
    monkeypatch.setenv("MCP_REPOS", f"{REPO}={root.as_posix()};\nocto/nopath")
    monkeypatch.setattr(repos, "_graphs", {})
    monkeypatch.setattr(code_tools, "_indexer", None)
    monkeypatch.setattr(github_reviews, "_bot_login", None)
    return root


def call(name: str, args: dict):
    """Calls a tool through an in-memory MCP client session."""
    async def _go():
        async with Client(build_server()) as client:
            return await client.call_tool(name, args)
    return asyncio.run(_go())


def ok(name: str, args: dict) -> dict:
    result = call(name, args)
    assert not result.is_error, result.content[0].text
    return result.structured_content


def error_text(name: str, args: dict) -> str:
    result = call(name, args)
    assert result.is_error
    text = result.content[0].text
    assert "Traceback" not in text
    return text


# --- listing -------------------------------------------------------------------------

def test_lists_exactly_the_seven_tools_with_descriptions():
    async def _go():
        async with Client(build_server()) as client:
            return (await client.list_tools()).tools
    tools = asyncio.run(_go())
    assert sorted(t.name for t in tools) == sorted(TOOL_NAMES)
    assert len(tools) == 7
    for t in tools:
        assert len(t.description) > 60, t.name
        assert t.annotations.read_only_hint is True, t.name
        for prop in t.input_schema["properties"].values():
            assert prop.get("description"), t.name
    schemas = {t.name: t.input_schema for t in tools}
    assert schemas["review_patch"]["required"] == ["file_path", "patch"]
    assert schemas["search_code"]["properties"]["limit"]["default"] == 5


# --- MCP_REPOS -------------------------------------------------------------------------

def test_parse_repos_accepts_semicolons_newlines_and_bare_names():
    parsed = repos.parse_repos('a/b=C:/x/b; "c/d=C:/y/d"\r\nE/f\n\n')
    assert parsed == {"a/b": "C:/x/b", "c/d": "C:/y/d", "E/f": None}


def test_parse_repos_rejects_a_bad_entry():
    with pytest.raises(ToolInputError, match="expected owner/name"):
        repos.parse_repos("not-a-repo=C:/x")


def test_repo_names_match_case_insensitively(clone):
    assert repos.require_repo("OCTO/App") == REPO


# --- search_code -------------------------------------------------------------------------

class FakeIndexer:
    collection = "codebase_titan512"

    class embedder:
        name = "bedrock"

    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def search_scored(self, query, repo, limit):
        self.calls.append((query, repo, limit))
        return self.hits[:limit]


def test_search_code_returns_scored_truncated_hits(clone, monkeypatch):
    hit = {"file_path": "agents/security_agent.py", "name": "_floor", "chunk_type": "function",
           "start_line": 280, "end_line": 289, "source": "x" * 2000}
    fake = FakeIndexer([(hit, 0.61234), (hit, 0.5)])
    monkeypatch.setattr(code_tools, "_indexer", fake)
    out = ok("search_code", {"repo": "octo/APP", "query": "critical floor", "limit": 1})
    assert fake.calls == [("critical floor", REPO, 1)]
    [r] = out["results"]
    assert r == {"file": "agents/security_agent.py", "name": "_floor", "type": "function", "lines": "280-289",
                 "score": 0.6123, "excerpt": "x" * code_tools.EXCERPT_CHARS + " …"}
    assert out["collection"] == "codebase_titan512"


def test_search_code_says_when_the_repo_is_not_indexed(clone, monkeypatch):
    monkeypatch.setattr(code_tools, "_indexer", FakeIndexer([]))
    out = ok("search_code", {"repo": REPO, "query": "anything"})
    assert out["results"] == [] and "scripts/index_repo.py" in out["note"]


def test_search_code_without_aws_credentials_is_a_clear_error(clone, monkeypatch):
    from botocore.exceptions import NoCredentialsError

    class NoCreds(FakeIndexer):
        def search_scored(self, *a, **k):
            raise NoCredentialsError()
    monkeypatch.setattr(code_tools, "_indexer", NoCreds([]))
    text = error_text("search_code", {"repo": REPO, "query": "q"})
    assert "AWS credentials missing or expired" in text and "aws login --profile cra-admin" in text


def test_search_code_reports_qdrant_errors_in_one_line(clone, monkeypatch):
    class Broken(FakeIndexer):
        def search_scored(self, *a, **k):
            raise RuntimeError("qdrant exploded\nwith a second line")
    monkeypatch.setattr(code_tools, "_indexer", Broken([]))
    assert error_text("search_code", {"repo": REPO, "query": "q"}).endswith("RuntimeError: qdrant exploded")


def test_uses_one_real_indexer_per_process(clone, monkeypatch):
    made = []
    monkeypatch.setattr(code_tools, "CodebaseIndexer", lambda: made.append(1) or FakeIndexer([]))
    code_tools.get_indexer(), code_tools.get_indexer()
    assert made == [1]


# --- call graph -------------------------------------------------------------------------

def test_get_function_returns_file_class_lines_and_source(clone):
    out = ok("get_function", {"repo": REPO, "name": "Store.save"})
    [m] = out["matches"]
    assert (m["file"], m["class"], m["line_start"], m["line_end"]) == ("app/db.py", "Store", 13, 15)
    assert m["source"].startswith("    def save(self):") and "run_query" in m["source"]
    assert not m["source_truncated"]


def test_get_function_includes_decorators_and_accepts_exact_keys(clone):
    [m] = ok("get_function", {"repo": REPO, "name": "app/db.py::helper"})["matches"]
    assert m["line_start"] == 9 and m["source"].lstrip().startswith("@staticmethod")


def test_get_function_truncates_long_source(clone, monkeypatch):
    monkeypatch.setattr(code_tools, "SOURCE_CHARS", 20)
    [m] = ok("get_function", {"repo": REPO, "name": "get_user"})["matches"]
    assert m["source_truncated"] and m["source"].endswith("(truncated)")


def test_get_callers_lists_each_caller_with_call_lines(clone):
    out = ok("get_callers", {"repo": REPO, "name": "run_query"})
    [m] = out["matches"]
    assert m["function"]["file"] == "app/db.py"
    by_name = {c["name"]: c for c in m["callers"]}
    assert set(by_name) == {"save", "get_user", "list_users"}
    assert by_name["list_users"]["call_lines"] == [10, 11]
    assert by_name["get_user"]["file"] == "app/users.py" and by_name["get_user"]["line_start"] == 4
    assert m["count"] == 3 and not m["truncated"]
    assert "name-based" in out["note"]


def test_get_callees(clone):
    [m] = ok("get_callees", {"repo": REPO, "name": "list_users"})["matches"]
    assert {c["name"]: c["call_lines"] for c in m["callees"]} == {"run_query": [10, 11], "get_user": [11]}


def test_self_calls_resolve_to_the_same_class(clone):
    [m] = ok("get_callees", {"repo": REPO, "name": "save"})["matches"]
    assert {(c["name"], c["class"]) for c in m["callees"]} == {("validate", "Store"), ("run_query", None)}


def test_the_graph_is_cached_until_refresh(clone):
    ok("get_callers", {"repo": REPO, "name": "get_user"})
    (clone / "app" / "api.py").write_text("from app.users import get_user\n\ndef show():\n    return get_user('a')\n")
    assert ok("get_callers", {"repo": REPO, "name": "get_user"})["matches"][0]["count"] == 1  # cached
    [m] = ok("get_callers", {"repo": REPO, "name": "get_user", "refresh": True})["matches"]
    assert {c["name"] for c in m["callers"]} == {"list_users", "show"}


def test_function_not_found_suggests_close_names(clone):
    text = error_text("get_function", {"repo": REPO, "name": "get_usr"})
    assert "Function 'get_usr' not found in octo/app" in text and "Did you mean: get_user" in text


@pytest.mark.parametrize("tool", ["get_function", "get_callers", "get_callees"])
def test_unknown_repo_is_a_clear_error(clone, tool):
    text = error_text(tool, {"repo": "someone/else", "name": "f"})
    assert "Unknown repo 'someone/else'. Configured in MCP_REPOS: octo/app, octo/nopath." in text


def test_repo_without_clone_path_is_a_clear_error(clone):
    assert "Repo octo/nopath has no clone path in MCP_REPOS" in error_text(
        "get_callers", {"repo": "octo/nopath", "name": "f"})


def test_missing_clone_directory_is_a_clear_error(clone, monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_REPOS", f"{REPO}={(tmp_path / 'gone').as_posix()}")
    assert "does not exist" in error_text("get_function", {"repo": REPO, "name": "f"})


def test_no_repos_configured(monkeypatch):
    monkeypatch.delenv("MCP_REPOS", raising=False)
    assert "No repos configured: set MCP_REPOS" in error_text("list_reviewed_prs", {"repo": REPO})


# --- GitHub -------------------------------------------------------------------------

def _status(state, description="d", context="ai-code-review"):
    return {"context": context, "state": state, "description": description, "updated_at": "2026-11-01T00:00:00Z"}


@pytest.fixture
def github(monkeypatch):
    """Fakes for every GitHub call github_reviews makes; records the calls."""
    calls = []
    statuses = {"head1": [_status("failure", "1 critical issue(s): Possible prompt injection"),
                          _status("success", context="ci")],
                "head2": [_status("success", "No blocking issues found.")],
                "head3": []}

    def fake_pr(repo, n, token):
        calls.append(("pr", repo, n))
        return {"title": "Add thing", "state": "closed", "merged_at": "2026-11-01", "html_url": "https://x/pr/19",
                "head": {"sha": "head1"}}

    def fake_comments(repo, n, token):
        return [
            {"user": {"login": "cra-bot[bot]"}, "body": "old review", "created_at": "2026-10-01T00:00:00Z"},
            {"user": {"login": "someone"}, "body": "human comment", "created_at": "2026-10-03T00:00:00Z"},
            {"user": {"login": "cra-bot[bot]"}, "body": "## ⚠️ prompt injection quarantined", "html_url": "u",
             "created_at": "2026-10-02T00:00:00Z", "updated_at": "2026-10-02T00:00:00Z"},
        ]

    def fake_pulls(repo, state, limit, token):
        calls.append(("pulls", repo, state, limit))
        return [{"number": n, "title": f"PR {n}", "state": "open", "merged_at": None, "head": {"sha": f"head{n}"}}
                for n in (1, 2, 3)]

    monkeypatch.setattr(github_reviews, "get_installation_token", lambda: "tok")
    monkeypatch.setattr(github_reviews, "get_app", lambda: {"slug": "cra-bot"})
    monkeypatch.setattr(github_reviews, "get_pr", fake_pr)
    monkeypatch.setattr(github_reviews, "list_issue_comments", fake_comments)
    monkeypatch.setattr(github_reviews, "get_combined_status", lambda repo, sha, token: {"statuses": statuses[sha]})
    monkeypatch.setattr(github_reviews, "list_pulls", fake_pulls)
    return calls


def test_get_pr_review_returns_the_apps_latest_comment_and_status(clone, github):
    out = ok("get_pr_review", {"repo": REPO, "pr_number": 19})
    assert out["review"]["body"] == "## ⚠️ prompt injection quarantined"
    assert out["review"]["author"] == "cra-bot[bot]" and out["review_comments_by_app"] == 2
    assert out["status"]["state"] == "failure" and "prompt injection" in out["status"]["description"]
    assert (out["state"], out["head_sha"]) == ("merged", "head1")


def test_get_pr_review_without_a_bot_comment(clone, github, monkeypatch):
    monkeypatch.setattr(github_reviews, "list_issue_comments", lambda *a: [])
    out = ok("get_pr_review", {"repo": REPO, "pr_number": 19})
    assert out["review"] is None and "No review comment from cra-bot[bot]" in out["note"]


def test_list_reviewed_prs(clone, github):
    out = ok("list_reviewed_prs", {"repo": REPO, "limit": 3})
    assert [(p["number"], p["head_sha"], p["review_state"]) for p in out["prs"]] == [
        (1, "head1", "failure"), (2, "head2", "success"), (3, "head3", None)]
    assert ("pulls", REPO, "all", 3) in github


def test_list_reviewed_prs_rejects_an_out_of_range_limit(clone, github):
    assert call("list_reviewed_prs", {"repo": REPO, "limit": 500}).is_error


def test_github_404_is_a_clear_error(clone, github, monkeypatch):
    def not_found(repo, n, token):
        resp = requests.Response()
        resp.status_code, resp.url = 404, f"https://api.github.com/repos/{repo}/pulls/{n}"
        raise requests.HTTPError("404 Client Error", response=resp)
    monkeypatch.setattr(github_reviews, "get_pr", not_found)
    text = error_text("get_pr_review", {"repo": REPO, "pr_number": 999})
    assert "GitHub returned 404 Not Found for /repos/octo/app/pulls/999" in text
    assert "isn't installed" in text


def test_github_tools_refuse_unconfigured_repos_before_any_call(clone, github):
    assert "Unknown repo" in error_text("get_pr_review", {"repo": "other/repo", "pr_number": 1})
    assert github == []


def test_github_read_helpers_use_the_installation_token(monkeypatch):
    import tools.github_tools as gt
    seen = []

    class Resp:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    def fake_get(url, headers, params=None, timeout=None):
        seen.append((url, headers["Authorization"], params, timeout))
        return Resp([{"id": i} for i in range(100)] if params.get("page") == 1 else [{"id": "last"}])
    monkeypatch.setattr(gt.requests, "get", fake_get)
    assert len(gt.list_issue_comments("o/r", 5, token="t")) == 101
    assert [s[2]["page"] for s in seen] == [1, 2]
    assert all(s[1] == "token t" and s[3] == 30 for s in seen)
    seen.clear()
    monkeypatch.setattr(gt.requests, "get", lambda url, headers, params=None, timeout=None:
                        seen.append((url, params)) or Resp({}))
    gt.list_pulls("o/r", limit=7, token="t")
    gt.get_combined_status("o/r", "abc", token="t")
    assert seen == [("https://api.github.com/repos/o/r/pulls",
                     {"state": "all", "sort": "updated", "direction": "desc", "per_page": 7}),
                    ("https://api.github.com/repos/o/r/commits/abc/status", {"per_page": 100})]


# --- review_patch -------------------------------------------------------------------------

SQLI_PATCH = textwrap.dedent('''\
    @@ -0,0 +1,6 @@
    +import sqlite3
    +
    +def find_user(conn, name):
    +    return conn.execute(
    +        f"SELECT id FROM users WHERE name = '{name}'"
    +    ).fetchall()''')

CLEAN_PATCH = textwrap.dedent('''\
    @@ -0,0 +1,3 @@
    +def add(a, b):
    +    """Sum of a and b."""
    +    return a + b''')

SQLI_FINDING = json.dumps([{
    "severity": "HIGH", "title": "SQL injection via f-string query",
    "description": "User input is interpolated into the SQL string.",
    "line_start": 4, "line_end": 6, "snippet": "f\"SELECT id FROM users WHERE name = '{name}'\"",
    "suggestion": "Use a parameterized query.", "confidence": 0.9,
}])


class FakeLLM:
    """model_client.reason for every agent: the security task answers with
    `security` (default: no findings), every other task with []. Records
    (task, user prompt) per call."""

    def __init__(self):
        self.security = "[]"
        self.prompts: list[tuple[str, str]] = []

    def reason(self, system, user, task, max_tokens=2000):
        self.prompts.append((task, user))
        return self.security if task == "security" else "[]"


@pytest.fixture
def fake_llm(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(mc.model_client, "reason", llm.reason)
    return llm


def test_review_patch_blocks_sql_injection_via_the_floor(clone, fake_llm):
    fake_llm.security = SQLI_FINDING
    out = ok("review_patch", {"file_path": "app/users.py", "patch": SQLI_PATCH})
    assert out["would_block"] is True
    [f] = out["findings"]
    assert (f["category"], f["severity"]) == ("SECURITY", "CRITICAL")  # the model said HIGH
    assert f["title"] == "SQL injection via f-string query" and f["suggestion"] == "Use a parameterized query."
    assert f["lines"] == "5-5"  # re-anchored from the model's 4-6 to the snippet's line
    assert out["gate"].startswith("1 critical issue(s): SQL injection")
    assert sorted(a["agent"] for a in out["agents"]) == [
        "architecture", "documentation", "security", "test_coverage"]
    assert out["llm"]["calls"] == 0 and out["warnings"] == []


def test_review_patch_clean_patch_does_not_block(clone, fake_llm):
    out = ok("review_patch", {"file_path": "lib/math.py", "patch": CLEAN_PATCH})
    assert out["would_block"] is False and out["findings"] == []
    assert out["gate"] == "No blocking issues found." and out["context_repo"] is None


def test_review_patch_adds_call_graph_context_for_a_configured_repo(clone, fake_llm):
    patch = "@@ -4,2 +4,2 @@\n def get_user(name):\n-    return run_query(name)\n+    return run_query(name.strip())"
    out = ok("review_patch", {"file_path": str(clone / "app" / "users.py"), "patch": patch})
    assert (out["file"], out["context_repo"]) == ("app/users.py", REPO)
    security_prompt = next(user for task, user in fake_llm.prompts if task == "security")
    assert "Called by" in security_prompt and "app/users.py::list_users" in security_prompt


def test_review_patch_skips_filtered_files_without_llm_calls(clone, fake_llm):
    out = ok("review_patch", {"file_path": "package-lock.json", "patch": "@@ -1 +1 @@\n-a\n+b"})
    assert out["skipped"] is True and "skip pattern" in out["reason"] and out["would_block"] is False
    assert fake_llm.prompts == []


def test_review_patch_docs_file_runs_only_the_documentation_agent(clone, fake_llm):
    out = ok("review_patch", {"file_path": "README.md", "patch": "@@ -1 +1 @@\n-a\n+b"})
    assert [a["agent"] for a in out["agents"]] == ["documentation"]


def test_review_patch_when_every_agent_fails_is_an_error_not_a_clean_review(clone, monkeypatch):
    def down(*a, **k):
        raise mc.ModelUnavailableError("All LLM providers failed for task=x (bedrock)")
    monkeypatch.setattr(mc.model_client, "reason", down)
    text = error_text("review_patch", {"file_path": "a.py", "patch": CLEAN_PATCH})
    assert "No review: every agent failed" in text and "All LLM providers failed" in text


def test_review_patch_on_bedrock_without_aws_credentials(clone, fake_llm, settings, monkeypatch):
    import boto3

    class NoCredsSession:
        def get_credentials(self):
            return None
    monkeypatch.setattr(settings, "LLM_PROVIDER", "bedrock")
    monkeypatch.setattr(settings, "LLM_FALLBACK", "none")
    monkeypatch.setattr(boto3, "Session", NoCredsSession)
    text = error_text("review_patch", {"file_path": "a.py", "patch": CLEAN_PATCH})
    assert "No AWS credentials found" in text and "aws login --profile cra-admin" in text
    assert fake_llm.prompts == []


def test_review_patch_guardrail_quarantine_blocks_without_agents(clone, fake_llm, settings, monkeypatch):
    from pipeline.guardrail import ScreenResult
    monkeypatch.setattr(settings, "GUARDRAIL_ID", "gr-1")
    monkeypatch.setattr(review, "require_aws_credentials", lambda: None)
    monkeypatch.setattr(review, "screen_patch",
                        lambda f, p: ScreenResult(blocked=True, detail="PROMPT_ATTACK (confidence HIGH)"))
    out = ok("review_patch", {"file_path": "a.py", "patch": CLEAN_PATCH, "use_guardrail": True})
    assert out["quarantined"] and out["would_block"]
    assert out["findings"][0]["title"] == "Possible prompt injection in a.py"
    assert fake_llm.prompts == []


def test_review_patch_guardrail_requested_but_not_configured(clone, fake_llm):
    out = ok("review_patch", {"file_path": "a.py", "patch": CLEAN_PATCH, "use_guardrail": True})
    assert "guardrail not configured" in out["warnings"][0] and not out["quarantined"]


def test_review_patch_guardrail_error_fails_open_with_a_warning(clone, fake_llm, settings, monkeypatch):
    from pipeline.guardrail import ScreenResult
    monkeypatch.setattr(settings, "GUARDRAIL_ID", "gr-1")
    monkeypatch.setattr(review, "require_aws_credentials", lambda: None)
    monkeypatch.setattr(review, "screen_patch", lambda f, p: ScreenResult(blocked=False, error="Throttling"))
    out = ok("review_patch", {"file_path": "a.py", "patch": CLEAN_PATCH, "use_guardrail": True})
    assert out["warnings"] == ["guardrail unavailable: Throttling"] and len(out["agents"]) == 4


def test_review_patch_rejects_an_empty_patch(clone):
    assert "patch is empty" in error_text("review_patch", {"file_path": "a.py", "patch": "  "})


# --- stdout isolation over real stdio ---------------------------------------------------

def test_stdio_subprocess_prints_never_reach_the_protocol_stream(tmp_path):
    """The knowledge-graph build print()s progress from the tool's worker
    thread; over a real stdio connection the call must still succeed (a
    stray line on stdout would break the JSON-RPC stream) and the prints
    must show up on stderr instead."""
    clone = _write_repo(tmp_path / "clone")
    env = {
        "MCP_REPOS": f"{REPO}={clone.as_posix()}",
        "PYTHONPATH": str(ROOT),
        "LANGSMITH_TRACING": "false",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server"], env=env, cwd=str(tmp_path))

    async def _go(errlog):
        async with Client(stdio_client(params, errlog=errlog)) as client:
            tools = (await client.list_tools()).tools
            callers = await client.call_tool("get_callers", {"repo": REPO, "name": "run_query"})
            function = await client.call_tool("get_function", {"repo": REPO, "name": "get_user", "refresh": True})
            return tools, callers, function

    with tempfile.TemporaryFile("w+", encoding="utf-8") as errlog:
        tools, callers, function = asyncio.run(asyncio.wait_for(_go(errlog), timeout=120))
        errlog.seek(0)
        stderr = errlog.read()

    assert len(tools) == 7
    assert not callers.is_error, callers.content[0].text
    assert {c["name"] for c in callers.structured_content["matches"][0]["callers"]} == {
        "save", "get_user", "list_users"}
    assert not function.is_error
    # Both builds printed, and it all went to stderr.
    assert stderr.count("Registering functions...") == 2, stderr
    assert "[mcp] call graph for octo/app" in stderr
