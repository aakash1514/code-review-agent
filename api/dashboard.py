from fastapi import APIRouter, HTTPException, Query
import json
from rag.knowledge_graph import KnowledgeGraph, kg_path_for_repo
from pipeline.job_store import list_jobs, get_job
from tools.github_tools import get_installation_token, get_pr, get_pr_files, get_full_file_content

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/reviews")
def list_reviews(repo: str = Query(..., description="owner/name"), limit: int = 20):
    """
    Summary list for the dashboard's landing view. Deliberately lightweight
    — parses just enough of result_json (when present) to show status at a
    glance, without shipping the full findings payload for every row.
    """
    jobs = list_jobs(repo, limit=limit)
    summaries = []
    for job in jobs:
        critical_count = None
        if job["result_json"]:
            try:
                critical_count = json.loads(job["result_json"]).get("critical_count")
            except json.JSONDecodeError:
                pass
        summaries.append({
            "commit_sha": job["commit_sha"],
            "pr_number": job["pr_number"],
            "status": job["status"],
            "critical_count": critical_count,
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "error": job["error"],
        })
    return {"repo": repo, "reviews": summaries}


@router.get("/reviews/{commit_sha}")
def get_review(commit_sha: str, repo: str = Query(...)):
    """Full ReviewReport for one commit — the detail view's data source."""
    job = get_job(repo, commit_sha)
    if job is None:
        raise HTTPException(status_code=404, detail="review not found")

    result = json.loads(job["result_json"]) if job["result_json"] else None
    return {
        "commit_sha": job["commit_sha"],
        "pr_number": job["pr_number"],
        "status": job["status"],
        "error": job["error"],
        "review": result,  # full ReviewReport dict, or null if not complete yet
    }


@router.get("/reviews/{commit_sha}/files")
def get_review_files(commit_sha: str, repo: str = Query(...)):
    """
    Full original (base) and modified (head) file content per changed file —
    data source for the dashboard's side-by-side Monaco diff viewer. Fetched
    live from GitHub each time, not persisted at review time.

    NOTE — known caveat, not a bug: base.sha/head.sha come from the PR's
    CURRENT state via get_pr, not frozen at commit_sha. If the PR has moved
    since this commit was reviewed, this may not exactly match what was
    reviewed. Same spirit as is_stale_sha elsewhere in this project — noted
    explicitly rather than left as a silent mismatch.
    """
    job = get_job(repo, commit_sha)
    if job is None:
        raise HTTPException(status_code=404, detail="review not found")

    try:
        token = get_installation_token()
        pr = get_pr(repo, job["pr_number"], token)
        base_sha = pr["base"]["sha"]
        head_sha = pr["head"]["sha"]
        files = get_pr_files(repo, job["pr_number"], token)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"failed to fetch PR metadata from GitHub: {e}")

    result_files = []
    for f in files:
        if not f.get("patch"):
            continue
        filename = f["filename"]
        status = f.get("status")

        try:
            original = "" if status == "added" else get_full_file_content(repo, filename, base_sha, token)
        except Exception:
            original = ""  # e.g. renamed-from path not resolvable, or a transient fetch error

        try:
            modified = "" if status == "removed" else get_full_file_content(repo, filename, head_sha, token)
        except Exception:
            modified = ""

        result_files.append({
            "filename": filename,
            "status": status,
            "original": original,
            "modified": modified,
        })

    return {"commit_sha": commit_sha, "files": result_files}


@router.post("/reviews/{commit_sha}/approve")
def approve_review(commit_sha: str, repo: str = Query(...)):
    """
    NOTE — gap #4, intentionally NOT closed here: this endpoint has NO
    auth check yet. Anyone who can reach your FastAPI server can call this
    and flip a PR's status to passing. This is fine for local/ngrok testing
    where only you can reach it, but this must NOT go to production without
    GitHub OAuth login + write-access verification wired in first — that's
    the real gap #4 fix, deliberately deferred until the frontend/auth flow
    is being built, not silently skipped.
    """
    job = get_job(repo, commit_sha)
    if job is None:
        raise HTTPException(status_code=404, detail="review not found")
    # TODO: actually call tools.github_tools.set_commit_status(..., "success", ...)
    # once this endpoint is auth-gated. Left as a stub for now on purpose.
    return {"status": "not_implemented", "reason": "gap #4: auth required before this can act for real"}


@router.post("/reviews/{commit_sha}/block")
def block_review(commit_sha: str, repo: str = Query(...)):
    """Same gap #4 caveat as approve_review above."""
    job = get_job(repo, commit_sha)
    if job is None:
        raise HTTPException(status_code=404, detail="review not found")
    return {"status": "not_implemented", "reason": "gap #4: auth required before this can act for real"}

@router.get("/graph")
def get_graph(repo: str = Query(...)):
    """
    Full call graph for the repo's most recently indexed knowledge graph.
    NOT scoped to a specific commit/review — the KG is repo-wide and only
    updates when scripts/index_repo.py is manually re-run (gap #6, still not
    started). The frontend combines this with the currently open review's
    findings client-side to highlight relevant nodes.
    """
    kg = KnowledgeGraph()
    try:
        kg.load(kg_path_for_repo(repo))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"no knowledge graph indexed yet for {repo}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to load knowledge graph: {e}")

    nodes = [
        {"id": key, "name": node.name, "file": node.file_path}
        for key, node in kg.nodes.items()
    ]
    edges = []
    for key, node in kg.nodes.items():
        for called in node.calls:
            if called in kg.nodes:  # guard against dangling refs
                edges.append({"source": key, "target": called})

    return {"repo": repo, "nodes": nodes, "edges": edges}
