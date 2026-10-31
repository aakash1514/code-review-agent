"""Line re-anchoring of agent findings to their snippet (KNOWN_ISSUES #10)."""
import asyncio

from models.findings import AgentReport
from pipeline.runner import run_agents_parallel
from tools.code_tools import new_file_lines, reanchor_finding_lines

# Two hunks of a GitHub-style patch (no file headers).
PATCH = """\
@@ -1,4 +1,5 @@
 import os
-OLD = 1
+NEW = 2
+EXTRA = 3

 def a():
@@ -20,3 +21,4 @@ def b():
     x = compute()
+    run(x)
     return x
+# end"""

# The same line added in two places.
DUPLICATE_PATCH = """\
@@ -1,2 +1,3 @@
 def a():
+    return cache[key]

@@ -10,2 +11,3 @@
 def b():
+    return cache[key]
"""


def _lines(findings):
    return [(f.location.line_start, f.location.line_end) for f in findings]


def test_hunks_map_added_and_context_lines_to_new_file_numbers():
    assert new_file_lines(PATCH) == [
        (1, "import os"), (2, "NEW = 2"), (3, "EXTRA = 3"), (4, ""), (5, "def a():"),
        (21, "    x = compute()"), (22, "    run(x)"), (23, "    return x"), (24, "# end"),
    ]


def test_snippet_in_added_lines(make_finding):
    [f] = reanchor_finding_lines([make_finding(snippet="run(x)", line_start=3, line_end=3)], PATCH)
    assert _lines([f]) == [(22, 22)]


def test_snippet_in_context_lines(make_finding):
    [f] = reanchor_finding_lines([make_finding(snippet="x = compute()", line_start=1, line_end=2)], PATCH)
    assert _lines([f]) == [(21, 21)]


def test_snippet_quoting_removed_lines_is_unchanged(make_finding):
    [f] = reanchor_finding_lines([make_finding(snippet="OLD = 1", line_start=7, line_end=8)], PATCH)
    assert _lines([f]) == [(7, 8)]


def test_multi_line_snippet_matches_consecutive_lines(make_finding):
    [f] = reanchor_finding_lines([make_finding(snippet="NEW = 2\nEXTRA = 3\n", line_start=40, line_end=40)], PATCH)
    assert _lines([f]) == [(2, 3)]


def test_multi_line_snippet_does_not_match_across_hunks(make_finding):
    [f] = reanchor_finding_lines([make_finding(snippet="def a():\nx = compute()", line_start=9, line_end=9)], PATCH)
    assert _lines([f]) == [(9, 9)]


def test_duplicate_snippet_takes_the_occurrence_nearest_the_reported_line(make_finding):
    findings = [make_finding(snippet="return cache[key]", line_start=1, line_end=1),
                make_finding(snippet="return cache[key]", line_start=10, line_end=10)]
    assert _lines(reanchor_finding_lines(findings, DUPLICATE_PATCH)) == [(2, 2), (12, 12)]


def test_whitespace_differences_are_ignored(make_finding):
    findings = [make_finding(snippet="   NEW   =\t2  ", line_start=9, line_end=9),
                make_finding(snippet="  x = compute()\n      run(x)", line_start=9, line_end=9)]
    assert _lines(reanchor_finding_lines(findings, PATCH)) == [(2, 2), (21, 22)]


def test_empty_snippet_is_unchanged(make_finding):
    [f] = reanchor_finding_lines([make_finding(snippet="", line_start=5, line_end=6)], PATCH)
    assert _lines([f]) == [(5, 6)]


def test_input_findings_are_not_mutated(make_finding):
    original = make_finding(snippet="run(x)", line_start=3, line_end=3)
    [f] = reanchor_finding_lines([original], PATCH)
    assert _lines([original]) == [(3, 3)]
    assert f is not original and _lines([f]) == [(22, 22)]


def test_runner_reanchors_every_agents_findings_before_the_callback(make_finding, capsys):
    def agent(changed_file, diff_snippet, retrieved_context):
        return AgentReport(agent_name="fake", findings=[make_finding(snippet="run(x)", line_start=3, line_end=3),
                                                        make_finding(snippet="not in the patch", line_start=4)])

    seen = []

    async def on_complete(report):
        seen.append(_lines(report.findings))

    reports = asyncio.run(run_agents_parallel("a.py", PATCH, "", on_agent_complete=on_complete,
                                              agent_functions=[agent, agent]))
    assert [_lines(r.findings) for r in reports] == [[(22, 22), (4, 1)]] * 2
    assert seen == [[(22, 22), (4, 1)]] * 2
    assert "[runner] re-anchored 2/4 finding(s) in a.py" in capsys.readouterr().out
