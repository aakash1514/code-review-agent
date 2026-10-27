# Autonomous Multi-Agent Code Review System

Four specialist AI agents review every GitHub pull request in parallel. They use the context of the whole codebase, post their findings as PR comments, and **block the merge when they find a critical issue**.

<!-- Add a dashboard screenshot or a 15-second demo GIF here -->
<!-- ![Dashboard](docs/dashboard.png) -->

## Why

Most AI reviewers only see the diff. This one also knows who calls the changed code and which similar functions already exist, because it indexes the repository into a vector store and a call graph before reviewing.

## How it works

```
GitHub PR event
      │  (GitHub App webhook, HMAC-verified)
      ▼
FastAPI webhook ──► background job (SQLite job store, duplicate-job guard)
      │
      ▼
Context retrieval ── Qdrant vectors (function/class chunks) + AST call graph
      │
      ▼
┌──────────── 4 agents in parallel, 45 s timeout each ────────────┐
│  Security   │  Architecture   │  Test coverage   │  Documentation │
└─────────────────────────────────────────────────────────────────┘
      │
      ▼
Synthesizer ── de-duplicate + rank by severity ──► PR review comments
      │
      ▼
HITL gate ── any CRITICAL finding ──► failed commit status (merge blocked)
      │
      ▼
React dashboard ── live progress over WebSocket, approve / block override
```

## Features

- **Parallel specialist agents.** Security, Architecture, Test Coverage and Documentation agents run concurrently. A slow or failing agent times out without breaking the review.
- **Codebase-aware RAG.** Python files are split into function and class chunks with the `ast` module, embedded with `granite-embedding-97m` (384-dim), and stored in Qdrant with per-repo filtering.
- **Call-graph knowledge graph.** Calls, callers and imports for every function, scoped per class. It rebuilds automatically on every push to the default branch.
- **Severity-ranked synthesis.** Findings are de-duplicated across agents and re-runs, then ranked from CRITICAL to INFO.
- **Merge gate with human in the loop.** CRITICAL findings set a failed `ai-code-review` commit status. A reviewer can approve or block from the dashboard, and that decision is stored.
- **Live dashboard.** GitHub OAuth login, per-agent progress over WebSocket, a Monaco side-by-side diff viewer, and a React Flow call-graph view.
- **Zero-cost stack.** Everything runs on free tiers: Llama 3.3 70B on Groq for reasoning, a lighter Groq model for simple tasks, with automatic fallback between them.

## Tech stack

| Layer | Tools |
| --- | --- |
| API | Python, FastAPI, WebSockets |
| LLM | Groq (Llama 3.3 70B + light model, with fallback) |
| Retrieval | Hugging Face Inference API embeddings, Qdrant Cloud, Python `ast` call graph |
| Storage | SQLite (jobs, results, override decisions) |
| GitHub | GitHub App (JWT + installation tokens), webhooks, commit status API, PR comments |
| Dashboard | React, Vite, Monaco Editor, React Flow + dagre |

## Project structure

```
agents/      security, architecture, test-coverage, documentation agents
api/         webhook server, WebSocket manager, dashboard REST routes
pipeline/    parallel runner, synthesizer, HITL gate, job store, re-indexer
rag/         AST chunker, Qdrant indexer, retriever, knowledge graph
tools/       GitHub API client, code helpers, Groq model client
models/      Pydantic models for findings and reports
scripts/     index_repo.py — one-off indexing of a repository
dashboard/   React + Vite frontend
```

## Running locally

**Prerequisites:** Python 3.11+, Node.js 18+, a GitHub App installed on the target repo, and free accounts on Groq, Hugging Face and Qdrant Cloud.

1. **Configure.** Copy [`config.example.py`](config.example.py) to `config.py` (it's git-ignored) and fill in these settings:

   | Setting | What it is |
   | --- | --- |
   | `GITHUB_APP_ID`, `GITHUB_INSTALLATION_ID` | Your GitHub App's IDs |
   | `GITHUB_PRIVATE_KEY_PATH` | Path to the App's `.pem` private key |
   | `GITHUB_WEBHOOK_SECRET` | Secret used to verify webhook signatures |
   | `GROQ_API_KEY`, `GROQ_MODEL_REASONING`, `GROQ_MODEL_LIGHT` | Groq key and model names |
   | `HF_TOKEN` | Hugging Face token for embeddings |
   | `QDRANT_URL`, `QDRANT_API_KEY` | Qdrant Cloud cluster |
   | `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET` | OAuth credentials for dashboard login |
   | `GITHUB_OAUTH_REDIRECT_URI` | OAuth callback, e.g. `http://localhost:8000/auth/callback` |
   | `DASHBOARD_BASE_URL` | Where the dashboard runs, e.g. `http://localhost:5173` (login redirects back here) |
   | `DATABASE_URL` | e.g. `sqlite:///./review_agent.db` |

   **Set up dashboard login.** Dashboard login uses your GitHub App's own OAuth credentials. In the GitHub App's settings (*Settings → Developer settings → GitHub Apps → your app*), set the callback URL to `http://localhost:8000/auth/callback` and generate a client secret. Copy the App's client ID and that secret into `GITHUB_OAUTH_CLIENT_ID` and `GITHUB_OAUTH_CLIENT_SECRET`. A standalone OAuth App with the same callback URL also works.

2. **Install and index the target repo.**

   ```bash
   pip install -r requirements.txt
   python scripts/index_repo.py --path /path/to/repo --repo owner/name
   ```

3. **Start the API.**

   ```bash
   uvicorn api.webhook:app --reload --port 8000
   ```

   Expose it with a tunnel (e.g. ngrok) and set the tunnel URL plus `/webhook` as your GitHub App's webhook URL.

4. **Start the dashboard.**

   ```bash
   cd dashboard
   npm install
   npm run dev   # http://localhost:5173
   ```

5. Open a pull request on the indexed repo and watch the review run in the dashboard.

## API

| Method | Route | Purpose | Login required |
| --- | --- | --- | --- |
| POST | `/webhook` | GitHub App webhook receiver (HMAC-verified) | No |
| WS | `/ws/reviews?repo=owner/name&ticket=<ticket>` | Live review progress | Ticket (see below) |
| GET | `/api/reviews?repo=owner/name` | List reviews | Yes + read access |
| GET | `/api/reviews/{commit_sha}?repo=owner/name` | Review detail | Yes + read access |
| GET | `/api/reviews/{commit_sha}/files?repo=owner/name` | Changed files for the diff viewer | Yes + read access |
| POST | `/api/reviews/{commit_sha}/approve?repo=owner/name` | Human override: approve | Yes + write access |
| POST | `/api/reviews/{commit_sha}/block?repo=owner/name` | Human override: block | Yes + write access |
| POST | `/api/reviews/{commit_sha}/clear-override?repo=owner/name` | Remove a human override | Yes + write access |
| GET | `/api/graph?repo=owner/name` | Call graph for the dashboard | Yes + read access |
| POST | `/api/ws-ticket?repo=owner/name` | Issue a single-use ticket for the WebSocket | Yes + read access |
| GET | `/auth/login` | Redirect to GitHub OAuth consent | No |
| GET | `/auth/callback` | OAuth callback; redirects to the dashboard with a one-time `?code=` | No |
| POST | `/auth/exchange` | Trade the one-time code (JSON body `{"code": ...}`) for a session token | No |
| GET | `/auth/me` | Current user's login and avatar | Yes |
| POST | `/auth/logout` | End the current session | No |

Routes marked "Yes" expect an `Authorization: Bearer <session_id>` header. The session ID comes from the OAuth login flow. Without a valid session they return `401`.

- **Login handoff:** after GitHub login, `/auth/callback` redirects to the dashboard with a one-time `?code=`, never the session itself. The dashboard immediately removes it from the URL and sends it to `POST /auth/exchange` in a JSON body to get the session token. A code is valid for 60 seconds and works once; an unknown, expired or reused code returns `401`. Sessions last 7 days.
- **No GitHub token is stored.** The user's GitHub OAuth token is used once during the callback to find out who logged in, then dropped. Expired sessions, tickets and handoff codes are deleted on startup.

- **Read access** means the signed-in GitHub user can read the repo: any signed-in user for a public repo, or a collaborator with read access or higher (including org and team access) for a private one. Otherwise the route returns `403`. Allowed checks and repo visibility are cached in-process for 5 minutes, so removing someone's access takes up to 5 minutes to apply here. Denials are not cached.
- **Write access** is checked on every request for the override routes.
- **WebSocket:** browsers can't send an `Authorization` header on a WebSocket, so the dashboard first calls `POST /api/ws-ticket` and then connects with `?ticket=`. A ticket is valid for 30 seconds, works once, and only for the repo it was issued for. Otherwise the handshake is rejected with `403`. Access is checked when the socket opens; an already open socket isn't closed on logout.

## Engineering notes

- **False merge blocks.** The Architecture agent sometimes rated routine edge cases as CRITICAL. This is fixed with a severity anchor in the prompt plus a deterministic cap in code, and was verified clean across 4 consecutive runs.
- **Cross-repo isolation.** Qdrant point IDs hash `repo::file::function`, and every search filters by repo, so two repos with identical function names never overwrite each other.
- **Safe de-duplication.** Only exact duplicates are dropped. Showing a near-duplicate twice is safer than silently losing a real finding.

## Limitations

- **Python only.** The agents only analyse Python code. The AST chunker and the call graph are both built on Python's `ast` module. Other files, such as `.js` and `.ts`, only get coarse fallback chunks and are left out of the call graph.
- **Call-graph key collision.** Call-graph nodes are keyed by file and function name, not by class. If two different classes in the same file define a method with the same name, one overwrites the other in the graph.

## Roadmap

- [ ] Deploy to AWS: ECS Fargate, Amazon Bedrock with Groq fallback, CloudWatch alarms, Terraform
- [ ] CI/CD with GitHub Actions (lint, tests, image build, deploy via OIDC)
- [ ] Prompt size capping for large RAG contexts
- [ ] Full audit history for override decisions

Deferred fixes and open questions are tracked in [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).

## License

Released under the [MIT License](LICENSE).
