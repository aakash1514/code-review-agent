"""Bedrock (Nova 2 Lite, Converse API) as the LLM provider, with Groq as
fallback (tools/model_client.py, tools/bedrock_llm.py). boto3 and requests
are mocked; nothing leaves the test."""
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from langsmith.run_helpers import tracing_context

import rag.embeddings
import tools.bedrock_llm as bedrock_llm
import tools.model_client as mc
from api.webhook import _review_metrics
from tools.model_client import ModelUnavailableError, llm_cost_usd, model_client, provider_chain
from tools.rate_limit import RateLimiter

NOVA = "global.amazon.nova-2-lite-v1:0"


def _converse(text='[]', stop="end_turn", input_tokens=1000, output_tokens=400, reasoning=True):
    content = ([{"reasoningContent": {"reasoningText": {"text": "Let me think about the diff..."}}}]
               if reasoning else []) + ([{"text": text}] if text is not None else [])
    return {"output": {"message": {"role": "assistant", "content": content}}, "stopReason": stop,
            "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens,
                      "totalTokens": input_tokens + output_tokens},
            "metrics": {"latencyMs": 2000}}


def _groq_ok(content="[]"):
    resp = MagicMock(status_code=200, headers={})
    resp.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "completion_tokens_details": {"reasoning_tokens": 30}},
    }
    return resp


class CountingLimiter:
    def __init__(self):
        self.waits = 0

    def wait(self):
        self.waits += 1
        return 0.0


@pytest.fixture
def bedrock(monkeypatch, settings):
    """LLM_PROVIDER=bedrock with a mocked client and limiter; Groq mocked too."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "bedrock")
    monkeypatch.setattr(settings, "LLM_FALLBACK", "groq")
    monkeypatch.setattr(settings, "BEDROCK_REASONING_EFFORTS", "")
    monkeypatch.setattr(settings, "GROQ_MODEL_REASONING", "openai/gpt-oss-120b")
    monkeypatch.setattr(settings, "GROQ_MODEL_LIGHT", "openai/gpt-oss-20b")
    client = MagicMock()
    client.converse.return_value = _converse()
    limiter = CountingLimiter()
    monkeypatch.setattr(bedrock_llm, "_client", client)
    monkeypatch.setattr(bedrock_llm, "_limiter", limiter)
    post = MagicMock(return_value=_groq_ok('[{"from": "groq"}]'))
    monkeypatch.setattr(mc.requests, "post", post)
    return SimpleBedrock(client, limiter, post)


class SimpleBedrock:
    def __init__(self, client, limiter, groq_post):
        self.client, self.limiter, self.groq_post = client, limiter, groq_post


# 1. text extraction ---------------------------------------------------------------

def test_answer_skips_reasoning_blocks(bedrock):
    bedrock.client.converse.return_value = _converse(text='[{"title": "SQL injection"}]')
    assert model_client.reason(system="s", user="u", task="security") == '[{"title": "SQL injection"}]'
    assert not bedrock.groq_post.called


def test_answer_text_joins_text_blocks_only():
    response = {"output": {"message": {"content": [
        {"reasoningContent": {"reasoningText": {"text": "thinking"}}}, {"text": "[{"}, {"text": "}]"}]}}}
    assert bedrock_llm.answer_text(response) == "[{}]"
    assert bedrock_llm.answer_text({}) == ""


# 2. reasoning effort per task, and the override ----------------------------------------

@pytest.mark.parametrize("task, effort", [
    ("security", "medium"), ("architecture", "medium"),
    ("synthesis", "low"), ("test_coverage", "low"), ("documentation", "low"),
])
def test_reasoning_config_per_task(bedrock, task, effort):
    model_client.reason(system="sys", user="usr", task=task, max_tokens=2000)
    kwargs = bedrock.client.converse.call_args.kwargs
    assert kwargs["modelId"] == NOVA
    assert kwargs["system"] == [{"text": "sys"}]
    assert kwargs["messages"] == [{"role": "user", "content": [{"text": "usr"}]}]
    assert kwargs["additionalModelRequestFields"] == {
        "reasoningConfig": {"type": "enabled", "maxReasoningEffort": effort}}
    assert kwargs["inferenceConfig"]["maxTokens"] == 2000 + bedrock_llm.REASONING_TOKEN_ALLOWANCE[effort]


def test_effort_override_from_settings(bedrock, settings, monkeypatch):
    monkeypatch.setattr(settings, "BEDROCK_REASONING_EFFORTS", "security:low, architecture:high")
    assert bedrock_llm.task_efforts() == {**bedrock_llm.DEFAULT_TASK_EFFORT,
                                          "security": "low", "architecture": "high"}
    model_client.reason(system="s", user="u", task="security")
    effort = bedrock.client.converse.call_args.kwargs["additionalModelRequestFields"]["reasoningConfig"]
    assert effort["maxReasoningEffort"] == "low"


@pytest.mark.parametrize("bad", ["security", "security:off", ":low", "security:LOUD"])
def test_bad_effort_override_is_rejected(settings, monkeypatch, bad):
    monkeypatch.setattr(settings, "BEDROCK_REASONING_EFFORTS", bad)
    with pytest.raises(ValueError, match="BEDROCK_REASONING_EFFORTS"):
        bedrock_llm.task_efforts()


# 3-5. fallback ----------------------------------------------------------------------

def test_max_tokens_stop_is_incomplete_and_falls_back_to_groq(bedrock, capsys):
    bedrock.client.converse.return_value = _converse(text='[{"title": "cut', stop="max_tokens")
    with mc.count_llm_calls() as counter:
        assert model_client.reason(system="s", user="u", task="security") == '[{"from": "groq"}]'
    assert counter.fallbacks == 1
    out = capsys.readouterr().out
    assert "[model_client] provider bedrock failed for task=security: IncompleteResponseError" in out
    assert "trying groq" in out


def test_empty_text_is_incomplete(bedrock):
    bedrock.client.converse.return_value = _converse(text=None)
    assert model_client.reason(system="s", user="u", task="documentation") == '[{"from": "groq"}]'


def test_throttling_after_retries_falls_back_and_is_counted(bedrock):
    bedrock.client.converse.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Too many requests"}}, "Converse")
    with mc.count_llm_calls() as counter:
        assert model_client.reason(system="s", user="u", task="architecture") == '[{"from": "groq"}]'
    assert counter.fallbacks == 1
    assert bedrock.groq_post.call_args.kwargs["json"]["model"] == "openai/gpt-oss-120b"


def test_no_fallback_raises_model_unavailable(bedrock, settings, monkeypatch):
    monkeypatch.setattr(settings, "LLM_FALLBACK", "none")
    bedrock.client.converse.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Too many requests"}}, "Converse")
    with pytest.raises(ModelUnavailableError) as exc:
        model_client.reason(system="s", user="u", task="security")
    assert isinstance(exc.value.__cause__, ClientError)
    assert not bedrock.groq_post.called


def test_provider_chain(settings, monkeypatch):
    for primary, fallback, want in [("groq", "groq", ["groq"]), ("bedrock", "groq", ["bedrock", "groq"]),
                                    ("bedrock", "none", ["bedrock"]), ("groq", "bedrock", ["groq", "bedrock"]),
                                    ("BEDROCK", "none", ["bedrock"])]:
        monkeypatch.setattr(settings, "LLM_PROVIDER", primary)
        monkeypatch.setattr(settings, "LLM_FALLBACK", fallback)
        assert provider_chain() == want
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    with pytest.raises(ValueError, match="unknown LLM provider"):
        provider_chain()


# 6. cost ------------------------------------------------------------------------------

def test_cost_math():
    assert llm_cost_usd(NOVA, 1_000_000, 0) == pytest.approx(0.30)
    assert llm_cost_usd(NOVA, 0, 1_000_000) == pytest.approx(2.50)
    assert llm_cost_usd(NOVA, 4000, 2000) == pytest.approx(0.0012 + 0.005)
    assert llm_cost_usd("openai/gpt-oss-120b", 10_000, 10_000) == 0.0


def test_counter_sums_bedrock_tokens_and_cost(bedrock):
    with mc.count_llm_calls() as counter:
        model_client.reason(system="s", user="u", task="security")
        model_client.reason(system="s", user="u", task="documentation")
    assert counter.calls == 2
    assert (counter.prompt_tokens, counter.completion_tokens) == (2000, 800)
    assert counter.reasoning_tokens == 0  # Bedrock doesn't report it separately
    assert counter.cost_usd == pytest.approx(2 * (1000 * 0.30 + 400 * 2.50) / 1e6)


def test_bedrock_call_is_logged(bedrock, capsys):
    model_client.reason(system="s", user="u", task="test_coverage")
    assert f"[bedrock] task=test_coverage model={NOVA} effort=low stop=end_turn tokens: prompt=1000 output=400" \
        in capsys.readouterr().out


# 7. pacing ----------------------------------------------------------------------------

def test_limiter_is_waited_on_before_every_call(bedrock):
    for task in ("security", "architecture", "documentation"):
        model_client.reason(system="s", user="u", task=task)
    assert bedrock.limiter.waits == bedrock.client.converse.call_count == 3


def test_pacing_wait_is_counted(bedrock):
    bedrock.limiter.wait = lambda: 2.5
    with mc.count_llm_calls() as counter:
        model_client.reason(system="s", user="u", task="security")
    assert counter.pacing_wait_ms == 2500


def test_llm_limiter_is_its_own_instance(monkeypatch, settings):
    monkeypatch.setattr(bedrock_llm, "_limiter", None)
    monkeypatch.setattr(settings, "BEDROCK_LLM_MAX_RPM", "18")
    llm = bedrock_llm.get_limiter()
    assert isinstance(llm, RateLimiter) and llm is bedrock_llm.get_limiter()
    assert llm.interval == pytest.approx(60 / 18)
    embedder = rag.embeddings.BedrockEmbedder(client=MagicMock(), max_rpm=50)
    assert embedder._limiter is not llm and embedder._limiter.interval == pytest.approx(1.2)


def test_client_is_reused_with_adaptive_retries(monkeypatch, settings):
    import boto3
    monkeypatch.setattr(bedrock_llm, "_client", None)
    monkeypatch.setattr(settings, "BEDROCK_LLM_REGION", "")
    monkeypatch.setattr(settings, "AWS_REGION", "ap-south-1", raising=False)
    made = MagicMock()
    monkeypatch.setattr(boto3, "client", made)
    assert bedrock_llm.get_client() is bedrock_llm.get_client()
    made.assert_called_once()
    args, kwargs = made.call_args
    assert args == ("bedrock-runtime",) and kwargs["region_name"] == "ap-south-1"
    assert kwargs["config"].retries == {"mode": "adaptive", "max_attempts": 10}
    assert kwargs["config"].read_timeout == 120


# 9. metrics ---------------------------------------------------------------------------

def test_llm_cost_is_an_emf_metric(bedrock):
    with mc.count_llm_calls() as counter:
        model_client.reason(system="s", user="u", task="security")
    m = _review_metrics({"status": "complete"}, None, 1000, counter)
    value, unit = m["LLMCostUSD"]
    assert unit == "None" and value == pytest.approx((1000 * 0.30 + 400 * 2.50) / 1e6)
    assert _review_metrics({}, None, 1, mc.LLMCallCounter())["LLMCostUSD"] == (0.0, "None")


# 10. tracing --------------------------------------------------------------------------

def test_bedrock_call_is_an_llm_run_with_provider_and_usage(bedrock):
    from tests.test_tracing import _runs
    client = MagicMock()
    with tracing_context(enabled=True, client=client):
        model_client.reason(system="be strict", user="the diff", task="security")
    (run,) = [r for r in _runs(client) if r.get("name") == "bedrock"]
    assert run["run_type"] == "llm"
    assert run["inputs"]["messages"][1] == {"role": "user", "content": "the diff"}
    meta = run["extra"]["metadata"]
    assert meta["ls_provider"] == "amazon_bedrock" and meta["ls_model_name"] == NOVA
    assert meta["finish_reason"] == "end_turn"
    usage = meta.get("usage_metadata") or run.get("outputs", {}).get("usage_metadata")
    assert usage["input_tokens"] == 1000 and usage["output_tokens"] == 400 and usage["total_tokens"] == 1400
