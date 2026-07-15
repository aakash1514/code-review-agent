import hashlib, hmac, json, traceback
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from models.findings import AgentReport
from pipeline.job_store import init_db, sweep_stuck_jobs, create_job, update_status, save_result, is_stale_sha
from pipeline.runner import run_agents_parallel
from pipeline.synthesizer import build_review_report, synthesize_markdown
from pipeline.hitl_gate import evaluate_hitl_gate, DEFAULT_STATUS_CONTEXT
from tools.github_tools import get_installation_token, get_pr_diff, get_pr_files, post_pr_review, set_commit_status
from tools.code_tools import extract_functions, strip_diff_to_source
from rag.knowledge_graph import KnowledgeGraph, kg_path_for_repo
from rag.indexer import CodebaseIndexer
from rag.retriever import SmartRetriever
from api.dashboard import router as dashboard_router
from api.ws_manager import manager

HANDLED_ACTIONS = {"opened", "synchronize"}

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    swept = sweep_stuck_jobs()
    if swept:
        print(f"[startup] swept {swept} stuck job(s) from a previous run")
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
app.include_router(dashboard_router)


def _verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(settings.GITHUB_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


def _load_retriever(repo: str) -> SmartRetriever:
    kg = KnowledgeGraph()
    try:
        kg.load(kg_path_for_repo(repo))
    except Exception as e:
        print(f"[webhook] no usable knowledge graph for {repo} ({type(e).__name__}: {e}) — continuing without it")
    indexer = CodebaseIndexer()
    return SmartRetriever(kg, indexer)


@app.websocket("/ws/reviews")
async def reviews_ws(websocket: WebSocket, repo: str):
    """Dashboard connects here per-repo (?repo=owner/name) and receives every
    pipeline progress event for reviews running against that repo. No auth —
    same trust boundary as the rest of the dashboard API for now (gap #4)."""
    await manager.connect(repo, websocket)
    try:
        while True:
            # Client doesn't need to send anything meaningful; this just
            # keeps the connection open and detects disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(repo, websocket)


@app.post("/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    raw_body = await request.body()
    if not _verify_signature(raw_body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=401, detail="invalid signature")

    event = request.headers.get("X-GitHub-Event", "")
    payload = json.loads(raw_body)

    if event == "ping":
        return {"status": "pong"}
    if event != "pull_request":
        return {"status": "ignored", "reason": f"unhandled event: {event}"}
    action = payload.get("action")
    if action not in HANDLED_ACTIONS:
        return {"status": "ignored", "reason": f"unhandled action: {action}"}

    repo = payload["repository"]["full_name"]
    pr_number = payload["pull_request"]["number"]
    commit_sha = payload["pull_request"]["head"]["sha"]

    # NOTE: create_job / duplicate-delivery protection / blocking-call ordering
    # are unchanged from the current baseline — those three fixes (5.1/5.2/5.3)
    # are intentionally deferred, per prior agreement, not part of this pass.
    create_job(repo, pr_number, commit_sha)

    token = get_installation_token()
    set_commit_status(repo, commit_sha, "pending", "AI review in progress...",
                       context=DEFAULT_STATUS_CONTEXT, token=token)

    background_tasks.add_task(run_review_pipeline, repo, pr_number, commit_sha)
    return {"status": "accepted", "repo": repo, "pr_number": pr_number, "commit_sha": commit_sha}


async def run_review_pipeline(repo: str, pr_number: int, commit_sha: str) -> None:
    update_status(repo, commit_sha, "running")
    await manager.broadcast(repo, {"commit_sha": commit_sha, "stage": "running", "pr_number": pr_number})

    try:
        token = get_installation_token()

        await manager.broadcast(repo, {"commit_sha": commit_sha, "stage": "fetching_files"})
        files = get_pr_files(repo, pr_number, token)

        retriever = _load_retriever(repo)
        all_agent_reports = []

        async def _on_agent_complete(report: AgentReport) -> None:
            await manager.broadcast(repo, {
                "commit_sha": commit_sha,
                "stage": "agent_complete",
                "agent_name": report.agent_name,
                "finding_count": len(report.findings),
                "error": report.error,
            })

        for f in files:
            patch = f.get("patch")
            if not patch:
                continue
            changed_file = f["filename"]
            await manager.broadcast(repo, {"commit_sha": commit_sha, "stage": "file_started", "file": changed_file})
            changed_fn_names = [fn.name for fn in extract_functions(strip_diff_to_source(patch))]
            context = retriever.retrieve_for_diff(
                repo=repo,
                changed_file=changed_file,
                diff_snippet=patch,
                changed_fn_names=changed_fn_names,
            )
            context_string = retriever.build_agent_context_string(context)
            reports = await run_agents_parallel(
                changed_file=changed_file,
                diff_snippet=patch,
                retrieved_context=context_string,
                on_agent_complete=_on_agent_complete,
            )
            all_agent_reports.extend(reports)

        review = build_review_report(
            pr_number=pr_number, repo=repo, commit_sha=commit_sha, agent_reports=all_agent_reports,
        )

        # NOTE: save_result still runs before synthesize_markdown here — that's
        # known bug 5.1, intentionally left as-is for this pass.
        save_result(repo, commit_sha, review.model_dump_json())
        await manager.broadcast(repo, {
            "commit_sha": commit_sha, "stage": "synthesizing",
            "critical_count": review.critical_count, "high_count": review.high_count,
        })

        if is_stale_sha(repo, pr_number, commit_sha):
            update_status(repo, commit_sha, "failed", error="discarded: superseded by newer commit")
            await manager.broadcast(repo, {"commit_sha": commit_sha, "stage": "failed", "error": "discarded: superseded by newer commit"})
            return

        gate = evaluate_hitl_gate(review)
        try:
            full_diff = get_pr_diff(repo, pr_number, token)
        except Exception:
            full_diff = None
        markdown = synthesize_markdown(review, diff_snippet=full_diff)

        post_pr_review(repo, pr_number, markdown, token=token)
        set_commit_status(repo, commit_sha, gate["state"], gate["description"],
                           context=DEFAULT_STATUS_CONTEXT, token=token)
        await manager.broadcast(repo, {
            "commit_sha": commit_sha, "stage": "complete", "should_block": gate["should_block"],
        })

    except Exception as e:
        traceback.print_exc()
        error_text = f"{type(e).__name__}: {e}"
        update_status(repo, commit_sha, "failed", error=error_text)
        await manager.broadcast(repo, {"commit_sha": commit_sha, "stage": "failed", "error": error_text})
        try:
            token = get_installation_token()
            set_commit_status(
                repo,
                commit_sha,
                "failure",
                f"Review failed: {error_text}"[:140],
                context=DEFAULT_STATUS_CONTEXT,
                token=token,
            )
        except Exception:
            pass