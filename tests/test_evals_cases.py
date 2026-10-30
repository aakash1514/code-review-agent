"""Dry check of the eval golden set: every case loads, its labels are valid
and its patch builds. No LLM calls."""
from collections import Counter

import pytest

from evals.golden import (all_case_ids, build_patch, load_case, load_cases, smoke_case_ids, validate_case,
                          visible_new_lines, CASES_DIR)
from evals.run_evals import build_context, disable_floor, disable_severity_cap
from pipeline.file_filter import plan_review_files
from tools.code_tools import strip_diff_to_source

ALL_IDS = all_case_ids()


def test_golden_set_has_33_cases_in_the_planned_groups():
    groups = Counter(case_id.split("-")[0] for case_id in ALL_IDS)
    assert groups == {"sec": 8, "arch": 8, "test": 6, "doc": 6, "clean": 5}


@pytest.mark.parametrize("case_id", ALL_IDS)
def test_case_is_valid(case_id):
    assert validate_case(load_case(CASES_DIR / case_id)) == []


def test_smoke_subset():
    ids = smoke_case_ids()
    assert len(ids) == len(set(ids)) == 8
    assert set(ids) <= set(ALL_IDS)
    assert Counter(i.split("-")[0] for i in ids) == {"sec": 2, "arch": 2, "test": 1, "doc": 1, "clean": 2}


def test_new_file_patch_is_one_added_hunk_without_headers():
    patch = build_patch("", "a\nb\n")
    assert patch.splitlines() == ["@@ -0,0 +1,2 @@", "+a", "+b"]
    assert strip_diff_to_source(patch) == "a\nb"


def test_modified_file_patch_keeps_context_lines():
    patch = build_patch("a\nb\nc\n", "a\nB\nc\n")
    assert patch.splitlines() == ["@@ -1,3 +1,3 @@", " a", "-b", "+B", " c"]
    assert visible_new_lines(patch) == [(1, 3)]


def test_docs_cases_go_to_the_documentation_agent_only():
    for case in load_cases("all"):
        [target] = plan_review_files([case.github_file()]).to_review
        assert target.docs_only == case.file_path.endswith(".md"), case.id


def test_kg_context_is_built_locally(capsys):
    case = load_cases("test-01-new-public-function")[0]
    text, info = build_context(case, case.patch, "kg")
    assert "tests/test_billing.py::test_invoice_total_sums_lines" in text
    assert info["changed_functions"] == ["invoice_total", "late_fee"]
    assert build_context(case, case.patch, "none") == ("", {"mode": "none"})


def test_disable_severity_cap(monkeypatch, make_finding):
    import agents.architecture_agent as arch
    monkeypatch.setattr(arch, "_cap_routine_edge_case_severity", arch._cap_routine_edge_case_severity)
    finding = make_finding(title="Division by zero", severity="CRITICAL")
    assert arch._cap_routine_edge_case_severity([finding])[0].severity.value == "MEDIUM"
    disable_severity_cap()
    assert arch._cap_routine_edge_case_severity([finding])[0].severity.value == "CRITICAL"


def test_disable_floor(monkeypatch, make_finding):
    import agents.security_agent as sec
    monkeypatch.setattr(sec, "_floor_canonical_vulnerabilities", sec._floor_canonical_vulnerabilities)
    finding = make_finding(title="Command injection", severity="HIGH", category="SECURITY",
                           snippet="subprocess.run(cmd, shell=True)")
    assert sec._floor_canonical_vulnerabilities([finding], "")[0].severity.value == "CRITICAL"
    disable_floor()
    assert sec._floor_canonical_vulnerabilities([finding], "")[0].severity.value == "HIGH"
