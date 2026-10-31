"""LangSmith tracing: one trace per review, with each agent and LLM call nested.

Off unless the environment says otherwise. LangSmith reads its own settings
from environment variables (on ECS: the task definition, with the key from
Secrets Manager):
  LANGSMITH_TRACING=true      turns tracing on
  LANGSMITH_API_KEY           the key (a secret)
  LANGSMITH_PROJECT           project name in LangSmith, e.g. code-review-agent
Without LANGSMITH_TRACING=true the decorators below do nothing and nothing is
sent anywhere, which is the case in tests and in local dev by default.

Traces contain the prompts, which include PR diffs and code. That's fine for
public repos; for private ones, think before turning this on.
"""
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree

__all__ = ["traceable", "record_llm_usage", "llm_inputs"]


def llm_inputs(inputs: dict) -> dict:
    """Shows an LLM call's inputs as chat messages (what LangSmith renders
    best), plus the model and task, instead of raw keyword arguments."""
    return {
        "messages": [
            {"role": "system", "content": inputs.get("system", "")},
            {"role": "user", "content": inputs.get("user", "")},
        ],
        "model": inputs.get("model"),
        "task": inputs.get("task"),
        "max_tokens": inputs.get("max_tokens"),
    }


def record_llm_usage(provider: str, model: str, usage: dict, finish_reason: str | None) -> None:
    """Attaches token counts and the model to the current LLM run, if there
    is one (no-op when tracing is off). LangSmith uses usage_metadata for its
    token and cost columns, and ls_provider/ls_model_name to price the call."""
    run = get_current_run_tree()
    if run is None:
        return
    prompt = usage.get("prompt_tokens", 0) or 0
    completion = usage.get("completion_tokens", 0) or 0
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
    run.set(
        usage_metadata={
            "input_tokens": prompt,
            "output_tokens": completion,
            "total_tokens": prompt + completion,
            "output_token_details": {"reasoning": reasoning},
        },
        metadata={"ls_provider": provider, "ls_model_name": model, "finish_reason": finish_reason},
    )