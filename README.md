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

1. **Configure.** Create `config.py` (it's git-ignored) with these settings:

   | Setting | What it is |
   | --- | --- |
   | `GITHUB_APP_ID`, `GITHUB_INSTALLATION_ID` | Your GitHub App's IDs |
   | `GITHUB_PRIVATE_KEY_PATH` | Path to the App's `.pem` private key |
   | `GITHUB_WEBHOOK_SECRET` | Secret used to verify webhook signatures |
   | `GROQ_API_KEY`, `GROQ_MODEL_REASONING`, `GROQ_MODEL_LIGHT` | Groq key and model names |
   | `HF_TOKEN` | Hugging Face token for embeddings |
   | `QDRANT_URL`, `QDRANT_API_KEY` | Qdrant Cloud cluster |
   | `DATABASE_URL` | e.g. `sqlite:///./review_agent.db` |

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

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/webhook` | GitHub App webhook receiver |
| WS | `/ws/reviews?repo=owner/name` | Live review progress |
| GET | `/reviews` | List reviews |
| GET | `/reviews/{commit_sha}` | Review detail |
| GET | `/reviews/{commit_sha}/files` | Changed files for the diff viewer |
| POST | `/reviews/{commit_sha}/approve` | Human override: approve |
| POST | `/reviews/{commit_sha}/block` | Human override: block |
| GET | `/graph` | Call graph for the dashboard |

## Engineering notes

- **False merge blocks.** The Architecture agent sometimes rated routine edge cases as CRITICAL. This is fixed with a severity anchor in the prompt plus a deterministic cap in code, and was verified clean across 4 consecutive runs.
- **Cross-repo isolation.** Qdrant point IDs hash `repo::file::function`, and every search filters by repo, so two repos with identical function names never overwrite each other.
- **Safe de-duplication.** Only exact duplicates are dropped. Showing a near-duplicate twice is safer than silently losing a real finding.

## Roadmap

- [ ] Deploy to AWS: ECS Fargate, Amazon Bedrock with Groq fallback, CloudWatch alarms, Terraform
- [ ] CI/CD with GitHub Actions (lint, tests, image build, deploy via OIDC)
- [ ] Prompt size capping for large RAG contexts
- [ ] Full audit history for override decisions
