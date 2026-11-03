# MCP server

The review pipeline that gates this repo's PRs, available inside the editor. `python -m mcp_server` is a local [Model Context Protocol](https://modelcontextprotocol.io) server over stdio. Claude Code, Claude Desktop and Cursor can use it to search the indexed code, walk the call graph, read what the review bot said on a PR, and run the four review agents on a diff before it's pushed.

It doesn't need the AWS deployment (ECS, ALB, EFS). It talks only to:

- **Qdrant Cloud**, for `search_code`.
- **Amazon Bedrock**, from your laptop's AWS login: Titan for query embeddings, and gpt-oss for `review_patch`.
- **The GitHub API**, with the GitHub App's installation token.
- **A local clone** of each repo, which the call graph is built from.

Every tool is read-only. Nothing is posted to GitHub or written to AWS or Qdrant. `review_patch` only makes LLM calls.

## Tools

| Tool | What it returns | Uses |
|---|---|---|
| `search_code(repo, query, limit=5)` | The best-matching functions and classes: file, name, lines, similarity score, and a source excerpt (600 characters) | Qdrant + Titan |
| `get_function(repo, name, refresh=false)` | Each definition with that name: file, class, lines and source (4,000 characters) | Clone |
| `get_callers(repo, name, refresh=false)` | The functions that call it, each with file, class, lines and the line numbers of the calls | Clone |
| `get_callees(repo, name, refresh=false)` | The functions it calls, in the same format | Clone |
| `get_pr_review(repo, pr_number)` | The App's latest review comment on the PR (6,000 characters), plus the `ai-code-review` status (state and description) on the PR's head commit | GitHub |
| `list_reviewed_prs(repo, limit=10)` | Recent PRs, open and closed: number, title, state, head SHA and the `ai-code-review` state (`null` if not reviewed) | GitHub |
| `review_patch(file_path, patch, use_guardrail=false)` | The ranked findings (category, severity, title, lines, description, suggestion), `would_block` and the gate's description, plus the LLM calls, tokens and cost | Bedrock |

`name` can be `run_security_agent`, `Class.method`, or an exact key such as `agents/security_agent.py::run_security_agent`. A name that isn't found gets the closest matches as suggestions. Lists are capped (10 definitions per name, 50 callers or callees each, 20 search results, 30 PRs) and long text is truncated, so a tool result stays small.

### What `review_patch` runs

The same code as a PR review, for one file:

1. **The file filter** (`pipeline/file_filter.py`). A lockfile, a generated path or a patch over 20,000 characters comes back as `skipped` with the reason, and no LLM call is made. A docs file gets only the documentation agent.
2. **The guardrail** (only with `use_guardrail=true` and `GUARDRAIL_ID` set). A flagged patch is quarantined as a CRITICAL finding, exactly as in production, and no agent sees it. If the screen fails, the review goes ahead (fail-open) with a warning.
3. **Call-graph context.** If the file is in a configured clone (an absolute path under it, or a relative path that exists in it), the agents get its callers and callees, as in production. Otherwise they get no context. There's no vector search here.
4. **The four agents** (`run_agents_parallel`), with the random per-call delimiters, re-anchoring of line numbers, and the security floor and ceiling.
5. **`build_review_report` and `evaluate_hitl_gate`.** `would_block` is what the `ai-code-review` check would say.

There's no synthesis call. The structured result is the answer. If every agent fails (no Bedrock access, say), the tool returns an error, not an empty and apparently clean review.

## Setup

Python 3.13 and a clone of this repo. Then:

```bash
cd C:/code-review-agent
python -m venv venv && source venv/Scripts/activate     # venv/bin/activate on Linux/macOS
pip install -r requirements-mcp.txt                      # requirements.txt + mcp==2.3.0
aws login --profile cra-admin
```

`.env` needs the GitHub App settings (`GITHUB_APP_ID`, `GITHUB_INSTALLATION_ID`, and the private key via `GITHUB_PRIVATE_KEY_PATH` or `GITHUB_PRIVATE_KEY`) and `QDRANT_URL` / `QDRANT_API_KEY`. The server loads `.env` from the repo root and resolves relative paths against it, wherever the client starts it from. It never prints a secret.

### Configuration

| Variable | MCP default | Meaning |
|---|---|---|
| `MCP_REPOS` | (none) | The repos the tools may use, as `owner/name=<clone path>`, separated by `;` or newlines. The path is optional: `search_code` and the GitHub tools work without one, the call-graph tools don't. Any other repo is refused. |
| `AWS_PROFILE` | (none) | The profile from `aws login`, e.g. `cra-admin`. |
| `EMBEDDING_PROVIDER` | `bedrock` | Titan V2, 512 dimensions, Qdrant collection `codebase_titan512`. |
| `LLM_PROVIDER` | `bedrock` | gpt-oss-120b for security and architecture, gpt-oss-20b for tests and docs. |
| `LLM_FALLBACK` | `none` | Set it to `groq` to fall back to Groq (needs `GROQ_API_KEY`). |
| `GUARDRAIL_ID` | (none) | Only for `review_patch(..., use_guardrail=true)`. |

The three defaults apply only to the MCP server, and only when the variable isn't set in its environment. A value in `.env` doesn't override them, because `.env` holds the API server's settings. `LOG_FORMAT` is always `text` here.

**`search_code` needs the repo indexed** in the collection of the active embedding model. This repo's 668 chunks are already in `codebase_titan512`. For another repo, index it once with `EMBEDDING_PROVIDER=bedrock python -m scripts.index_repo --path <clone> --repo owner/name`. `local` and `hf_api` embeddings live in a different collection (`codebase_granite384`), and searching with one model never finds chunks indexed with the other. When the repo has no chunks in the collection, the tool says so.

**The call graph is built lazily.** The first call-graph tool for a repo parses its clone, which takes a few seconds for this repo (166 files, about 1,000 functions). The graph stays in memory for the life of the server. It's never written to disk or Qdrant. After pulling or editing code, pass `refresh: true` to rebuild it, or restart the server (reconnect it from `/mcp` in Claude Code).

## Register it

### Claude Code (Windows, Git Bash)

```bash
claude mcp add code-review-agent -s user \
  -e MCP_REPOS="aakash1514/code-review-agent=C:/code-review-agent" \
  -e AWS_PROFILE=cra-admin \
  -e EMBEDDING_PROVIDER=bedrock \
  -e LLM_PROVIDER=bedrock \
  -e LLM_FALLBACK=none \
  -e PYTHONPATH=C:/code-review-agent \
  -- C:/code-review-agent/venv/Scripts/python.exe -m mcp_server
```

- `-s user` makes it available in every project. Leave it out to register it only for the current directory.
- `claude mcp add` has no working-directory option, and Claude Code starts the server from whichever project you're in. So `PYTHONPATH` is what lets `-m mcp_server` find the package. The server then changes to the repo root on its own.
- The server name goes before the `-e` flags, and `--` separates Claude's options from the server's command.
- To add a second repo, separate entries with `;` inside the quotes: `"a/b=C:/src/b;c/d=C:/src/d"`.

Check it with `claude mcp list`, which should show it as connected, or with `/mcp` inside Claude Code. Remove it with `claude mcp remove code-review-agent -s user`.

### Claude Desktop

`%APPDATA%\Claude\claude_desktop_config.json` (on macOS, `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "code-review-agent": {
      "command": "C:\\code-review-agent\\venv\\Scripts\\python.exe",
      "args": ["-m", "mcp_server"],
      "env": {
        "MCP_REPOS": "aakash1514/code-review-agent=C:/code-review-agent",
        "AWS_PROFILE": "cra-admin",
        "EMBEDDING_PROVIDER": "bedrock",
        "LLM_PROVIDER": "bedrock",
        "LLM_FALLBACK": "none",
        "PYTHONPATH": "C:\\code-review-agent"
      }
    }
  }
}
```

Restart Claude Desktop after editing it. Cursor takes the same `mcpServers` block in `.cursor/mcp.json` (per project) or `~/.cursor/mcp.json` (global).

## Example prompts

- "Where do CRITICAL findings get raised for canonical vulnerabilities? Use search_code on aakash1514/code-review-agent."
- "Who calls `_floor_canonical_vulnerabilities`? Show me the call sites."
- "What did the review bot say on PR 19, and is the check still failing?"
- "List the last 5 PRs and which ones the bot blocked."
- "Run review_patch on my staged change to `api/webhook.py` (use `git diff --cached -- api/webhook.py`) and tell me whether it would block the merge."

## Measured (3 Nov 2026, from a laptop in India, against ap-south-1 and Qdrant Cloud eu-west-2)

| Call | Result | Latency |
|---|---|---|
| `search_code("…where are CRITICAL findings raised for canonical vulnerabilities")` | `agents/security_agent.py::_floor_canonical_vulnerabilities` first (score 0.64) | 6.2 s, cold |
| `get_callers(…, "_floor_canonical_vulnerabilities")` | `run_security_agent` (line 371), plus 3 tests | 4.2 s, including the graph build |
| `get_pr_review(…, 19)` | The guardrail quarantine comment; status `failure`, "1 critical issue(s): Possible prompt injection in tools/prompt_boundaries.py" | 8.8 s, cold token |
| `list_reviewed_prs(…, 5)` | PRs 22–18 with their states | 9.5 s |
| `review_patch("app/users.py", <f-string SQL query>)` | CRITICAL SQL injection (the model said HIGH and the floor raised it), `would_block: true` | 8.8 s, about $0.0017 |
| `review_patch("app/users.py", <parameterized query>)` | MEDIUM and LOW only, `would_block: false` | 5.6 s, $0.0012 |

## Cost

`review_patch` makes four LLM calls per file (one per agent; docs files make one), plus a retry if an agent's JSON doesn't parse. On Bedrock gpt-oss that's **about $0.001–0.002 per file** for a small patch, and more for a large one, since the cost grows with the patch's size. The exact cost of each call is in the result's `llm.cost_usd`, from the same token counter the production metrics use. The other tools make no LLM calls. `search_code` makes one Titan embedding call per query (a fraction of a cent).

## Limits

- **Python only.** The call graph uses Python's `ast`. Other languages have no call graph, and the call-graph tools find nothing for them.
- **The call graph is name-based.** `obj.save()` links to every function named `save`. `self.save()` is scoped to the caller's class. Two methods with the same name in different classes of one file share a key, and the last one wins (the same caveat as the production graph).
- **`search_code` searches the index, not your working tree.** It reflects the last indexing run.
- **`review_patch` reviews one file at a time** and has no vector context, so it can miss problems that span files. Its findings have the pipeline's precision (about 33% on the evals; see [evals/RESULTS.md](../evals/RESULTS.md)).
- **Single GitHub App installation**, as in the rest of the project.

## Security

- **Read-only.** No tool writes to GitHub, AWS or Qdrant, and every tool declares itself read-only to the client. GitHub is read with the App's installation token, which never leaves the server process.
- **Allow-listed repos.** A repo that isn't in `MCP_REPOS` is refused before any API call, so a prompt can't point the App's token at another repo.
- **Diffs go to Bedrock.** `review_patch` sends the patch to the LLM in your AWS account (ap-south-1 by default), as a PR review does. Don't pass content you wouldn't push.
- **Prompt injection.** The agents treat the patch as data inside random per-call delimiters. Set `use_guardrail: true` (with `GUARDRAIL_ID`) to screen a patch from someone else first. A flagged patch is quarantined and never reaches the agents.
- **stdout is the protocol channel.** All application output (the pipeline's `print()`s, logs, tracebacks) goes to stderr, which Claude Code and Claude Desktop write to their MCP logs. `tests/test_mcp_server.py` runs the server as a subprocess to check this.
- **Errors are one line.** Unknown repo, missing clone, function not found, missing or expired AWS credentials, a GitHub 404: each comes back as a short message with the fix, never a traceback.
