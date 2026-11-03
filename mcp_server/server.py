"""Tool registration. Every tool is read-only: nothing is written to GitHub,
AWS or Qdrant; review_patch only makes LLM calls."""
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from mcp_server import code_tools, github_reviews, review
from mcp_server.errors import tool_errors

SERVER_NAME = "code-review-agent"
TOOL_NAMES = ("search_code", "get_function", "get_callers", "get_callees",
              "get_pr_review", "list_reviewed_prs", "review_patch")

INSTRUCTIONS = """Read-only tools over the code-review-agent pipeline. Repos must be listed in \
MCP_REPOS (owner/name); the call-graph tools also need the repo's local clone. Use search_code \
to find code by meaning, get_function / get_callers / get_callees to walk the Python call graph, \
get_pr_review / list_reviewed_prs for what the review bot said on GitHub, and review_patch to run \
the four review agents on one file's diff before pushing it (costs about $0.002 per file)."""

Repo = Annotated[str, Field(description="GitHub repo as owner/name; must be listed in MCP_REPOS.")]
FunctionName = Annotated[str, Field(
    description="Function name: 'name', 'Class.method', or an exact key 'path/to/file.py::name'.")]
Refresh = Annotated[bool, Field(
    description="Rebuild the call graph from the clone first (after pulling or editing code).")]

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                            open_world_hint=False)
READ_ONLY_REMOTE = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                                   open_world_hint=True)


def build_server(lifespan=None) -> MCPServer:
    mcp = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, lifespan=lifespan)

    @mcp.tool(annotations=READ_ONLY_REMOTE)
    def search_code(
        repo: Repo,
        query: Annotated[str, Field(description="What you're looking for, in plain words or code.")],
        limit: Annotated[int, Field(ge=1, le=code_tools.MAX_SEARCH_RESULTS,
                                    description="How many results (1-20).")] = 5,
    ) -> dict[str, Any]:
        """Semantic search over the repo's indexed code (Qdrant, Titan embeddings). Returns the best
        matching functions and classes with file, name, lines, similarity score and a truncated
        source excerpt. The repo must already be indexed in the collection for the current
        EMBEDDING_PROVIDER."""
        with tool_errors():
            return code_tools.search_code(repo, query, limit)

    @mcp.tool(annotations=READ_ONLY)
    def get_function(repo: Repo, name: FunctionName, refresh: Refresh = False) -> dict[str, Any]:
        """A Python function from the repo's knowledge graph (built from the local clone): file,
        enclosing class, line range and source (truncated at 4,000 characters). Every definition
        with that name is returned (up to 10)."""
        with tool_errors():
            return code_tools.get_function(repo, name, refresh)

    @mcp.tool(annotations=READ_ONLY)
    def get_callers(repo: Repo, name: FunctionName, refresh: Refresh = False) -> dict[str, Any]:
        """Functions that call the named Python function, from the static call graph: each with
        file, class, line range and the lines of the calls. Use it to see the impact radius of a
        change."""
        with tool_errors():
            return code_tools.get_callers(repo, name, refresh)

    @mcp.tool(annotations=READ_ONLY)
    def get_callees(repo: Repo, name: FunctionName, refresh: Refresh = False) -> dict[str, Any]:
        """Functions the named Python function calls, from the static call graph: each with file,
        class, line range and the lines of the calls."""
        with tool_errors():
            return code_tools.get_callees(repo, name, refresh)

    @mcp.tool(annotations=READ_ONLY_REMOTE)
    def get_pr_review(
        repo: Repo,
        pr_number: Annotated[int, Field(ge=1, description="The pull request number.")],
    ) -> dict[str, Any]:
        """The review bot's latest review comment on a PR (Markdown, truncated at 6,000
        characters) and the ai-code-review commit status (state and description) on the PR's head
        commit, which is what blocks or allows the merge."""
        with tool_errors():
            return github_reviews.get_pr_review(repo, pr_number)

    @mcp.tool(annotations=READ_ONLY_REMOTE)
    def list_reviewed_prs(
        repo: Repo,
        limit: Annotated[int, Field(ge=1, le=github_reviews.MAX_PRS,
                                    description="How many PRs, most recently updated first (1-30).")] = 10,
    ) -> dict[str, Any]:
        """Recent PRs (open and closed) with their title, state, head SHA and the ai-code-review
        status on that head commit (success = passed, failure = blocked, null = not reviewed)."""
        with tool_errors():
            return github_reviews.list_reviewed_prs(repo, limit)

    @mcp.tool(annotations=READ_ONLY_REMOTE)
    async def review_patch(
        file_path: Annotated[str, Field(
            description="The file the patch changes, e.g. app/users.py. A path inside a clone listed "
                        "in MCP_REPOS adds call-graph context.")],
        patch: Annotated[str, Field(
            description="That file's unified diff: the @@ hunks with +/- lines (git diff output for "
                        "one file).")],
        use_guardrail: Annotated[bool, Field(
            description="Screen the patch for prompt injection with Bedrock Guardrails first "
                        "(needs GUARDRAIL_ID); a flagged patch is quarantined as CRITICAL.")] = False,
    ) -> dict[str, Any]:
        """Runs the real review pipeline on one file's patch: the file filter, optionally the
        guardrail, then the security, architecture, test-coverage and documentation agents
        (deterministic severity floor and ceiling included) and the merge gate. Returns the ranked
        findings, would_block (whether the ai-code-review check would fail) and the gate's
        description. Sends the patch to the LLM (Bedrock gpt-oss by default, about $0.002 per
        file); nothing is posted to GitHub."""
        with tool_errors():
            return await review.review_patch(file_path, patch, use_guardrail)

    return mcp
