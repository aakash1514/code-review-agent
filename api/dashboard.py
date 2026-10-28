from fastapi import APIRouter, HTTPException, Query, Depends
import json
from pipeline.job_store import list_jobs, get_job, set_override, clear_override
from tools.github_tools import (
    get_installation_token, get_pr, get_pr_files, get_full_file_content,
    check_repo_write_permission, set_commit_status,
)
from rag.knowledge_graph import KnowledgeGraph, kg_path_for_repo
from pipeline.hitl_gate import DEFAULT_STATUS_CONTEXT
from api.auth import get_current_user, require_repo_read, validate_repo_name
from pipeline.session_store import create_ws_ticket, WS_TICKET_TTL_SECONDS

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/reviews")
def list_reviews(repo: str = Query(..., description="owner/name"), limit: int = 20,
                 user: dict = Depends(require_repo_read)):
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
            "override_action": job["override_action"],
        })
    return {"repo": repo, "reviews": summaries}


@router.get("/reviews/{commit_sha}")
def get_review(commit_sha: str, repo: str = Query(...), user: dict = Depends(require_repo_read)):
    job = get_job(repo, commit_sha)
    if job is None:
        raise HTTPException(status_code=404, detail="review not found")

    result = json.loads(job["result_json"]) if job["result_json"] else None
    return {
        "commit_sha": job["commit_sha"],
        "pr_number": job["pr_number"],
        "status": job["status"],
        "error": job["error"],
        "review": result,
        "override_action": job["override_action"],
        "override_by": job["override_by"],
    }


@router.get("/reviews/{commit_sha}/files")
def get_review_files(commit_sha: str, repo: str = Query(...), user: dict = Depends(require_repo_read)):
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
            original = ""

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


@router.get("/graph")
def get_graph(repo: str = Query(...), user: dict = Depends(require_repo_read)):
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
            if called in kg.nodes:
                edges.append({"source": key, "target": called})

    return {"repo": repo, "nodes": nodes, "edges": edges}


@router.post("/ws-ticket")
def issue_ws_ticket(repo: str = Query(...), user: dict = Depends(require_repo_read)):
    """Trades a Bearer session for a single-use, short-lived ticket the
    dashboard passes as /ws/reviews?ticket=... — see create_ws_ticket."""
    ticket = create_ws_ticket(user["github_login"], repo)
    return {"ticket": ticket, "expires_in": WS_TICKET_TTL_SECONDS}


@router.post("/reviews/{commit_sha}/approve")
def approve_review(commit_sha: str, repo: str = Query(...), user: dict = Depends(get_current_user)):
    """
    gap #4 CLOSED: requires a valid dashboard session (get_current_user) AND
    verifies the logged-in GitHub user actually has write access to THIS
    repo (via the App's own installation token, not the user's OAuth token —
    see check_repo_write_permission's docstring) before touching anything.
    Only then does it call set_commit_status for real.
    """
    validate_repo_name(repo)
    job = get_job(repo, commit_sha)
    if job is None:
        raise HTTPException(status_code=404, detail="review not found")

    login = user["github_login"]
    token = get_installation_token()
    if not check_repo_write_permission(repo, login, token=token):
        raise HTTPException(status_code=403, detail=f"@{login} does not have write access to {repo}")

    set_commit_status(
        repo, commit_sha, "success", f"Manually approved by @{login}",
        context=DEFAULT_STATUS_CONTEXT, token=token,
    )
    set_override(repo, commit_sha, "approved", login)
    return {"status": "approved", "by": login}


@router.post("/reviews/{commit_sha}/block")
def block_review(commit_sha: str, repo: str = Query(...), user: dict = Depends(get_current_user)):
    """Same auth/permission gate as approve_review above."""
    validate_repo_name(repo)
    job = get_job(repo, commit_sha)
    if job is None:
        raise HTTPException(status_code=404, detail="review not found")

    login = user["github_login"]
    token = get_installation_token()
    if not check_repo_write_permission(repo, login, token=token):
        raise HTTPException(status_code=403, detail=f"@{login} does not have write access to {repo}")

    set_commit_status(
        repo, commit_sha, "failure", f"Manually blocked by @{login}",
        context=DEFAULT_STATUS_CONTEXT, token=token,
    )
    set_override(repo, commit_sha, "blocked", login)
    return {"status": "blocked", "by": login}


@router.post("/reviews/{commit_sha}/clear-override")
def clear_review_override(commit_sha: str, repo: str = Query(...), user: dict = Depends(get_current_user)):
    """Backs 'Change decision' in the dashboard — same auth gate as
    approve/block, since undoing a decision is itself a repo-write-adjacent
    action worth gating the same way."""
    validate_repo_name(repo)
    job = get_job(repo, commit_sha)
    if job is None:
        raise HTTPException(status_code=404, detail="review not found")

    login = user["github_login"]
    token = get_installation_token()
    if not check_repo_write_permission(repo, login, token=token):
        raise HTTPException(status_code=403, detail=f"@{login} does not have write access to {repo}")

    clear_override(repo, commit_sha)
    return {"status": "cleared"}