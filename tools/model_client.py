import time
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


class ModelUnavailableError(Exception):
    pass


class FreeModelClient:
    def reason(self, system: str, user: str, task: str, max_tokens: int = 2000) -> str:
        """
        Routes a (system, user) prompt pair to a Groq model based on task type,
        with one automatic fallback hop to the other Groq model if the primary
        fails. Returns raw text content — callers (agents) are responsible for
        parsing/validating JSON out of it, since Groq's JSON mode is less
        strict than Anthropic's and needs parse-with-retry at the call site.
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
                return self._call_groq(system, user, model_for[tier], max_tokens)
            except Exception as e:
                last_error = e
                print(f"    [model_client] {tier} ({model_for[tier]}) failed for task={task}: "
                      f"{type(e).__name__}: {e} — trying next")
                continue

        raise ModelUnavailableError(f"All Groq tiers exhausted for task={task}") from last_error

    def _call_groq(self, system: str, user: str, model: str, max_tokens: int, max_retries: int = 2) -> str:
        for attempt in range(max_retries + 1):
            resp = requests.post(
                GROQ_CHAT_URL,
                headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.2,
                },
                timeout=60,
            )
            if resp.status_code == 429 and attempt < max_retries:
                wait = int(resp.headers.get("retry-after", 5 * (attempt + 1)))
                print(f"    [groq] rate-limited on {model}, waiting {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

        raise ModelUnavailableError(f"Groq rate-limited after {max_retries} retries on {model}")


model_client = FreeModelClient()