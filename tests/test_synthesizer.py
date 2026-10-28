import pytest

from models.findings import AgentReport, Severity
from pipeline.synthesizer import build_review_report, deduplicate_findings, rank_findings


def test_exact_duplicates_are_removed_keeping_the_most_confident(make_finding):
    low = make_finding(title="SQL built from input", snippet="q = f'SELECT {x}'", confidence=0.6)
    high = make_finding(title="SQL built from input", snippet="q = f'SELECT {x}'", confidence=0.95)

    result = deduplicate_findings([low, high])

    assert len(result) == 1
    assert result[0].confidence == 0.95


def test_whitespace_only_snippet_differences_count_as_exact_duplicates(make_finding):
    a = make_finding(snippet="return  a /\n b")
    b = make_finding(snippet="return a / b")
    assert len(deduplicate_findings([a, b])) == 1


@pytest.mark.parametrize("override", [
    {"snippet": "return a / c"},          # different code
    {"file": "other.py"},                 # same code, different file
    {"category": "SECURITY"},             # same code, different agent
])
def test_near_duplicates_are_kept(make_finding, override):
    base = make_finding(snippet="return a / b")
    near = make_finding(**{"snippet": "return a / b", **override})
    assert len(deduplicate_findings([base, near])) == 2


def test_distinct_findings_without_snippets_are_both_kept(make_finding):
    a = make_finding(title="Missing docstring for foo", category="DOCUMENTATION", snippet="")
    b = make_finding(title="Missing docstring for bar", category="DOCUMENTATION", snippet="")
    assert len(deduplicate_findings([a, b])) == 2


def test_snippetless_findings_dedup_on_normalised_title_and_line(make_finding):
    def doc(title, line, snippet=""):
        return make_finding(title=title, category="DOCUMENTATION", snippet=snippet,
                            line_start=line, line_end=line)

    # Same finding re-reported: case/whitespace in the title and a
    # whitespace-only snippet don't make it distinct.
    assert len(deduplicate_findings([doc("Missing docstring", 10), doc("  missing   DOCSTRING ", 10, "  \n")])) == 1
    # Same title on different lines is two findings.
    assert len(deduplicate_findings([doc("Missing docstring", 10), doc("Missing docstring", 20)])) == 2
    # No line number on either: the title alone decides.
    assert len(deduplicate_findings([doc("Missing docstring", 0), doc("missing docstring", 0)])) == 1
    # A snippetless finding never merges with one that has a snippet.
    assert len(deduplicate_findings([doc("Missing docstring", 10), doc("Missing docstring", 10, "def f(): ...")])) == 2


def test_ranking_is_critical_to_info_then_confidence(make_finding):
    findings = [
        make_finding(title="info", severity="INFO", snippet="1"),
        make_finding(title="high-low-conf", severity="HIGH", snippet="2", confidence=0.5),
        make_finding(title="critical", severity="CRITICAL", snippet="3"),
        make_finding(title="low", severity="LOW", snippet="4"),
        make_finding(title="high-high-conf", severity="HIGH", snippet="5", confidence=0.9),
        make_finding(title="medium", severity="MEDIUM", snippet="6"),
    ]

    ranked = [f.title for f in rank_findings(findings)]

    assert ranked == ["critical", "high-high-conf", "high-low-conf", "medium", "low", "info"]


def test_build_review_report_dedups_ranks_and_counts(make_finding):
    security = AgentReport(agent_name="security", findings=[
        make_finding(title="Hardcoded key", severity="CRITICAL", category="SECURITY", snippet="KEY='x'"),
        make_finding(title="Weak hash", severity="HIGH", category="SECURITY", snippet="md5(p)"),
    ])
    architecture = AgentReport(agent_name="architecture", findings=[
        make_finding(title="Long function", severity="LOW", snippet="def f(): ..."),
        make_finding(title="Long function", severity="LOW", snippet="def f(): ..."),  # exact dup
        make_finding(title="Coupling", severity="HIGH", snippet="import everything"),
    ])

    review = build_review_report(pr_number=3, repo="o/r", commit_sha="abc", agent_reports=[security, architecture])

    assert [f.severity for f in review.all_findings] == [
        Severity.CRITICAL, Severity.HIGH, Severity.HIGH, Severity.LOW,
    ]
    assert review.critical_count == 1
    assert review.high_count == 2
    assert review.hitl_required is True
    assert review.agent_reports == [security, architecture]


def test_build_review_report_with_no_findings(make_finding):
    review = build_review_report(pr_number=1, repo="o/r", commit_sha="abc",
                                 agent_reports=[AgentReport(agent_name="security")])
    assert review.all_findings == []
    assert (review.critical_count, review.high_count, review.hitl_required) == (0, 0, False)
