from unittest.mock import MagicMock

import pytest

import tools.model_client as mc
from models.findings import ReviewReport
from pipeline.synthesizer import synthesize_markdown
from tools.model_client import (
    REASONING_TOKEN_ALLOWANCE, IncompleteResponseError, ModelUnavailableError, model_client,
)


def _response(content="[]", finish_reason="stop", reasoning_tokens=40, status=200):
    resp = MagicMock(status_code=status, headers={})
    resp.json.return_value = {
        "choices": [{"finish_reason": finish_reason, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": reasoning_tokens + 10,
                  "completion_tokens_details": {"reasoning_tokens": reasoning_tokens}},
    }
    return resp


@pytest.fixture
def groq(monkeypatch, settings):
    monkeypatch.setattr(settings, "GROQ_MODEL_REASONING", "openai/gpt-oss-120b")
    monkeypatch.setattr(settings, "GROQ_MODEL_LIGHT", "openai/gpt-oss-20b")
    post = MagicMock(return_value=_response())
    monkeypatch.setattr(mc.requests, "post", post)
    return post


@pytest.mark.parametrize("task, model, effort", [
    ("security", "openai/gpt-oss-120b", "medium"),
    ("architecture", "openai/gpt-oss-120b", "medium"),
    ("synthesis", "openai/gpt-oss-120b", "medium"),
    ("test_coverage", "openai/gpt-oss-20b", "low"),
    ("documentation", "openai/gpt-oss-20b", "low"),
])
def test_gpt_oss_gets_effort_hidden_reasoning_and_headroom(groq, task, model, effort):
    model_client.reason(system="s", user="u", task=task, max_tokens=2000)

    body = groq.call_args.kwargs["json"]
    assert body["model"] == model
    assert body["reasoning_effort"] == effort
    assert body["include_reasoning"] is False
    assert body["max_completion_tokens"] == 2000 + REASONING_TOKEN_ALLOWANCE[effort]
    assert "max_tokens" not in body


def test_other_models_get_plain_max_tokens(groq, monkeypatch, settings):
    monkeypatch.setattr(settings, "GROQ_MODEL_REASONING", "qwen/qwen3.8-27b")
    model_client.reason(system="s", user="u", task="security", max_tokens=1500)

    body = groq.call_args.kwargs["json"]
    assert body["max_tokens"] == 1500
    assert not {"reasoning_effort", "include_reasoning", "max_completion_tokens"} & set(body)


def test_answer_comes_from_content(groq):
    groq.return_value = _response(content='[{"title": "x"}]')
    assert model_client.reason(system="s", user="u", task="security") == '[{"title": "x"}]'


def test_truncated_answer_falls_back_to_the_other_tier(groq, capsys):
    groq.side_effect = [_response(content="", finish_reason="length"), _response(content="[]")]

    assert model_client.reason(system="s", user="u", task="security") == "[]"

    first, second = (c.kwargs["json"]["model"] for c in groq.call_args_list)
    assert (first, second) == ("openai/gpt-oss-120b", "openai/gpt-oss-20b")
    assert "finish=length" in capsys.readouterr().out


def test_truncated_with_partial_content_is_still_a_failure(groq):
    groq.side_effect = [_response(content='[{"title": "cut', finish_reason="length")] * 2
    with pytest.raises(ModelUnavailableError) as exc:
        model_client.reason(system="s", user="u", task="security")
    assert isinstance(exc.value.__cause__, IncompleteResponseError)


def test_empty_content_is_a_failure(groq):
    groq.side_effect = [_response(content=None), _response(content="   ")]
    with pytest.raises(ModelUnavailableError):
        model_client.reason(system="s", user="u", task="documentation")


def test_usage_is_logged(groq, capsys):
    model_client.reason(system="s", user="u", task="test_coverage")
    out = capsys.readouterr().out
    assert "task=test_coverage model=openai/gpt-oss-20b effort=low finish=stop" in out
    assert "reasoning=40 answer=10" in out


def test_synthesis_uses_model_client_and_falls_back_to_template(monkeypatch, make_finding):
    review = ReviewReport(pr_number=1, repo="o/r", commit_sha="abc",
                          all_findings=[make_finding(title="Hardcoded key", severity="CRITICAL")])
    review.recompute_counts()

    reason = MagicMock(return_value="## Review\nAll good")
    monkeypatch.setattr(mc.model_client, "reason", reason)
    assert synthesize_markdown(review) == "## Review\nAll good"
    assert reason.call_args.kwargs["task"] == "synthesis"

    reason.side_effect = ModelUnavailableError("all tiers failed")
    fallback = synthesize_markdown(review)
    assert "critical issue(s) found" in fallback and "Hardcoded key" in fallback


def test_counter_sums_tokens_from_every_successful_response(groq):
    # One truncated answer (its tokens were still spent) then the fallback's answer.
    groq.side_effect = [_response(content="", finish_reason="length", reasoning_tokens=30),
                        _response(content="[]", reasoning_tokens=40)]

    with mc.count_llm_calls() as counter:
        model_client.reason(system="s", user="u", task="security")

    assert counter.calls == 2
    assert counter.prompt_tokens == 200
    assert counter.completion_tokens == (30 + 10) + (40 + 10)
    assert counter.reasoning_tokens == 70


def test_counter_skips_rate_limited_responses_and_missing_usage(groq, monkeypatch):
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    limited = MagicMock(status_code=429, headers={"retry-after": "0"})
    no_usage = _response(content="[]")
    no_usage.json.return_value.pop("usage")
    groq.side_effect = [limited, no_usage]

    with mc.count_llm_calls() as counter:
        model_client.reason(system="s", user="u", task="security")

    assert counter.calls == 2
    assert (counter.prompt_tokens, counter.completion_tokens, counter.reasoning_tokens) == (0, 0, 0)
