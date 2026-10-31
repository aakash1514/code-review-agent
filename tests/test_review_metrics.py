"""CloudWatch metrics for a finished review (api/webhook._review_metrics)."""
from api.webhook import _review_metrics
from tools.model_client import LLMCallCounter


def _counter(**kw) -> LLMCallCounter:
    c = LLMCallCounter()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _values(metrics: dict) -> dict:
    return {name: value for name, (value, _) in metrics.items()}


def test_completed_review_with_a_blocking_finding():
    m = _review_metrics({"status": "complete"}, {"critical_findings": 2, "merge_blocked": True}, 12_000,
                        _counter(calls=5, prompt_tokens=4000, completion_tokens=2500, fallbacks=1,
                                 rate_limit_wait_ms=7000))
    assert _values(m) == {
        "ReviewsCompleted": 1, "ReviewsFailed": 0, "ReviewDurationMs": 12_000, "LLMCalls": 5,
        "LLMTokens": 6500, "LLMFallbacks": 1, "RateLimitWaitMs": 7000,
        "CriticalFindings": 2, "MergeBlocked": 1,
    }
    assert m["ReviewDurationMs"][1] == "Milliseconds" and m["LLMTokens"][1] == "Count"


def test_failed_review_has_no_gate_metrics():
    m = _review_metrics({"status": "failed", "error": "HTTPError: 502"}, None, 900, _counter())
    v = _values(m)
    assert v["ReviewsFailed"] == 1 and v["ReviewsCompleted"] == 0
    assert "CriticalFindings" not in v and "MergeBlocked" not in v


def test_interrupted_review_counts_as_failed():
    v = _values(_review_metrics({"status": "interrupted"}, None, 100, _counter()))
    assert v["ReviewsFailed"] == 1


def test_superseded_review_is_neither_completed_nor_failed():
    job = {"status": "failed", "error": "discarded: superseded by newer commit"}
    v = _values(_review_metrics(job, None, 100, _counter()))
    assert v["ReviewsFailed"] == 0 and v["ReviewsCompleted"] == 0


def test_missing_job_row_is_not_counted_as_anything():
    v = _values(_review_metrics({}, None, 100, _counter()))
    assert v["ReviewsCompleted"] == 0 and v["ReviewsFailed"] == 0