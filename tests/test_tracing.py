"""LangSmith tracing (tools/tracing.py): off by default; when on, one trace
per review with agents and Groq calls nested, and token usage on LLM runs.
A MagicMock stands in for the LangSmith client, so nothing leaves the test."""
import asyncio
from unittest.mock import MagicMock

import pytest
from langsmith.run_helpers import tracing_context

import tools.model_client as mc
from models.findings import AgentReport
from pipeline import runner
from tools.model_client import model_client
from tools.tracing import traceable


def _groq_response():
    resp = MagicMock(status_code=200, headers={})
    resp.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "[]"}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 50,
                  "completion_tokens_details": {"reasoning_tokens": 30}},
    }
    return resp


@pytest.fixture
def groq(monkeypatch):
    monkeypatch.setattr(mc.requests, "post", MagicMock(return_value=_groq_response()))


def _runs(client: MagicMock) -> list[dict]:
    """Every run the tracer created, by name, merged with its final update."""
    runs = {}
    for call in client.create_run.call_args_list:
        runs[call.kwargs.get("id")] = dict(call.kwargs)
    for call in client.update_run.call_args_list:
        run_id = call.args[0] if call.args else call.kwargs.get("run_id")
        runs.setdefault(run_id, {}).update({k: v for k, v in call.kwargs.items() if v is not None})
    return list(runs.values())


def test_tracing_is_off_by_default(groq):
    client = MagicMock()
    with tracing_context(client=client):  # no enabled=True: env says false
        model_client.reason(system="s", user="u", task="security")
    assert not client.create_run.called


def test_groq_call_is_an_llm_run_with_messages_and_token_usage(groq):
    client = MagicMock()
    with tracing_context(enabled=True, client=client):
        model_client.reason(system="be strict", user="the diff", task="security")
    (run,) = [r for r in _runs(client) if r.get("name") == "groq"]
    assert run["run_type"] == "llm"
    assert run["inputs"]["messages"] == [{"role": "system", "content": "be strict"},
                                         {"role": "user", "content": "the diff"}]
    assert run["inputs"]["task"] == "security"
    meta = run["extra"]["metadata"]
    assert meta["ls_provider"] == "groq" and meta["finish_reason"] == "stop"
    usage = meta.get("usage_metadata") or run.get("outputs", {}).get("usage_metadata")
    assert usage["input_tokens"] == 120 and usage["output_tokens"] == 50 and usage["total_tokens"] == 170


def test_agents_nest_under_the_review_and_llm_calls_under_the_agent(groq):
    def run_security_agent(changed_file, diff_snippet, retrieved_context):
        model_client.reason(system="s", user=diff_snippet, task="security")
        return AgentReport(agent_name="security")
    run_security_agent.__module__ = "agents.security_agent"

    @traceable(name="review", run_type="chain")
    async def review():
        return await runner.run_agents_parallel("app.py", "+x = 1", "", agent_functions=[run_security_agent])

    client = MagicMock()
    with tracing_context(enabled=True, client=client):
        asyncio.run(review())

    runs = {r["name"]: r for r in _runs(client) if r.get("name")}
    assert set(runs) >= {"review", "security_agent", "groq"}
    assert runs["security_agent"]["parent_run_id"] == runs["review"]["id"]
    assert runs["groq"]["parent_run_id"] == runs["security_agent"]["id"]
    assert "agent" in runs["security_agent"]["tags"]