import re
import time
from contextlib import contextmanager
from contextvars import ContextVar

import requests
from config import settings

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

# Task -> primary Groq model. gap #7's fallback chain is now single-provider:
# every task has a primary Groq model, and falls back to the OTHER Groq model
# (reasoning <-> light) if the primary is rate-limited or erroring.
# HF is no longer part of the reasoning path — Qwen2.5-Coder isn't hosted on
# the free `hf-inference` serverless tier (confirmed via standalone test,
# same failure mode as the nomic-embed-code issue in lesson #1). HF Inference
# API is still used elsewhere in the project for embeddings (rag/indexer.py),
# just not here.
TASK_ROUTING = {
    "security": "reasoning",
    "architecture": "reasoning",
    "synthesis": "reasoning",
    "test_coverage": "light",
    "documentation": "light",
}


# gpt-oss models think before answering, and those reasoning tokens come out
# of the same completion budget as the answer: with too small a budget the
# model stops mid-thought (finish_reason "length") with empty content. So
# each task gets a reasoning effort, and the request budget is the caller's
# answer budget PLUS a reasoning allowance for that effort.
TASK_REASONING_EFFORT = {
    "security": "medium",
    "architecture": "medium",
    "synthesis": "medium",
    "test_coverage": "low",
    "documentation": "low",
}
REASONING_TOKEN_ALLOWANCE = {"low": 2048, "medium": 6144, "high": 12288}


def _is_gpt_oss(model: str) -> bool:
    # Only gpt-oss accepts reasoning_effort low/medium/high and include_reasoning;
    # other Groq models reject them, so they're sent to gpt-oss only.
    return "gpt-oss" in model


def _rate_limit_details(resp) -> str:
    """The x-ratelimit-* / retry-after headers and the (truncated) body of a
    429, so a daily-quota stop is distinguishable from a per-minute wait.
    Neither carries the API key; organization ids in the body are masked."""
    headers = {k.lower(): v for k, v in resp.headers.items()
               if k.lower().startswith("x-ratelimit-") or k.lower() == "retry-after"}
    body = re.sub(r"org_\w+", "org_***", resp.text[:500])
    return f"headers={headers} body={body!r}"


class ModelUnavailableError(Exception):
    pass


class LLMCallCounter:
    def __init__(self):
        self.calls = 0
        # Summed from the usage block of every successful (2xx) response,
        # including ones then rejected as incomplete: those tokens were spent.
        self.prompt_tokens = 0
        self.completion_tokens = 0  # includes reasoning_tokens
        self.reasoning_tokens = 0


_call_counter: ContextVar[LLMCallCounter | None] = ContextVar("llm_call_counter", default=None)


@contextmanager
def count_llm_calls():
    """Counts every Groq HTTP request (retries and fallbacks included), and
    the tokens of every successful response, made in this context — including from agent threads started with
    asyncio.to_thread, which inherit it. Concurrent reviews each get their
    own count."""
    counter = LLMCallCounter()
    token = _call_counter.set(counter)
    try:
        yield counter
    finally:
        _call_counter.reset(token)


class IncompleteResponseError(Exception):
    """The model returned no usable answer: cut off by the token limit
    (finish_reason "length") or empty content. Treated like any other
    failure, so reason() falls back to the other tier."""


class FreeModelClient:
    def reason(self, system: str, user: str, task: str, max_tokens: int = 2000) -> str:
        """
        Routes a (system, user) prompt pair to a Groq model based on task type,
        with one automatic fallback hop to the other Groq model if the primary
        fails. Returns raw text content — callers (agents) are responsible for
        parsing/validating JSON out of it, since Groq's JSON mode is less
        strict than Anthropic's and needs parse-with-retry at the call site.
        max_tokens is the budget for the answer itself; reasoning models get
        their reasoning allowance on top (see TASK_REASONING_EFFORT).
        """
        primary = TASK_ROUTING.get(task, "reasoning")
        secondary = "light" if primary == "reasoning" else "reasoning"

        model_for = {
            "reasoning": settings.GROQ_MODEL_REASONING,
            "light": settings.GROQ_MODEL_LIGHT,
        }

        last_error = None
        for tier in (primary, secondary):
            try:
                return self._call_groq(system, user, model_for[tier], max_tokens, task)
            except Exception as e:
                last_error = e
                print(f"    [model_client] {tier} ({model_for[tier]}) failed for task={task}: "
                      f"{type(e).__name__}: {e} — trying next")
                continue

        raise ModelUnavailableError(f"All Groq tiers exhausted for task={task}") from last_error

    def _call_groq(self, system: str, user: str, model: str, max_tokens: int, task: str,
                   max_retries: int = 2) -> str:
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        if _is_gpt_oss(model):
            effort = TASK_REASONING_EFFORT.get(task, "medium")
            body["reasoning_effort"] = effort
            # The reasoning comes back in message.reasoning, never in content;
            # leaving it out entirely keeps it away from anything we parse.
            body["include_reasoning"] = False
            body["max_completion_tokens"] = max_tokens + REASONING_TOKEN_ALLOWANCE[effort]
        else:
            effort = None
            body["max_tokens"] = max_tokens

        for attempt in range(max_retries + 1):
            started = time.monotonic()
            counter = _call_counter.get()
            if counter is not None:
                counter.calls += 1
            resp = requests.post(
                GROQ_CHAT_URL,
                headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}", "Content-Type": "application/json"},
                json=body,
                timeout=90,
            )
            if resp.status_code == 429:
                print(f"    [groq] 429 from {model}: {_rate_limit_details(resp)}")
            if resp.status_code == 429 and attempt < max_retries:
                wait = int(resp.headers.get("retry-after", 5 * (attempt + 1)))
                print(f"    [groq] rate-limited on {model}, waiting {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]
            finish_reason = choice.get("finish_reason")
            usage = data.get("usage") or {}
            reasoning_tokens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            if counter is not None:
                counter.prompt_tokens += usage.get("prompt_tokens", 0)
                counter.completion_tokens += completion_tokens
                counter.reasoning_tokens += reasoning_tokens
            print(f"    [groq] task={task} model={model} effort={effort or '-'} finish={finish_reason} "
                  f"tokens: prompt={usage.get('prompt_tokens', '?')} reasoning={reasoning_tokens} "
                  f"answer={completion_tokens - reasoning_tokens} ({time.monotonic() - started:.1f}s)")

            content = choice["message"].get("content")
            if finish_reason == "length":
                raise IncompleteResponseError(
                    f"{model} hit the token limit (finish_reason=length, {completion_tokens} completion tokens)")
            if not content or not content.strip():
                raise IncompleteResponseError(f"{model} returned empty content (finish_reason={finish_reason})")
            return content

        raise ModelUnavailableError(f"Groq rate-limited after {max_retries} retries on {model}")


model_client = FreeModelClient()
