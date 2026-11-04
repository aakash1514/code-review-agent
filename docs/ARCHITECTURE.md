# Architecture and configuration reference

Details behind the [README](../README.md): what each stage of a review does, how the LLM and embedding providers are configured and paced, every setting, and how dashboard auth works. Results are in [evals/RESULTS.md](../evals/RESULTS.md), open problems in [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

## Code layout

```
agents/      security, architecture, test-coverage, documentation agents
api/         webhook server, origin-verify middleware, WebSocket manager, dashboard REST routes, GitHub OAuth
pipeline/    file filter, guardrail screen, parallel runner, synthesizer, HITL gate, job store, re-indexer
rag/         AST chunker, embeddings, Qdrant indexer, retriever, knowledge graph
tools/       GitHub client, code helpers, LLM client (Bedrock + Groq), rate limiter, prompt boundaries, logs/metrics, tracing
mcp_server/  local stdio MCP server: read-only review tools for editors (docs/MCP.md)
models/      Pydantic models for findings and reports
evals/       golden cases, harness, scoring, CI gate thresholds
scripts/     index_repo.py: one-off indexing of a repository
dashboard/   React + Vite frontend
```

## Review stages

The entry point is `api/webhook.run_review_pipeline`, run as a background job for each `pull_request` event with action `opened` or `synchronize`. A `push` to the default branch triggers a reindex instead.

1. **Webhook.** `POST /webhook` checks the `X-Hub-Signature-256` HMAC with `hmac.compare_digest` and stores a job in SQLite (a duplicate-job guard stops a second job for the same commit).
2. **File filtering** (`pipeline/file_filter.py`). Lockfiles, generated and vendored paths, removed and binary files, `LICENSE`, and patches over `MAX_PATCH_CHARS` (default 20000) are skipped before any LLM call. Docs (`.md`, `.txt`, `.rst`) get only the documentation agent. At most `MAX_FILES_PER_REVIEW` (default 25) files are reviewed: Python first, then other code, config and docs. Every skipped file is listed with its reason in the PR comment and on the dashboard. The skip patterns are `REVIEW_SKIP_PATTERNS` in `config.py`.
3. **Guardrail screen** (`pipeline/guardrail.py`). With `GUARDRAIL_ID` set, each patch goes through Bedrock Guardrails `ApplyGuardrail` (prompt-attack filter). A flagged file is quarantined: no retrieval, no agents, and it's removed from the diff the synthesis step sees. It gets one CRITICAL "Possible prompt injection" finding, so the merge blocks until a human looks. If the call errors, the file is reviewed anyway with a "guardrail unavailable" warning (fail-open, [KNOWN_ISSUES #14](KNOWN_ISSUES.md)).
4. **Context** (`rag/retriever.py`). The knowledge graph (`rag/knowledge_graph.py`, built with Python's `ast`) gives callers, callees and imports of each changed function. Qdrant gives similar functions from the same repo. The combined context is capped at 8000 characters. If embedding or Qdrant fails, the review runs on call-graph context alone and says "vector context unavailable".
5. **Agents** (`pipeline/runner.run_agents_parallel`). The four agents run with `asyncio.gather`, each under a 45 s `asyncio.wait_for` timeout, so a slow or failing agent returns an error report without stopping the others. Every untrusted block in a prompt (diff, retrieved context, static analysis) is fenced in tags with a random per-call suffix (`tools/prompt_boundaries.py`, `secrets.token_hex(6)`).
6. **Post-processing.** Each finding's lines are re-anchored to where its quoted snippet sits in the patch (`tools/code_tools.reanchor_finding_lines`). The security agent applies a deterministic severity floor and ceiling ([evals/README.md](../evals/README.md#severity-policy)); the architecture agent caps CRITICAL. The synthesizer drops exact duplicates, groups related findings and ranks them CRITICAL to INFO.
7. **Synthesis** (`pipeline/synthesizer.synthesize_markdown`). gpt-oss-120b writes the PR comment from the ranked findings. If that call fails, a deterministic markdown fallback is used.
8. **HITL gate** (`pipeline/hitl_gate.evaluate_hitl_gate`). Any CRITICAL finding sets a failed `ai-code-review` commit status. A reviewer with write access can approve or block from the dashboard, which overrides the status.

Progress is broadcast to the dashboard over WebSocket at each stage.

## LLM provider

`LLM_PROVIDER` picks who answers the agents and the synthesis step, and `LLM_FALLBACK` who is tried when it fails. Production runs `LLM_PROVIDER=bedrock` with Groq as the fallback.

| Task | Bedrock model (default) | Reasoning effort |
|---|---|---|
| Security, architecture, synthesis | `openai.gpt-oss-120b-1:0` | medium |
| Test coverage, documentation | `openai.gpt-oss-20b-1:0` | low |

Calls go through the Bedrock Converse API in ap-south-1. If Bedrock fails after its retries, the call moves to Groq (same models); `LLM_FALLBACK=none` turns that off. On a Groq daily-quota 429 the client fails fast instead of waiting out the `retry-after` ([KNOWN_ISSUES #19](KNOWN_ISSUES.md)). With both settings at `groq` (the local default), only Groq is used; its free tier allows 200k tokens per day per model.

`BEDROCK_LLM_MODEL=global.amazon.nova-2-lite-v1:0` switches every task to Nova 2 Lite. It was measured in eval v3 and rejected: lower recall, higher cost, and it followed injected instructions ([RESULTS.md](../evals/RESULTS.md)).

**Quota and pacing.** On-demand quotas in this account: gpt-oss-120b and gpt-oss-20b 100 requests per minute each, Nova 2 Lite 20 per minute, Titan Embeddings V2 60 per minute (not adjustable). Each model has its own client-side limiter (`tools/rate_limit.RateLimiter`) at about 90% of its quota (gpt-oss 90/min, Nova 18/min; `BEDROCK_LLM_MAX_RPM` overrides), on top of botocore's adaptive retries, so parallel agents queue instead of being throttled. Titan is paced at 50/min (`BEDROCK_EMBED_MAX_RPM`): a full reindex makes hundreds of calls in a row, and retries alone gave up partway through.

**Cost** per 1M input / output tokens, reasoning included: gpt-oss-120b $0.15 / $0.62, gpt-oss-20b $0.07 / $0.31, Nova 2 Lite $0.30 / $2.50 (table and sources in `tools/model_client.py`; Groq's free tier counts as $0). Each review logs `LLMCostUSD`.

## Embeddings

Python files are split into function and class chunks with `ast`; other files get coarse fallback chunks. Each embedding model has its own Qdrant collection, so vectors from different models never mix. Qdrant point IDs hash `repo::file::function` and every search filters by repo.

| `EMBEDDING_PROVIDER` | Model | Dim | Collection |
|---|---|---|---|
| `bedrock` (container default) | Titan Text Embeddings V2 | 512 (256, 1024 supported) | `codebase_titan<dim>` |
| `local` (laptop default) | `granite-embedding-97m`, sentence-transformers on CPU | 384 | `codebase_granite384` |
| `hf_api` | the same model through the Hugging Face Inference API | 384 | `codebase_granite384` |

The API refuses to start if the configured model doesn't return vectors of the expected size. Index again after switching provider or dimension.

## Configuration

Copy [`.env.example`](../.env.example) to `.env` (git-ignored). `config.py` holds no secrets; it reads every setting from the environment, loading `.env` first. On AWS, the ECS task definition sets them, with secrets from Secrets Manager. Leave a setting empty to use its default.

| Setting | What it is |
| --- | --- |
| `GITHUB_APP_ID`, `GITHUB_INSTALLATION_ID` | Your GitHub App's IDs |
| `GITHUB_PRIVATE_KEY` | The App's private key as a PEM string (newlines written as `\n`). Takes precedence over `GITHUB_PRIVATE_KEY_PATH` |
| `GITHUB_PRIVATE_KEY_PATH` | Path to the App's `.pem` key. Default `./github-app-private-key.pem` |
| `GITHUB_WEBHOOK_SECRET` | Secret used to verify webhook signatures |
| `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET` | OAuth credentials for dashboard login |
| `GITHUB_OAUTH_REDIRECT_URI` | OAuth callback. Default `http://localhost:8000/auth/callback` |
| `DASHBOARD_BASE_URL` | Where the dashboard runs (login redirects back here). Default `http://localhost:5173` |
| `LLM_PROVIDER`, `LLM_FALLBACK` | `groq` (default) or `bedrock`; then `groq` (default) or `none` |
| `BEDROCK_LLM_MODEL_REASONING`, `BEDROCK_LLM_MODEL_LIGHT` | Defaults `openai.gpt-oss-120b-1:0` and `openai.gpt-oss-20b-1:0`; `LIGHT=none` uses the reasoning model for both |
| `BEDROCK_LLM_MODEL` | Optional: one model for every task, e.g. `global.amazon.nova-2-lite-v1:0` |
| `BEDROCK_LLM_REGION` | Region for Bedrock LLM calls. Default: `AWS_REGION` |
| `BEDROCK_LLM_MAX_RPM` | Pacing override: a number for every model, or `model-id=rpm,...`. Default gpt-oss 90, Nova 18 |
| `BEDROCK_REASONING_EFFORTS` | Per-task effort override, e.g. `security:medium,architecture:low` |
| `GROQ_API_KEY`, `GROQ_MODEL_REASONING`, `GROQ_MODEL_LIGHT` | Groq key and models. Defaults `openai/gpt-oss-120b` and `openai/gpt-oss-20b` |
| `EMBEDDING_PROVIDER` | `local` (default), `hf_api` or `bedrock` (see above) |
| `HF_TOKEN` | Only for `EMBEDDING_PROVIDER=hf_api` |
| `AWS_REGION` | Default `ap-south-1`. Credentials come from `AWS_PROFILE`, environment keys or the task role |
| `BEDROCK_EMBED_REGION`, `BEDROCK_EMBED_DIM` | Titan region (default `AWS_REGION`) and size: `256`, `512` (default) or `1024` |
| `GUARDRAIL_ID`, `GUARDRAIL_VERSION` | Bedrock Guardrails prompt-injection screen. Empty ID = off. Version default `1` |
| `QDRANT_URL`, `QDRANT_API_KEY` | Qdrant Cloud cluster |
| `DATA_DIR` | SQLite database and `knowledge_graph_*.json`. Default `./data` (`/data` in the container) |
| `DATABASE_URL` | Optional. Default `sqlite:///<DATA_DIR>/review_agent.db` |
| `MAX_PATCH_CHARS`, `MAX_FILES_PER_REVIEW` | Review scope limits. Defaults `20000` and `25` |
| `LOG_FORMAT` | `text` (default locally) or `json` (default in the container) |
| `ORIGIN_VERIFY_SECRET` | Empty (default) = off. When set, every request except `/healthz` needs a matching `X-Origin-Verify` header |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | LangSmith tracing; off unless `LANGSMITH_TRACING=true` |

**Dependencies** are split into three files: `requirements.txt` (the core runtime, without PyTorch; all the Docker image installs, enough for `hf_api` or `bedrock` embeddings), `requirements-local-embed.txt` (adds CPU-only PyTorch and sentence-transformers for `local` embeddings, about 1.1 GB on disk plus a 210 MB model download on first run), and `requirements-dev.txt` (pytest, httpx, pytest-cov, and `botocore[crt]`, which boto3 needs for `aws login` credentials).

## Dashboard auth

Routes marked "Yes" in the README's API table expect `Authorization: Bearer <session_id>`. Without a valid session they return `401`.

- **Login handoff.** After GitHub login, `/auth/callback` redirects to the dashboard with a one-time `?code=`, never the session itself. The dashboard removes it from the URL and trades it at `POST /auth/exchange` for a session token. A code is valid for 60 seconds and works once. Sessions last 7 days.
- **No GitHub token is stored.** The user's OAuth token is used once in the callback to find out who logged in, then dropped.
- **Read access** means the user can read the repo on GitHub: anyone signed in for a public repo, a collaborator with read access or higher for a private one. Otherwise `403`. Allowed checks are cached in-process for 5 minutes; denials aren't cached.
- **Write access** is checked on every request to the override routes.
- **WebSocket.** Browsers can't send an `Authorization` header on a WebSocket, so the dashboard first calls `POST /api/ws-ticket` and connects with `?ticket=`. A ticket is valid for 30 seconds, works once, and only for its repo.

Auth state (sessions, tickets, OAuth `state`, caches) is per instance or in the local SQLite file, which is one reason production runs a single task ([KNOWN_ISSUES #7](KNOWN_ISSUES.md)).

## Deployment

The AWS stack in ap-south-1 is defined in Terraform in [infra/](../infra/): one root module, 63 resources, state in S3 with native locking. It replaced the Console-built stack on 4 Nov 2026 ([rebuild log](../infra/README.md#rebuild-log)).

- **Path:** CloudFront serves the dashboard from S3 (OAC) and forwards `/api`, `/auth`, `/ws`, `/webhook` and `/healthz` to the ALB `cra-alb` with the `X-Origin-Verify` header. The ALB forwards to one Fargate task of service `cra-service` in `cra-cluster`, which mounts EFS at `/data`.
- **Security groups:** `cra-alb-sg` accepts port 80 only from CloudFront's origin-facing prefix list, and its egress is TCP 8000 to `cra-task-sg` only. `cra-task-sg` accepts 8000 from the ALB and has open egress (no NAT or VPC endpoints). `cra-efs-sg` accepts 2049 from the task and has no egress.
- **EFS policy:** only `cra-ecs-task-role` may mount and write, only through the access point `cra-data-ap` (uid 1000, no root access), and only over TLS.
- **Secrets:** `code-review-agent/prod` is created as a container, and its values are put by hand, so they're never in code or state. `code-review-agent/origin-verify` is generated by Terraform, because CloudFront's config has to contain it anyway.
- **CI and Terraform:** `deploy-app` registers new task-definition revisions with the git-SHA image, and the service ignores `task_definition`, so `terraform plan` stays at "No changes" after a deploy.
- **Kept outside Terraform:** the GitHub OIDC provider, the `cra-gha-evals` role (the Eval gate must work while the stack is down), the Bedrock guardrail `odz2vmufjpud` (its measured config would be lost on a rebuild) and the state bucket.

A rebuild creates a new EFS, so the SQLite review history starts empty ([KNOWN_ISSUES #21](KNOWN_ISSUES.md)). The knowledge graph rebuilds on the next push to the default branch, and the Qdrant vectors live in Qdrant Cloud, so they survive.

## Shutdown

On `SIGTERM`, uvicorn gets up to 90 s to finish (`--timeout-graceful-shutdown 90`; the ECS stop timeout and compose's `stop_grace_period` are 120 s). Reviews still pending or running are marked `interrupted`, and their GitHub check is set to error with "push again to re-run". The same cleanup runs at startup for anything a crash left behind.
