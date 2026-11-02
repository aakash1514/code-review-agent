"""Amazon Bedrock (Converse API) as an LLM provider for tools/model_client.

Models, by task tier (tools/llm_routing.TASK_ROUTING), as on Groq:
  reasoning (security, architecture, synthesis)  BEDROCK_LLM_MODEL_REASONING,
                                                 default openai.gpt-oss-120b-1:0
  light (test_coverage, documentation)           BEDROCK_LLM_MODEL_LIGHT,
                                                 default openai.gpt-oss-20b-1:0;
                                                 "none" = use the reasoning model
BEDROCK_LLM_MODEL, when set, overrides both (one model for every task, e.g.
global.amazon.nova-2-lite-v1:0). Region: BEDROCK_LLM_REGION, else AWS_REGION.

The code doesn't assume a model family. Reasoning is requested per family:
  gpt-oss  {"reasoning_effort": effort}, with the Groq effort map
           (TASK_REASONING_EFFORT), so results compare with Groq directly;
  Nova     {"reasoningConfig": {...}}, all tasks "low": at "medium" Nova 2
           Lite reasoned until maxTokens on every architecture call and on 7%
           of security calls (eval v3), and with reasoning off it ignored
           "reply ONLY with JSON";
  other    no reasoning field.
BEDROCK_REASONING_EFFORTS ("security:medium,architecture:low") overrides
the map for whichever family is in use.

Reasoning tokens are billed as output and come back inside outputTokens;
the usage block has no separate reasoning count.
"""
import threading

from config import settings
from tools.llm_routing import REASONING_TOKEN_ALLOWANCE, TASK_REASONING_EFFORT, TASK_ROUTING
from tools.rate_limit import RateLimiter

EFFORTS = ("low", "medium", "high")
GPT_OSS_120B = "openai.gpt-oss-120b-1:0"
GPT_OSS_20B = "openai.gpt-oss-20b-1:0"
NOVA_2_LITE = "global.amazon.nova-2-lite-v1:0"

NOVA_TASK_EFFORT = {task: "low" for task in TASK_ROUTING}

# Client-side pacing per model, at about 90% of this account's on-demand
# requests-per-minute quota in ap-south-1 (Service Quotas, 2 Nov 2026):
# gpt-oss-120b and gpt-oss-20b 100/min each (not adjustable), Nova 2 Lite
# 20/min (global profile). Unknown models get the most cautious rate.
# BEDROCK_LLM_MAX_RPM overrides: a number for every model, or
# "model-id=rpm,model-id=rpm".
DEFAULT_MAX_RPM = {GPT_OSS_120B: 90, GPT_OSS_20B: 90, NOVA_2_LITE: 18}
FALLBACK_MAX_RPM = 18


def _setting(name: str, default: str = "") -> str:
    return getattr(settings, name, "") or default


def family(model: str) -> str:
    if "gpt-oss" in model:
        return "gpt-oss"
    if "nova" in model:
        return "nova"
    return "other"


def model_for(task: str) -> str:
    """The Bedrock model ID for a task: BEDROCK_LLM_MODEL for everything when
    set, else the task's tier model."""
    override = _setting("BEDROCK_LLM_MODEL")
    if override:
        return override
    reasoning = _setting("BEDROCK_LLM_MODEL_REASONING", GPT_OSS_120B)
    if TASK_ROUTING.get(task, "reasoning") == "reasoning":
        return reasoning
    light = _setting("BEDROCK_LLM_MODEL_LIGHT", GPT_OSS_20B)
    return reasoning if light.lower() == "none" else light


def task_efforts(model: str) -> dict[str, str]:
    """The task -> effort map for this model's family, with
    BEDROCK_REASONING_EFFORTS applied on top. Raises ValueError on an
    unknown effort or a malformed entry."""
    efforts = dict(NOVA_TASK_EFFORT if family(model) == "nova" else TASK_REASONING_EFFORT)
    for entry in _setting("BEDROCK_REASONING_EFFORTS").split(","):
        if not entry.strip():
            continue
        task, sep, effort = (x.strip() for x in entry.partition(":"))
        if not sep or not task or effort not in EFFORTS:
            raise ValueError(f"BEDROCK_REASONING_EFFORTS: bad entry {entry.strip()!r} "
                             f"(want task:effort, effort one of {EFFORTS})")
        efforts[task] = effort
    return efforts


def effort_for(model: str, task: str) -> str:
    return task_efforts(model).get(task, "low")


def reasoning_fields(model: str, effort: str) -> dict:
    """additionalModelRequestFields asking this model family for reasoning."""
    if family(model) == "gpt-oss":
        return {"reasoning_effort": effort}
    if family(model) == "nova":
        return {"reasoningConfig": {"type": "enabled", "maxReasoningEffort": effort}}
    return {}


def max_rpm(model: str) -> float:
    raw = _setting("BEDROCK_LLM_MAX_RPM").strip()
    if raw and "=" not in raw:
        return float(raw)
    overrides = {}
    for entry in raw.split(","):
        if entry.strip():
            name, _, rpm = entry.rpartition("=")
            overrides[name.strip()] = float(rpm)
    return overrides.get(model, DEFAULT_MAX_RPM.get(model, FALLBACK_MAX_RPM))


_client = None
_limiters: dict[str, RateLimiter] = {}
_lock = threading.Lock()


def get_client():
    """One bedrock-runtime client per process (thread-safe). Adaptive retries
    slow the client down once Bedrock throttles; read_timeout allows for a
    long reasoning answer."""
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


def get_limiter(model: str) -> RateLimiter:
    """This model's own pacer: Bedrock quotas are per model, and separate
    from the Titan embedder's (which has its own limiter)."""
    with _lock:
        if model not in _limiters:
            _limiters[model] = RateLimiter(max_rpm(model))
        return _limiters[model]


def answer_text(response: dict) -> str:
    """The answer: the message's text blocks joined, without the
    reasoningContent blocks that come before them."""
    blocks = ((response.get("output") or {}).get("message") or {}).get("content") or []
    return "".join(b["text"] for b in blocks if isinstance(b.get("text"), str))
