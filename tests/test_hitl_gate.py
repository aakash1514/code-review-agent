from models.findings import ReviewReport
from pipeline.hitl_gate import evaluate_hitl_gate


def _review(findings):
    review = ReviewReport(pr_number=1, repo="o/r", commit_sha="abc", all_findings=findings)
    review.recompute_counts()
    return review


def test_any_critical_blocks_with_failure_state(make_finding):
    critical = make_finding(title="Hardcoded secret", severity="CRITICAL")
    gate = evaluate_hitl_gate(_review([critical, make_finding(title="nit", severity="LOW", snippet="y")]))

    assert gate["should_block"] is True
    assert gate["state"] == "failure"
    assert gate["blocking_findings"] == [critical]
    assert gate["description"].startswith("1 critical issue(s): Hardcoded secret")


def test_no_critical_is_success(make_finding):
    gate = evaluate_hitl_gate(_review([make_finding(severity="HIGH"), make_finding(severity="MEDIUM", snippet="y")]))

    assert gate["should_block"] is False
    assert gate["state"] == "success"
    assert gate["blocking_findings"] == []
    assert "1 high-severity" in gate["description"]


def test_empty_review_is_success():
    gate = evaluate_hitl_gate(_review([]))
    assert (gate["should_block"], gate["state"]) == (False, "success")
    assert gate["description"] == "No blocking issues found."


def test_description_fits_github_status_limit(make_finding):
    findings = [make_finding(title="T" * 100, severity="CRITICAL", snippet=str(i)) for i in range(3)]
    gate = evaluate_hitl_gate(_review(findings))
    assert gate["should_block"] is True
    assert len(gate["description"]) <= 140


def test_min_confidence_ignores_low_confidence_criticals(make_finding):
    unsure = make_finding(title="Maybe critical", severity="CRITICAL", confidence=0.3)
    gate = evaluate_hitl_gate(_review([unsure]), min_critical_confidence=0.5)
    assert (gate["should_block"], gate["state"]) == (False, "success")
