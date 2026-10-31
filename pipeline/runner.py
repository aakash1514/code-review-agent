import asyncio, time
from models.findings import AgentReport
from tools.code_tools import reanchor_finding_lines
from tools.logs import log_context, log_event
from tools.tracing import traceable
from agents.security_agent import run_security_agent
from agents.architecture_agent import run_architecture_agent
from agents.test_coverage_agent import run_test_coverage_agent
from agents.documentation_agent import run_documentation_agent

def _traced(agent_fn, agent_name: str):
    """The agent as its own run in the review's LangSmith trace (a no-op when
    tracing is off). asyncio.to_thread copies the context, so the run nests
    under the review and the agent's LLM calls nest under it."""
    return traceable(name=agent_name, run_type="chain", tags=["agent"])(agent_fn)


AGENT_FUNCTIONS = [run_security_agent, run_architecture_agent, run_test_coverage_agent, run_documentation_agent]


async def run_agents_parallel(changed_file: str, diff_snippet: str, retrieved_context: str,
                                timeout_seconds: float = 45.0, on_agent_complete=None,
                                agent_functions=None) -> list[AgentReport]:
    """Thread-pool concurrency (agents are sync/requests-based, not true async I/O).
    on_agent_complete: optional async callback(AgentReport) -> None, fired as soon as
    EACH agent finishes (not just once all four are done) — used to stream live
    progress to the dashboard over WebSocket. Any exception it raises is swallowed;
    progress reporting must never break the actual pipeline.
    agent_functions: which agents to run (default: all four); docs-only files
    pass just the documentation agent.
    Agents run via asyncio.to_thread, which (unlike run_in_executor) copies
    the caller's context into the thread, so model_client's per-review LLM
    call counter sees their calls.
    Every agent's findings get their lines re-anchored to their snippet in
    diff_snippet (agents guess line numbers; KNOWN_ISSUES #10) before
    on_agent_complete sees them, so PR comments and the dashboard agree."""
    moved = total = 0

    async def _run_one(agent_fn) -> AgentReport:
        nonlocal moved, total
        agent_name = agent_fn.__module__.split(".")[-1]
        start = time.monotonic()
        # gather() runs each _run_one in its own task (its own context copy),
        # so this agent field never leaks into the other agents' log lines.
        with log_context(agent=agent_name, file=changed_file):
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(_traced(agent_fn, agent_name), changed_file, diff_snippet, retrieved_context),
                    timeout=timeout_seconds)
            except asyncio.TimeoutError:
                result = AgentReport(agent_name=agent_name, findings=[], summary=f"Agent timed out after {timeout_seconds}s.",
                                      runtime_ms=int((time.monotonic() - start) * 1000), error="timeout")
            except Exception as e:
                result = AgentReport(agent_name=agent_name, findings=[], summary="Agent failed with an unexpected error.",
                                      runtime_ms=int((time.monotonic() - start) * 1000), error=f"{type(e).__name__}: {e}")
            if result.findings:
                anchored = reanchor_finding_lines(result.findings, diff_snippet)
                moved += sum((a.location.line_start, a.location.line_end) != (f.location.line_start, f.location.line_end)
                             for a, f in zip(anchored, result.findings))
                total += len(anchored)
                result = result.model_copy(update={"findings": anchored})
            log_event("agent_finished", duration_ms=int((time.monotonic() - start) * 1000),
                      findings=len(result.findings), error=result.error)
        if on_agent_complete:
            try:
                await on_agent_complete(result)
            except Exception:
                pass
        return result

    tasks = [_run_one(fn) for fn in (agent_functions or AGENT_FUNCTIONS)]
    reports = await asyncio.gather(*tasks)
    print(f"    [runner] re-anchored {moved}/{total} finding(s) in {changed_file}")
    return reports