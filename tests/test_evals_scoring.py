"""Scorer for the eval harness (evals/scoring.py), on synthetic findings."""
import pytest

from evals import scoring


def _f(category="SECURITY", severity="HIGH", start=10, end=None, title="t"):
    return {"category": category, "severity": severity, "title": title,
            "location": {"file": "app.py", "line_start": start, "line_end": end if end is not None else start}}


def _labels(issues=(), acceptable=(), expect_block=False, case_id="c1"):
    return {"id": case_id, "expect_block": expect_block, "issues": list(issues), "acceptable": list(acceptable)}


def _issue(iid="i1", category="SECURITY", lines=(10, 12), severity="HIGH"):
    return {"id": iid, "category": category, "lines": list(lines), "severity": severity}


def _run(findings, should_block=False, wall_ms=1000, calls=4, errors=()):
    return {"findings": findings, "should_block": should_block, "wall_ms": wall_ms, "llm_calls": calls,
            "prompt_tokens": 100, "completion_tokens": 50, "reasoning_tokens": 20,
            "agents": [{"agent_name": a, "error": "boom"} for a in errors]}


def _statuses(labels, findings):
    return [c["status"] for c in scoring.classify_findings(labels, findings)]


@pytest.mark.parametrize("start, end, matches", [
    (7, 7, True),     # 3 before the issue's first line
    (6, 6, False),    # 4 before
    (15, 15, True),   # 3 after the last line
    (16, 16, False),  # 4 after
    (1, 30, True),    # wide finding covering the issue
    (11, 11, True),
])
def test_overlap_tolerance_is_three_lines(start, end, matches):
    labels = _labels([_issue(lines=(10, 12))])
    assert _statuses(labels, [_f(start=start, end=end)]) == ["match" if matches else "unmatched"]


def test_category_must_match():
    labels = _labels([_issue(category="SECURITY")])
    assert _statuses(labels, [_f(category="ARCHITECTURE")]) == ["unmatched"]


def test_finding_without_lines_never_matches():
    labels = _labels([_issue()])
    assert _statuses(labels, [_f(start=0, end=0)]) == ["unmatched"]


def test_each_issue_matches_once_and_extra_findings_are_duplicates():
    labels = _labels([_issue()])
    findings = [_f(severity="MEDIUM"), _f(severity="CRITICAL")]
    classified = scoring.classify_findings(labels, findings)
    # the most severe finding takes the match, whatever the input order
    assert [c["status"] for c in classified] == ["duplicate", "match"]

    m = scoring.compute_metrics([(labels, _run(findings))])
    assert m["SECURITY.tp"] == 1
    assert m["SECURITY.dup"] == 1
    assert m["SECURITY.fp"] == 0
    assert m["SECURITY.precision"] == 1.0


def test_one_finding_over_two_issues_matches_only_one():
    labels = _labels([_issue("a", lines=(10, 10)), _issue("b", lines=(12, 12))])
    m = scoring.compute_metrics([(labels, _run([_f(start=10, end=12)]))])
    assert m["SECURITY.matched"] == 1
    assert m["SECURITY.recall"] == 0.5


def test_low_and_info_are_excluded_from_precision_but_count_for_recall():
    labels = _labels([_issue()])
    findings = [_f(severity="LOW"), _f(severity="INFO", start=50)]
    m = scoring.compute_metrics([(labels, _run(findings))])
    assert m["SECURITY.recall"] == 1.0
    assert m["SECURITY.low"] == 2
    assert (m["SECURITY.tp"], m["SECURITY.fp"]) == (0, 0)
    assert m["SECURITY.precision"] is None


def test_acceptable_findings_are_not_false_positives():
    labels = _labels([], acceptable=[{"category": "TEST_COVERAGE", "lines": [20, 30]}])
    findings = [_f(category="TEST_COVERAGE", severity="MEDIUM", start=25),
                _f(category="TEST_COVERAGE", severity="MEDIUM", start=80)]
    assert _statuses(labels, findings) == ["acceptable", "unmatched"]
    m = scoring.compute_metrics([(labels, _run(findings))])
    assert m["TEST_COVERAGE.acceptable"] == 1
    assert m["TEST_COVERAGE.fp"] == 1
    assert m["TEST_COVERAGE.precision"] == 0.0


def test_severity_agreement():
    labels = _labels([_issue("a", lines=(10, 10), severity="CRITICAL"),
                      _issue("b", lines=(40, 40), severity="MEDIUM"),
                      _issue("c", lines=(80, 80), severity="CRITICAL")])
    findings = [_f(severity="CRITICAL", start=10), _f(severity="HIGH", start=40), _f(severity="MEDIUM", start=80)]
    m = scoring.compute_metrics([(labels, _run(findings))])
    assert m["severity.matched"] == 3
    assert m["severity.exact"] == pytest.approx(1 / 3)
    assert m["severity.within_one"] == pytest.approx(2 / 3)


def test_false_critical_rate_and_clean_cases_with_critical():
    flagged = _labels([_issue(severity="CRITICAL"), _issue("h", lines=(40, 40), severity="HIGH")],
                      expect_block=True, case_id="flagged")
    clean = _labels([], case_id="clean")
    results = [
        (flagged, _run([_f(severity="CRITICAL", start=11),     # true CRITICAL
                        _f(severity="CRITICAL", start=12),     # duplicate, still on a CRITICAL issue
                        _f(severity="CRITICAL", start=40)],    # over-escalated HIGH issue
                       should_block=True)),
        (clean, _run([_f(severity="CRITICAL", start=5)], should_block=True)),
    ]
    m = scoring.compute_metrics(results)
    assert m["critical.total"] == 4
    assert m["critical.false"] == 2
    assert m["critical.false_rate"] == 0.5
    assert m["critical.clean_cases_with_critical"] == 1


def test_block_accuracy():
    results = [
        (_labels(expect_block=True), _run([], should_block=True)),    # correct
        (_labels(expect_block=True), _run([], should_block=False)),   # missed
        (_labels(expect_block=False), _run([], should_block=True)),   # false block
        (_labels(expect_block=False), _run([], should_block=False)),  # correct
    ]
    m = scoring.compute_metrics(results)
    assert m["block.accuracy"] == 0.5
    assert (m["block.false_blocks"], m["block.missed_blocks"]) == (1, 1)


def test_ops_metrics():
    results = [(_labels(), _run([], wall_ms=ms, errors=("security",) if ms == 3000 else ()))
               for ms in (1000, 2000, 3000, 4000)]
    m = scoring.compute_metrics(results)
    assert m["ops.latency_ms_mean"] == 2500
    assert m["ops.latency_ms_p50"] == 2000
    assert m["ops.latency_ms_p95"] == 4000
    assert m["ops.llm_calls_mean"] == 4
    assert m["ops.runs_with_agent_errors"] == 1


def test_repeats_are_split_and_aggregated_to_mean_and_range():
    labels = _labels([_issue()], expect_block=False)
    case_json = {"runs": [_run([_f()]), _run([]), _run([_f()])]}
    passes = scoring.results_by_repeat([(labels, case_json)])
    assert len(passes) == 3
    agg = scoring.aggregate([scoring.compute_metrics(p) for p in passes])
    assert agg["SECURITY.recall"] == {"mean": pytest.approx(2 / 3), "min": 0.0, "max": 1.0, "n": 3}
    # precision is undefined in the repeat with no findings and is skipped
    assert agg["SECURITY.precision"]["n"] == 2


def test_harness_crashes_are_left_out_of_repeats():
    labels = _labels()
    case_json = {"runs": [{"harness_error": "RuntimeError: x"}, _run([])]}
    passes = scoring.results_by_repeat([(labels, case_json)])
    assert [len(p) for p in passes] == [0, 1]


def test_unmatched_findings_lists_medium_and_above_only():
    labels = _labels([_issue()], acceptable=[{"category": "ARCHITECTURE", "lines": [30, 30]}])
    case_json = {"runs": [_run([_f(), _f(severity="HIGH", start=60, title="extra"),
                                _f(severity="LOW", start=70), _f(category="ARCHITECTURE", severity="MEDIUM", start=30)])]}
    rows = scoring.unmatched_findings([(labels, case_json)])
    assert [(r["title"], r["lines"], r["acceptable"]) for r in rows] == [("extra", (60, 60), False), ("t", (30, 30), True)]
