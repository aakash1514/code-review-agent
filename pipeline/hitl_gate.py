from models.findings import ReviewReport

# GitHub commit-status states, per tools/github_tools.set_commit_status
STATUS_SUCCESS = "success"
STATUS_FAILURE = "failure"

DEFAULT_STATUS_CONTEXT = "ai-code-review"


def evaluate_hitl_gate(review: ReviewReport, min_critical_confidence: float = 0.0) -> dict:
    """
    Decides whether a PR should be blocked, based on the already-computed
    ReviewReport.hitl_required flag (set by recompute_counts() in
    synthesizer.build_review_report — critical_count > 0).

    min_critical_confidence is an optional extra safety valve: if set above
    0.0, a CRITICAL finding only counts toward blocking if its confidence
    meets this bar. Defaults to 0.0 (any CRITICAL blocks, matching
    ReviewReport.hitl_required exactly) so this function's default behavior
    is a pure pass-through of the already-agreed dedup/rank logic — not a
    second, different opinion. Only raise this above 0.0 if a specific
    severity-calibration issue (e.g. non-deterministic over-escalation to
    CRITICAL — see architecture_agent's known TODO) is causing false-positive
    blocks in practice and the prompt-level fix hasn't fully resolved it.

    Returns a dict (not a bare bool) so the caller has everything needed to
    call tools.github_tools.set_commit_status without recomputing anything:
        {
            "should_block": bool,
            "state": "success" | "failure",
            "description": str,   # <=140 chars, fits GitHub's status description limit
            "blocking_findings": list[Finding],  # CRITICAL findings that triggered the block
        }
    """
    if min_critical_confidence > 0.0:
        blocking = [
            f for f in review.all_findings
            if f.severity.value == "CRITICAL" and f.confidence >= min_critical_confidence
        ]
    else:
        blocking = [f for f in review.all_findings if f.severity.value == "CRITICAL"]

    should_block = len(blocking) > 0

    if should_block:
        titles = ", ".join(f.title for f in blocking[:2])
        suffix = "..." if len(blocking) > 2 else ""
        description = f"{len(blocking)} critical issue(s): {titles}{suffix}"
    elif review.high_count > 0:
        description = f"No critical issues. {review.high_count} high-severity finding(s) to review."
    else:
        description = "No blocking issues found."

    return {
        "should_block": should_block,
        "state": STATUS_FAILURE if should_block else STATUS_SUCCESS,
        "description": description[:140],
        "blocking_findings": blocking,
    }