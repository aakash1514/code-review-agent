import asyncio, time
from models.findings import AgentReport
from agents.security_agent import run_security_agent
from agents.architecture_agent import run_architecture_agent
from agents.test_coverage_agent import run_test_coverage_agent
from agents.documentation_agent import run_documentation_agent

AGENT_FUNCTIONS = [run_security_agent, run_architecture_agent, run_test_coverage_agent, run_documentation_agent]


async def run_agents_parallel(changed_file: str, diff_snippet: str, retrieved_context: str,
                                timeout_seconds: float = 45.0, on_agent_complete=None) -> list[AgentReport]:
    """Thread-pool concurrency (agents are sync/requests-based, not true async I/O).
    on_agent_complete: optional async callback(AgentReport) -> None, fired as soon as
    EACH agent finishes (not just once all four are done) — used to stream live
    progress to the dashboard over WebSocket. Any exception it raises is swallowed;
    progress reporting must never break the actual pipeline."""
    loop = asyncio.get_running_loop()

    async def _run_one(agent_fn) -> AgentReport:
        agent_name = agent_fn.__module__.split(".")[-1]
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, agent_fn, changed_file, diff_snippet, retrieved_context),
                timeout=timeout_seconds)
        except asyncio.TimeoutError:
            result = AgentReport(agent_name=agent_name, findings=[], summary=f"Agent timed out after {timeout_seconds}s.",
                                  runtime_ms=int((time.monotonic() - start) * 1000), error="timeout")
        except Exception as e:
            result = AgentReport(agent_name=agent_name, findings=[], summary="Agent failed with an unexpected error.",
                                  runtime_ms=int((time.monotonic() - start) * 1000), error=f"{type(e).__name__}: {e}")
        if on_agent_complete:
            try:
                await on_agent_complete(result)
            except Exception:
                pass
        return result

    tasks = [_run_one(fn) for fn in AGENT_FUNCTIONS]
    return await asyncio.gather(*tasks)