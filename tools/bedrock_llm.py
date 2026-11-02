"""Amazon Bedrock (Converse API) as an LLM provider for tools/model_client.

Settings (config.py): BEDROCK_LLM_MODEL (default the global Nova 2 Lite
profile, the only route to it from ap-south-1), BEDROCK_LLM_REGION (default
AWS_REGION), BEDROCK_LLM_MAX_RPM (default 18) and BEDROCK_REASONING_EFFORTS.

Reasoning is always on. Measured on 2 Nov with a small security prompt:
with reasoning off, Nova 2 Lite ignored "reply ONLY with JSON" and added
prose; "low" gave clean JSON and rated a SQL injection HIGH; "medium" gave
clean JSON and rated it CRITICAL, at about 10x the output tokens and 4x the
latency. Hence medium for the two tasks whose severity gates a merge, low
for the rest.

Reasoning tokens are billed as output and come back inside outputTokens;
the usage block has no separate reasoning count.
"""
import threading

from config import settings
from tools.rate_limit import RateLimiter

EFFORTS = ("low", "medium", "high")
DEFAULT_TASK_EFFORT = {
    "security": "medium",
    "architecture": "medium",
    "synthesis": "low",
    "test_coverage": "low",
    "documentation": "low",
}
# Added to the caller's answer budget for maxTokens, which covers reasoning
# plus answer (the same allowances as for gpt-oss on Groq).
REASONING_TOKEN_ALLOWANCE = {"low": 2048, "medium": 6144, "high": 12288}


def _setting(name: str, default: str = "") -> str:
    return getattr(settings, name, "") or default


def task_efforts() -> dict[str, str]:
    """The built-in task -> effort map with BEDROCK_REASONING_EFFORTS
    ("security:medium,architecture:low") applied on top. Raises ValueError
    on an unknown effort or a malformed entry."""
    efforts = dict(DEFAULT_TASK_EFFORT)
    for entry in _setting("BEDROCK_REASONING_EFFORTS").split(","):
        if not entry.strip():
            continue
        task, sep, effort = (x.strip() for x in entry.partition(":"))
        if not sep or not task or effort not in EFFORTS:
            raise ValueError(f"BEDROCK_REASONING_EFFORTS: bad entry {entry.strip()!r} "
                             f"(want task:effort, effort one of {EFFORTS})")
        efforts[task] = effort
    return efforts


def effort_for(task: str) -> str:
    return task_efforts().get(task, "medium")


def model_id() -> str:
    return _setting("BEDROCK_LLM_MODEL", "global.amazon.nova-2-lite-v1:0")


_client = None
_limiter = None
_lock = threading.Lock()


def get_client():
    """One bedrock-runtime client per process (thread-safe). Adaptive retries
    slow the client down once Bedrock throttles; read_timeout allows for a
    medium-effort answer, which took ~12 s for a small prompt."""
    global _client
    with _lock:
        if _client is None:
            import boto3
            from botocore.config import Config
            _client = boto3.client(
                "bedrock-runtime",
                region_name=_setting("BEDROCK_LLM_REGION") or _setting("AWS_REGION", "ap-south-1"),
                config=Config(retries={"mode": "adaptive", "max_attempts": 10},
                              connect_timeout=5, read_timeout=120),
            )
        return _client


def get_limiter() -> RateLimiter:
    """The LLM's own pacer, separate from the Titan embedder's: the quotas
    are separate. The account allows 20 Nova 2 Lite requests per minute."""
    global _limiter
    with _lock:
        if _limiter is None:
            _limiter = RateLimiter(float(_setting("BEDROCK_LLM_MAX_RPM", "18")))
        return _limiter


def answer_text(response: dict) -> str:
    """The answer: the message's text blocks joined, without the
    reasoningContent blocks that come before them."""
    blocks = ((response.get("output") or {}).get("message") or {}).get("content") or []
    return "".join(b["text"] for b in blocks if isinstance(b.get("text"), str))
