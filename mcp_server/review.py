"""review_patch: one file's unified-diff patch through the production
pipeline, without GitHub: file filter -> optional guardrail screen ->
call-graph context -> the agents (run_agents_parallel) -> build_review_report
-> evaluate_hitl_gate. No synthesis call: the structured result is the answer.
"""
import asyncio
import time

from agents.documentation_agent import run_documentation_agent
from pipeline.file_filter import plan_review_files
from pipeline.guardrail import guardrail_enabled, quarantine_report, screen_patch
from pipeline.hitl_gate import evaluate_hitl_gate
from pipeline.runner import run_agents_parallel
from pipeline.synthesizer import build_review_report
from rag.knowledge_graph import KnowledgeGraph
from rag.retriever import SmartRetriever
from tools.code_tools import extract_functions, strip_diff_to_source
from tools.model_client import count_llm_calls, provider_chain

from mcp_server.errors import ToolInputError, require_aws_credentials
from mcp_server.repos import get_graph, repo_for_file

MAX_FINDINGS = 25
TEXT_CHARS = 300
LOCAL_REPO = "local"


def _short(text: str | None) -> str | None:
    if not text:
        return text
    return text if len(text) <= TEXT_CHARS else text[:TEXT_CHARS] + " …"


def _context_string(repo: str | None, changed_file: str, patch: str, warnings: list[str]) -> str:
    """The agents' retrieved context, as in the webhook but call-graph only
    (no vector search): the clone's graph when the file is in a configured
    repo, else an empty one (just the file name)."""
    kg = KnowledgeGraph()
    if repo:
        try:
            kg = get_graph(repo).kg
        except Exception as e:
            warnings.append(f"no call-graph context: {e}")
    retriever = SmartRetriever(kg, None, indexer_error="not used by review_patch")
    names = [fn.name for fn in extract_functions(strip_diff_to_source(patch))]
    context = retriever.retrieve_for_diff(repo=repo or LOCAL_REPO, changed_file=changed_file,
                                          diff_snippet=patch, changed_fn_names=names)
    return retriever.build_agent_context_string(context)


def _finding(f) -> dict:
    return {
        "category": f.category.value, "severity": f.severity.value, "title": f.title,
        "lines": f"{f.location.line_start}-{f.location.line_end}",
        "description": _short(f.description), "suggestion": _short(f.suggestion),
        "confidence": f.confidence,
    }


async def review_patch(file_path: str, patch: str, use_guardrail: bool = False) -> dict:
    started = time.monotonic()
    if not file_path.strip():
        raise ToolInputError("file_path is empty.")
    if not patch.strip():
        raise ToolInputError("patch is empty: pass the file's unified diff (the @@ hunks).")
    repo, changed_file = repo_for_file(file_path)
    base = {"file": changed_file, "context_repo": repo}

    plan = plan_review_files([{"filename": changed_file, "patch": patch, "status": "modified"}])
    if plan.skipped:
        return {**base, "skipped": True, "reason": plan.skipped[0].reason, "would_block": False,
                "findings": []}
    target = plan.to_review[0]

    guardrail_on = use_guardrail and guardrail_enabled()
    if "bedrock" in provider_chain() or guardrail_on:
        require_aws_credentials()

    warnings: list[str] = []
    if use_guardrail and not guardrail_on:
        warnings.append("guardrail not configured (GUARDRAIL_ID is empty); the patch was not screened")

    with count_llm_calls() as llm:
        reports, quarantined = [], False
        if guardrail_on:
            screen = await asyncio.to_thread(screen_patch, changed_file, patch)
            if screen.blocked:
                reports, quarantined = [quarantine_report(changed_file, patch, screen.detail)], True
            elif screen.error:
                warnings.append(f"guardrail unavailable: {screen.error}")
        if not quarantined:
            context = await asyncio.to_thread(_context_string, repo, changed_file, patch, warnings)
            reports = await run_agents_parallel(
                changed_file=changed_file, diff_snippet=patch, retrieved_context=context,
                agent_functions=[run_documentation_agent] if target.docs_only else None,
            )
            failed = [r for r in reports if r.error]
            if len(failed) == len(reports):
                # Every agent failed (e.g. no LLM access): no findings here does
                # not mean a clean patch, so don't report it as one.
                raise ToolInputError("No review: every agent failed. "
                                     + "; ".join(f"{r.agent_name}: {r.error}" for r in failed))
            warnings += [f"{r.agent_name} failed: {r.error}" for r in failed]

    review = build_review_report(pr_number=0, repo=repo or LOCAL_REPO, commit_sha=LOCAL_REPO,
                                 agent_reports=reports)
    gate = evaluate_hitl_gate(review)
    return {
        **base,
        "skipped": False,
        "would_block": gate["should_block"],
        "gate": gate["description"],
        "quarantined": quarantined,
        "critical_count": review.critical_count,
        "high_count": review.high_count,
        "findings": [_finding(f) for f in review.all_findings[:MAX_FINDINGS]],
        "findings_total": len(review.all_findings),
        "agents": [{"agent": r.agent_name, "findings": len(r.findings), "error": r.error} for r in reports],
        "warnings": warnings,
        "llm": {"calls": llm.calls, "prompt_tokens": llm.prompt_tokens,
                "completion_tokens": llm.completion_tokens, "cost_usd": round(llm.cost_usd, 6)},
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
