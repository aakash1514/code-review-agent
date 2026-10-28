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
- **Review scope.** Lockfiles, generated and vendored paths, removed and binary files, `LICENSE` and patches over `MAX_PATCH_CHARS` are skipped before any LLM call. Docs (`.md`, `.txt`, `.rst`) get only the documentation agent. At most `MAX_FILES_PER_REVIEW` files are reviewed, Python first, then other code, config and docs. Every skipped file is listed with its reason in the PR comment and on the dashboard. `MAX_PATCH_CHARS` and `MAX_FILES_PER_REVIEW` are set through environment variables; the skip patterns are the `REVIEW_SKIP_PATTERNS` list in `config.py`.
- **Codebase-aware RAG.** Python files are split into function and class chunks with the `ast` module, embedded, and stored in Qdrant with per-repo filtering. Local development defaults to `granite-embedding-97m` (384-dim, run in-process with sentence-transformers or through the Hugging Face Inference API). The container defaults to Amazon Bedrock Titan Text Embeddings V2 (512-dim; 256 and 1024 also supported). Each embedding model gets its own Qdrant collection (`codebase_granite384`, `codebase_titan512`), so vectors from different models never mix. If embedding or Qdrant fails during a review, the review still runs on call-graph context and says "vector context unavailable" in the PR comment and the dashboard.
- **Call-graph knowledge graph.** Calls, callers and imports for every function, scoped per class. It rebuilds automatically on every push to the default branch.
- **Severity-ranked synthesis.** Findings are de-duplicated across agents and re-runs, then ranked from CRITICAL to INFO.
- **Merge gate with human in the loop.** CRITICAL findings set a failed `ai-code-review` commit status. A reviewer can approve or block from the dashboard, and that decision is stored.
- **Live dashboard.** GitHub OAuth login, per-agent progress over WebSocket, a Monaco side-by-side diff viewer, and a React Flow call-graph view.
- **Free-tier LLMs.** OpenAI's open-weight gpt-oss-120b on Groq for reasoning and gpt-oss-20b for simple tasks, with automatic fallback between them.
- **Container-ready.** One non-root Docker image (about 115 MB compressed), configured only through environment variables. It writes structured JSON logs, exposes `/healthz`, and shuts down gracefully: reviews still unfinished at shutdown are marked `interrupted` and their GitHub check is set to error.

## Tech stack

| Layer | Tools |
| --- | --- |
| API | Python, FastAPI, WebSockets |
| LLM | Groq: gpt-oss-120b + gpt-oss-20b, with fallback |
| Retrieval | Amazon Bedrock Titan Text Embeddings V2, or sentence-transformers on CPU (or the Hugging Face Inference API); Qdrant Cloud; Python `ast` call graph |
| Storage | SQLite (jobs, results, override decisions) and knowledge-graph JSON, both under `DATA_DIR` |
| GitHub | GitHub App (JWT + installation tokens), webhooks, commit status API, PR comments |
| Dashboard | React, Vite, Monaco Editor, React Flow + dagre |
| Runtime | Docker (multi-stage, non-root), uvicorn, structured JSON logs |

## Project structure

```
agents/      security, architecture, test-coverage, documentation agents
api/         webhook server, WebSocket manager, dashboard REST routes
pipeline/    parallel runner, synthesizer, HITL gate, job store, re-indexer
rag/         AST chunker, Qdrant indexer, retriever, knowledge graph
tools/       GitHub API client, code helpers, Groq model client, structured logging
models/      Pydantic models for findings and reports
scripts/     index_repo.py — one-off indexing of a repository
dashboard/   React + Vite frontend
```

## Running locally

**Prerequisites:** Python 3.11+, Node.js 18+, a GitHub App installed on the target repo, and free accounts on Groq and Qdrant Cloud (plus Hugging Face if you use `EMBEDDING_PROVIDER=hf_api`).

1. **Configure.** Copy [`.env.example`](.env.example) to `.env` (it's git-ignored) and fill it in. `config.py` is tracked and holds no secrets: it reads every setting from environment variables, loading `.env` first. Leave a setting empty to use its default.

   | Setting | What it is |
   | --- | --- |
   | `GITHUB_APP_ID`, `GITHUB_INSTALLATION_ID` | Your GitHub App's IDs |
   | `GITHUB_PRIVATE_KEY` | The App's private key as a PEM string (newlines written as `\n`). Takes precedence over `GITHUB_PRIVATE_KEY_PATH` |
   | `GITHUB_PRIVATE_KEY_PATH` | Path to the App's `.pem` private key. Default `./github-app-private-key.pem` |
   | `GITHUB_WEBHOOK_SECRET` | Secret used to verify webhook signatures |
   | `GROQ_API_KEY`, `GROQ_MODEL_REASONING`, `GROQ_MODEL_LIGHT` | Groq key and model IDs. Defaults `openai/gpt-oss-120b` and `openai/gpt-oss-20b` |
   | `EMBEDDING_PROVIDER` | `local` (default): embed in-process with sentence-transformers on CPU. `hf_api`: use the Hugging Face Inference API. `bedrock`: Amazon Bedrock Titan Text Embeddings V2 (`amazon.titan-embed-text-v2:0`) |
   | `HF_TOKEN` | Hugging Face token, only needed for `EMBEDDING_PROVIDER=hf_api` |
   | `AWS_REGION` | AWS region. Default `ap-south-1`. Credentials come from `AWS_PROFILE`, environment keys or a task role |
   | `BEDROCK_EMBED_REGION` | Region for Bedrock embedding calls. Default: `AWS_REGION` |
   | `BEDROCK_EMBED_DIM` | Titan V2 vector size: `256`, `512` (default) or `1024`. Each size has its own collection (`codebase_titan<dim>`) |
   | `QDRANT_URL`, `QDRANT_API_KEY` | Qdrant Cloud cluster |
   | `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET` | OAuth credentials for dashboard login |
   | `GITHUB_OAUTH_REDIRECT_URI` | OAuth callback. Default `http://localhost:8000/auth/callback` |
   | `DASHBOARD_BASE_URL` | Where the dashboard runs (login redirects back here). Default `http://localhost:5173` |
   | `DATA_DIR` | Holds the SQLite database and `knowledge_graph_*.json`. Default `./data` (`/data` in the container) |
   | `DATABASE_URL` | Optional. Default `sqlite:///<DATA_DIR>/review_agent.db` |
   | `MAX_PATCH_CHARS`, `MAX_FILES_PER_REVIEW` | Review scope limits. Defaults `20000` and `25` |
   | `LOG_FORMAT` | `text` (default locally) or `json` (default in the container) |
   | `ORIGIN_VERIFY_SECRET` | Empty (default) turns the check off. When set, every request except `/healthz` needs a matching `X-Origin-Verify` header |

   **Set up dashboard login.** Dashboard login uses your GitHub App's own OAuth credentials. In the GitHub App's settings (*Settings → Developer settings → GitHub Apps → your app*), set the callback URL to `http://localhost:8000/auth/callback` and generate a client secret. Copy the App's client ID and that secret into `GITHUB_OAUTH_CLIENT_ID` and `GITHUB_OAUTH_CLIENT_SECRET`. A standalone OAuth App with the same callback URL also works.

2. **Install and index the target repo.**

   ```bash
   pip install -r requirements-local-embed.txt -r requirements-dev.txt
   python -m scripts.index_repo --path /path/to/repo --repo owner/name
   ```

   The dependencies are split into three files:

   - `requirements.txt`: the core runtime, without PyTorch. It's all the Docker image installs, and it's enough for `EMBEDDING_PROVIDER=hf_api` or `bedrock`.
   - `requirements-local-embed.txt`: the core runtime plus CPU-only PyTorch and sentence-transformers, for `EMBEDDING_PROVIDER=local`.
   - `requirements-dev.txt`: test tools (pytest, httpx, pytest-cov) and `botocore[crt]`, which boto3 needs to use `aws login` credentials.

   Indexing writes to the configured embedding model's collection, so index again after switching `EMBEDDING_PROVIDER` or `BEDROCK_EMBED_DIM`.

   `requirements-local-embed.txt` pulls CPU-only PyTorch from PyTorch's own package index (the `--extra-index-url` line), so no CUDA libraries are installed. The embedding stack (PyTorch CPU, sentence-transformers, transformers and their dependencies) adds about 1.1 GB on disk, including a roughly 125 MB PyTorch download. The first run also downloads the embedding model (about 210 MB) into the Hugging Face cache. The API loads it once at startup, and refuses to start if the configured model doesn't return vectors of the expected size.

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

   The dashboard calls the API with same-origin relative URLs. The Vite dev server proxies `/api`, `/auth` and `/ws` to `http://localhost:8000`, so there's nothing to configure. `VITE_API_BASE_URL` is optional and only needed to point at a different API.

5. Open a pull request on the indexed repo and watch the review run in the dashboard.

6. **Tests:** `python -m pytest` (no network or real keys needed).

## Running in Docker

The same image that will run in the cloud also runs locally with Docker Compose. It defaults to JSON logs, `DATA_DIR=/data` (a named volume) and Bedrock embeddings. Compose reads `.env` for the GitHub App, Groq, Qdrant and OAuth settings, and mounts the GitHub App key from `./github-app-private-key.pem` as a compose secret.

1. **Get short-lived AWS credentials** for Bedrock:

   ```bash
   aws login --profile <your-profile>
   aws configure export-credentials --profile <your-profile> --format env-no-export > .env.aws
   ```

   `.env.aws` is git- and docker-ignored, so it never ends up in a commit or in the image. It expires with your login session; re-run both lines and `docker compose up -d` to refresh it.

2. **Build and start:**

   ```bash
   docker compose up -d --build
   docker compose logs -f app
   ```

   Stop any local `uvicorn --port 8000` first. The container listens on port 8000, so your tunnel keeps working. `GET /healthz` responds without calling any external service.

3. **Index a repo inside the container.** The container has no checkout of the repo, so it indexes through the GitHub API:

   ```bash
   docker compose exec app python -c "from pipeline.reindexer import rebuild_index_for_repo as r; from tools.github_tools import get_installation_token as t; print(r('owner/name', 'main', t()))"
   ```

**Shutdown.** On `SIGTERM` (for example from `docker compose down`), uvicorn gets up to 90 seconds to finish (`--timeout-graceful-shutdown 90`; compose's `stop_grace_period` is 120 s). Reviews still pending or running are marked `interrupted`, and their GitHub check is set to error with "push again to re-run". The same cleanup runs at startup for anything a crash left behind.

## API

| Method | Route | Purpose | Login required |
| --- | --- | --- | --- |
| GET | `/healthz` | Health check; makes no external calls | No |
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
