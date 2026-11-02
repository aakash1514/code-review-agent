"""Task routing and reasoning budgets shared by every LLM provider
(tools/model_client.py for Groq, tools/bedrock_llm.py for Bedrock).

Each task goes to a "reasoning" or a "light" model; each provider maps those
tiers to its own model IDs. gpt-oss gets the same per-task reasoning effort
on Groq and on Bedrock, so results compare across hosts.
"""

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
