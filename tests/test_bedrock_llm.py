"""Bedrock (Converse API) as the LLM provider, with Groq as fallback
(tools/model_client.py, tools/bedrock_llm.py): gpt-oss-120b/20b by task tier
by default, Nova 2 Lite when BEDROCK_LLM_MODEL selects it. boto3 and
requests are mocked; nothing leaves the test."""
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from langsmith.run_helpers import tracing_context

import rag.embeddings
import tools.bedrock_llm as bedrock_llm
import tools.model_client as mc
from api.webhook import _review_metrics
from tools.llm_routing import REASONING_TOKEN_ALLOWANCE, TASK_REASONING_EFFORT
from tools.model_client import ModelUnavailableError, llm_cost_usd, model_client, provider_chain
from tools.rate_limit import RateLimiter

G120, G20, NOVA = bedrock_llm.GPT_OSS_120B, bedrock_llm.GPT_OSS_20B, bedrock_llm.NOVA_2_LITE


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


class Bedrock:
    def __init__(self, client, limiters, groq_post):
        self.client, self.limiters, self.groq_post = client, limiters, groq_post

    def calls(self):
        return [c.kwargs for c in self.client.converse.call_args_list]


@pytest.fixture
def bedrock(monkeypatch, settings):
    """LLM_PROVIDER=bedrock with default models, a mocked client and one
    counting limiter per model; Groq mocked too."""
    for name, value in {"LLM_PROVIDER": "bedrock", "LLM_FALLBACK": "groq", "BEDROCK_REASONING_EFFORTS": "",
                        "BEDROCK_LLM_MODEL": "", "BEDROCK_LLM_MODEL_REASONING": G120,
                        "BEDROCK_LLM_MODEL_LIGHT": G20, "BEDROCK_LLM_MAX_RPM": "",
                        "GROQ_MODEL_REASONING": "openai/gpt-oss-120b", "GROQ_MODEL_LIGHT": "openai/gpt-oss-20b"}.items():
        monkeypatch.setattr(settings, name, value)
    client = MagicMock()
    client.converse.return_value = _converse()
    limiters = {model: CountingLimiter() for model in (G120, G20, NOVA)}
    monkeypatch.setattr(bedrock_llm, "_client", client)
    monkeypatch.setattr(bedrock_llm, "_limiters", limiters)
    post = MagicMock(return_value=_groq_ok('[{"from": "groq"}]'))
    monkeypatch.setattr(mc.requests, "post", post)
    return Bedrock(client, limiters, post)


@pytest.fixture
def nova(bedrock, settings, monkeypatch):
    monkeypatch.setattr(settings, "BEDROCK_LLM_MODEL", NOVA)
    return bedrock


# routing ------------------------------------------------------------------------------

@pytest.mark.parametrize("task, model", [
    ("security", G120), ("architecture", G120), ("synthesis", G120),
    ("test_coverage", G20), ("documentation", G20),
])
def test_tasks_route_to_gpt_oss_by_tier(bedrock, task, model):
    model_client.reason(system="s", user="u", task=task)
    assert bedrock.calls()[-1]["modelId"] == model


def test_light_tasks_use_120b_when_20b_is_disabled(bedrock, settings, monkeypatch):
    monkeypatch.setattr(settings, "BEDROCK_LLM_MODEL_LIGHT", "none")
    assert bedrock_llm.model_for("documentation") == G120
    model_client.reason(system="s", user="u", task="test_coverage")
    assert bedrock.calls()[-1]["modelId"] == G120


def test_single_model_override_wins_for_every_task(nova):
    for task in ("security", "documentation"):
        model_client.reason(system="s", user="u", task=task)
    assert {c["modelId"] for c in nova.calls()} == {NOVA}


# reasoning field per family, effort maps -----------------------------------------------------

@pytest.mark.parametrize("task", list(TASK_REASONING_EFFORT))
def test_gpt_oss_gets_reasoning_effort_with_the_groq_map(bedrock, task):
    model_client.reason(system="sys", user="usr", task=task, max_tokens=2000)
    kwargs = bedrock.calls()[-1]
    effort = TASK_REASONING_EFFORT[task]
    assert kwargs["additionalModelRequestFields"] == {"reasoning_effort": effort}
    assert kwargs["inferenceConfig"] == {"maxTokens": 2000 + REASONING_TOKEN_ALLOWANCE[effort], "temperature": 0.2}
    assert kwargs["system"] == [{"text": "sys"}]
    assert kwargs["messages"] == [{"role": "user", "content": [{"text": "usr"}]}]


@pytest.mark.parametrize("task", list(TASK_REASONING_EFFORT))
def test_nova_gets_reasoning_config_all_low(nova, task):
    model_client.reason(system="s", user="u", task=task, max_tokens=2000)
    kwargs = nova.calls()[-1]
    assert kwargs["additionalModelRequestFields"] == {
        "reasoningConfig": {"type": "enabled", "maxReasoningEffort": "low"}}
    assert kwargs["inferenceConfig"]["maxTokens"] == 2000 + REASONING_TOKEN_ALLOWANCE["low"]


def test_unknown_family_gets_no_reasoning_field():
    assert bedrock_llm.reasoning_fields("anthropic.some-model-v1:0", "low") == {}
    assert bedrock_llm.family("anthropic.some-model-v1:0") == "other"


def test_effort_override_applies_to_either_family(bedrock, settings, monkeypatch):
    monkeypatch.setattr(settings, "BEDROCK_REASONING_EFFORTS", "security:low, documentation:medium")
    assert bedrock_llm.task_efforts(G120) == {**TASK_REASONING_EFFORT, "security": "low", "documentation": "medium"}
    assert bedrock_llm.task_efforts(NOVA) == {**bedrock_llm.NOVA_TASK_EFFORT, "documentation": "medium"}
    model_client.reason(system="s", user="u", task="security")
    assert bedrock.calls()[-1]["additionalModelRequestFields"] == {"reasoning_effort": "low"}


@pytest.mark.parametrize("bad", ["security", "security:off", ":low", "security:LOUD"])
def test_bad_effort_override_is_rejected(settings, monkeypatch, bad):
    monkeypatch.setattr(settings, "BEDROCK_REASONING_EFFORTS", bad)
    with pytest.raises(ValueError, match="BEDROCK_REASONING_EFFORTS"):
        bedrock_llm.task_efforts(G120)


# text extraction ----------------------------------------------------------------------

def test_answer_skips_reasoning_blocks(bedrock):
    bedrock.client.converse.return_value = _converse(text='[{"title": "SQL injection"}]')
    assert model_client.reason(system="s", user="u", task="security") == '[{"title": "SQL injection"}]'
    assert not bedrock.groq_post.called


def test_answer_text_joins_text_blocks_only():
    response = {"output": {"message": {"content": [
        {"reasoningContent": {"reasoningText": {"text": "thinking"}}}, {"text": "[{"}, {"text": "}]"}]}}}
    assert bedrock_llm.answer_text(response) == "[{}]"
    assert bedrock_llm.answer_text({}) == ""


# fallback -----------------------------------------------------------------------------

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


# cost ---------------------------------------------------------------------------------

@pytest.mark.parametrize("model, per_in, per_out", [
    (G120, 0.1545, 0.6180), (G20, 0.0721, 0.3090), (NOVA, 0.30, 2.50),
])
def test_cost_per_model(model, per_in, per_out):
    assert llm_cost_usd(model, 1_000_000, 0) == pytest.approx(per_in)
    assert llm_cost_usd(model, 0, 1_000_000) == pytest.approx(per_out)
    assert llm_cost_usd(model, 4000, 2000) == pytest.approx((4000 * per_in + 2000 * per_out) / 1e6)


def test_groq_models_are_free_even_with_the_same_name():
    assert llm_cost_usd("openai/gpt-oss-120b", 10_000, 10_000) == 0.0
    assert llm_cost_usd("openai/gpt-oss-20b", 10_000, 10_000) == 0.0


def test_counter_sums_tokens_and_cost_per_model(bedrock):
    with mc.count_llm_calls() as counter:
        model_client.reason(system="s", user="u", task="security")       # 120b
        model_client.reason(system="s", user="u", task="documentation")  # 20b
    assert counter.calls == 2
    assert (counter.prompt_tokens, counter.completion_tokens) == (2000, 800)
    assert counter.reasoning_tokens == 0  # Bedrock doesn't report it separately
    assert counter.cost_usd == pytest.approx(llm_cost_usd(G120, 1000, 400) + llm_cost_usd(G20, 1000, 400))


def test_bedrock_call_is_logged(bedrock, capsys):
    model_client.reason(system="s", user="u", task="test_coverage")
    assert f"[bedrock] task=test_coverage model={G20} effort=low stop=end_turn tokens: prompt=1000 output=400" \
        in capsys.readouterr().out


# pacing -------------------------------------------------------------------------------

def test_each_model_waits_on_its_own_limiter(bedrock):
    for task in ("security", "architecture", "documentation"):
        model_client.reason(system="s", user="u", task=task)
    assert bedrock.limiters[G120].waits == 2 and bedrock.limiters[G20].waits == 1
    assert bedrock.client.converse.call_count == 3


def test_pacing_wait_is_counted(bedrock):
    bedrock.limiters[G120].wait = lambda: 2.5
    with mc.count_llm_calls() as counter:
        model_client.reason(system="s", user="u", task="security")
    assert counter.pacing_wait_ms == 2500


def test_one_limiter_per_model_with_default_rates(monkeypatch, settings):
    monkeypatch.setattr(bedrock_llm, "_limiters", {})
    monkeypatch.setattr(settings, "BEDROCK_LLM_MAX_RPM", "")
    big, small, nova = (bedrock_llm.get_limiter(m) for m in (G120, G20, NOVA))
    assert len({id(big), id(small), id(nova)}) == 3 and big is bedrock_llm.get_limiter(G120)
    assert isinstance(big, RateLimiter)
    assert big.interval == pytest.approx(60 / 90) and small.interval == pytest.approx(60 / 90)
    assert nova.interval == pytest.approx(60 / 18)
    assert bedrock_llm.get_limiter("unknown.model-v1:0").interval == pytest.approx(60 / 18)
    embedder = rag.embeddings.BedrockEmbedder(client=MagicMock(), max_rpm=50)
    assert embedder._limiter not in (big, small, nova)


def test_max_rpm_overrides(settings, monkeypatch):
    monkeypatch.setattr(settings, "BEDROCK_LLM_MAX_RPM", "30")
    assert bedrock_llm.max_rpm(G120) == bedrock_llm.max_rpm(NOVA) == 30
    monkeypatch.setattr(settings, "BEDROCK_LLM_MAX_RPM", f"{G120}=60, {NOVA}=10")
    assert (bedrock_llm.max_rpm(G120), bedrock_llm.max_rpm(NOVA), bedrock_llm.max_rpm(G20)) == (60, 10, 90)


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


# metrics, tracing ---------------------------------------------------------------------

def test_llm_cost_is_an_emf_metric(bedrock):
    with mc.count_llm_calls() as counter:
        model_client.reason(system="s", user="u", task="security")
    m = _review_metrics({"status": "complete"}, None, 1000, counter)
    value, unit = m["LLMCostUSD"]
    assert unit == "None" and value == pytest.approx(llm_cost_usd(G120, 1000, 400), abs=1e-6)
    assert _review_metrics({}, None, 1, mc.LLMCallCounter())["LLMCostUSD"] == (0.0, "None")


def test_bedrock_call_is_an_llm_run_with_provider_and_usage(bedrock):
    from tests.test_tracing import _runs
    client = MagicMock()
    with tracing_context(enabled=True, client=client):
        model_client.reason(system="be strict", user="the diff", task="security")
    (run,) = [r for r in _runs(client) if r.get("name") == "bedrock"]
    assert run["run_type"] == "llm"
    assert run["inputs"]["messages"][1] == {"role": "user", "content": "the diff"}
    meta = run["extra"]["metadata"]
    assert meta["ls_provider"] == "amazon_bedrock" and meta["ls_model_name"] == G120
    assert meta["finish_reason"] == "end_turn"
    usage = meta.get("usage_metadata") or run.get("outputs", {}).get("usage_metadata")
    assert usage["input_tokens"] == 1000 and usage["output_tokens"] == 400 and usage["total_tokens"] == 1400


def test_groq_daily_quota_in_the_fallback_fails_fast(bedrock, monkeypatch):
    monkeypatch.setattr(mc.time, "sleep", lambda s: pytest.fail("slept on a Groq daily quota"))
    bedrock.client.converse.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Too many requests"}}, "Converse")
    tpd = MagicMock(status_code=429, headers={"retry-after": "435"},
                    text='{"error": {"message": "Rate limit reached on tokens per day (TPD): Limit 200000"}}')
    bedrock.groq_post.return_value = tpd

    with pytest.raises(ModelUnavailableError) as exc:
        model_client.reason(system="s", user="u", task="security")

    assert bedrock.groq_post.call_count == 2  # both Groq tiers tried once each
    groq_error = exc.value.__cause__
    assert isinstance(groq_error, ModelUnavailableError)
    assert isinstance(groq_error.__cause__, mc.GroqDailyQuotaError)
