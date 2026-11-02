import re
import time
from contextlib import contextmanager
from contextvars import ContextVar

import requests
from config import settings
from tools import bedrock_llm
from tools.llm_routing import REASONING_TOKEN_ALLOWANCE, TASK_REASONING_EFFORT, TASK_ROUTING  # noqa: F401
from tools.tracing import llm_inputs, record_llm_usage, traceable

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

# Task -> tier (reasoning | light) and per-task reasoning effort, shared with
# the Bedrock provider (tools/llm_routing.py). On Groq every task has a
# primary model by tier, and falls back to the OTHER Groq model
# (reasoning <-> light) if the primary is rate-limited or erroring.
# USD per 1M tokens (input, output), matched on a fragment of the Bedrock
# model id (dotted, so Groq's "openai/gpt-oss-120b" never matches). Reasoning
# tokens are billed as output. Groq models are on the free tier, so they
# cost 0, as does any model not listed here.
# - gpt-oss: aws.amazon.com/bedrock/pricing, Standard tier, checked 2 Nov
#   2026. AWS lists no Mumbai (ap-south-1) row for gpt-oss; these are the
#   Asia Pacific (Sydney) rows, the nearest published APAC price.
# - Nova 2 Lite: Amazon Bedrock on-demand price, about $0.30 / $2.50,
#   checked 2 Nov 2026.
LLM_PRICES_PER_MTOK = {
    "openai.gpt-oss-120b": (0.1545, 0.6180),
    "openai.gpt-oss-20b": (0.0721, 0.3090),
    "amazon.nova-2-lite": (0.30, 2.50),
}


def llm_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    for fragment, (per_in, per_out) in LLM_PRICES_PER_MTOK.items():
        if fragment in model:
            return (prompt_tokens * per_in + completion_tokens * per_out) / 1_000_000
    return 0.0


PROVIDERS = ("groq", "bedrock")


def provider_chain() -> list[str]:
    """[LLM_PROVIDER, LLM_FALLBACK], deduplicated; "none" = no fallback."""
    chain = []
    for name in (getattr(settings, "LLM_PROVIDER", "") or "groq", getattr(settings, "LLM_FALLBACK", "") or "groq"):
        name = name.strip().lower()
        if name == "none" or name in chain:
            continue
        if name not in PROVIDERS:
            raise ValueError(f"unknown LLM provider {name!r}; use one of {PROVIDERS} (or none as fallback)")
        chain.append(name)
    return chain


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


class GroqDailyQuotaError(ModelUnavailableError):
    """A Groq 429 from the daily quota (tokens or requests per day). Its
    retry-after can be minutes (435 s seen), so it isn't waited out: the
    caller moves on at once to the other Groq tier, whose quota is separate."""


# Groq 429 bodies name the limit hit, e.g. "on tokens per day (TPD): Limit
# 200000 ..."; per-minute limits say "tokens per minute (TPM)" / "(RPM)".
_DAILY_LIMITS = {
    "TPD": re.compile(r"tokens per day|\(TPD\)", re.IGNORECASE),
    "RPD": re.compile(r"requests per day|\(RPD\)", re.IGNORECASE),
}


def _daily_limit(resp) -> str | None:
    """"TPD" / "RPD" when a 429 comes from Groq's daily quota, else None
    (a per-minute limit, which is worth waiting for). The body says which
    limit was hit; failing that, x-ratelimit-remaining-requests is the
    requests-per-day counter, so 0 there means the daily request quota."""
    for name, pattern in _DAILY_LIMITS.items():
        if pattern.search(resp.text or ""):
            return name
    headers = {k.lower(): v for k, v in resp.headers.items()}
    if headers.get("x-ratelimit-remaining-requests", "").strip() == "0":
        return "RPD"
    return None


class LLMCallCounter:
    def __init__(self):
        self.calls = 0
        # Summed from the usage block of every successful (2xx) response,
        # including ones then rejected as incomplete: those tokens were spent.
        self.prompt_tokens = 0
        self.completion_tokens = 0  # includes reasoning_tokens
        self.reasoning_tokens = 0
        # Times reason() moved a task on: to the other Groq tier, or to the
        # fallback provider.
        self.fallbacks = 0
        # Time spent sleeping on 429 retry-after waits.
        self.rate_limit_wait_ms = 0
        # Time spent in client-side pacing before Bedrock calls.
        self.pacing_wait_ms = 0
        # From LLM_PRICES_PER_MTOK; 0 for free-tier Groq.
        self.cost_usd = 0.0


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
        Sends a (system, user) prompt pair to each provider in
        provider_chain() until one answers: LLM_PROVIDER, then LLM_FALLBACK.
        Returns raw text content — callers (agents) are responsible for
        parsing/validating JSON out of it, and retry on a parse failure.
        max_tokens is the budget for the answer itself; reasoning models get
        their reasoning allowance on top. Raises ModelUnavailableError only
        when every provider has failed.
        """
        chain = provider_chain()
        last_error = None
        for i, provider in enumerate(chain):
            if i > 0:
                counter = _call_counter.get()
                if counter is not None:
                    counter.fallbacks += 1
                print(f"    [model_client] provider {chain[i - 1]} failed for task={task}: "
                      f"{type(last_error).__name__}: {last_error} — trying {provider}")
            try:
                if provider == "bedrock":
                    return self._call_bedrock(system, user, bedrock_llm.model_for(task), max_tokens, task)
                return self._reason_groq(system, user, task, max_tokens)
            except Exception as e:
                last_error = e
        if len(chain) == 1 and isinstance(last_error, ModelUnavailableError):
            raise last_error  # Groq alone: its own error, as before
        raise ModelUnavailableError(f"All LLM providers failed for task={task} ({', '.join(chain)})") from last_error

    def _reason_groq(self, system: str, user: str, task: str, max_tokens: int) -> str:
        """A Groq model by task type, with one fallback hop to the other Groq
        model (reasoning <-> light) if the primary fails."""
        primary = TASK_ROUTING.get(task, "reasoning")
        secondary = "light" if primary == "reasoning" else "reasoning"

        model_for = {
            "reasoning": settings.GROQ_MODEL_REASONING,
            "light": settings.GROQ_MODEL_LIGHT,
        }

        last_error = None
        for tier in (primary, secondary):
            if tier == secondary:
                counter = _call_counter.get()
                if counter is not None:
                    counter.fallbacks += 1
            try:
                return self._call_groq(system, user, model_for[tier], max_tokens, task)
            except Exception as e:
                last_error = e
                print(f"    [model_client] {tier} ({model_for[tier]}) failed for task={task}: "
                      f"{type(e).__name__}: {e} — trying next")
                continue

        raise ModelUnavailableError(f"All Groq tiers exhausted for task={task}") from last_error

    # One LLM run per Groq call (retries on 429 included) in the LangSmith trace.
    @traceable(run_type="llm", name="groq", process_inputs=llm_inputs)
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
                daily = _daily_limit(resp)
                if daily:
                    print(f"    [groq] daily quota ({daily}) exhausted on {model}; not waiting "
                          f"{resp.headers.get('retry-after', '?')}s, failing fast")
                    raise GroqDailyQuotaError(f"Groq daily quota ({daily}) exhausted on {model}")
            if resp.status_code == 429 and attempt < max_retries:
                wait = int(resp.headers.get("retry-after", 5 * (attempt + 1)))
                print(f"    [groq] rate-limited on {model}, waiting {wait}s")
                if counter is not None:
                    counter.rate_limit_wait_ms += wait * 1000
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
                counter.cost_usd += llm_cost_usd(model, usage.get("prompt_tokens", 0), completion_tokens)
            record_llm_usage("groq", model, usage, finish_reason)
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

    # One LLM run per Bedrock call in the LangSmith trace (botocore's own
    # retries happen inside it).
    @traceable(run_type="llm", name="bedrock", process_inputs=llm_inputs)
    def _call_bedrock(self, system: str, user: str, model: str, max_tokens: int, task: str) -> str:
        """One Converse call, paced by this model's own limiter. Throttling
        and transient errors are retried by botocore (adaptive); whatever
        still fails raises, so reason() can fall back to the next provider."""
        effort = bedrock_llm.effort_for(model, task)
        counter = _call_counter.get()
        slept = bedrock_llm.get_limiter(model).wait()
        if counter is not None:
            counter.pacing_wait_ms += int(slept * 1000)
            counter.calls += 1
        started = time.monotonic()
        response = bedrock_llm.get_client().converse(
            modelId=model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            # temperature is accepted together with reasoning by Nova 2 Lite
            # and gpt-oss (checked live, 2 Nov).
            inferenceConfig={"maxTokens": max_tokens + REASONING_TOKEN_ALLOWANCE[effort], "temperature": 0.2},
            additionalModelRequestFields=bedrock_llm.reasoning_fields(model, effort),
        )
        stop_reason = response.get("stopReason")
        usage = response.get("usage") or {}
        prompt_tokens, completion_tokens = usage.get("inputTokens", 0), usage.get("outputTokens", 0)
        if counter is not None:
            counter.prompt_tokens += prompt_tokens
            counter.completion_tokens += completion_tokens  # reasoning included; not reported separately
            counter.cost_usd += llm_cost_usd(model, prompt_tokens, completion_tokens)
        record_llm_usage("amazon_bedrock", model, usage, stop_reason)
        print(f"    [bedrock] task={task} model={model} effort={effort} stop={stop_reason} "
              f"tokens: prompt={prompt_tokens} output={completion_tokens} "
              f"({time.monotonic() - started:.1f}s" + (f", paced {slept:.1f}s" if slept >= 0.05 else "") + ")")

        text = bedrock_llm.answer_text(response)
        if stop_reason == "max_tokens":
            raise IncompleteResponseError(
                f"{model} hit the token limit (stopReason=max_tokens, {completion_tokens} output tokens)")
        if not text.strip():
            raise IncompleteResponseError(f"{model} returned no text (stopReason={stop_reason})")
        return text


model_client = FreeModelClient()