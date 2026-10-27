import pytest

from agents.architecture_agent import _cap_routine_edge_case_severity
from models.findings import Severity


@pytest.mark.parametrize("title, description", [
    ("Division by zero in calculate_ratio", "Raises ZeroDivisionError when total is 0."),
    ("Missing null check", "user may be None here."),
    ("Unhandled edge case", "Empty list is not handled."),
    ("Possible KeyError", "config['x'] may be missing."),
    ("Index out of range", "items[0] fails on an empty list."),
])
def test_routine_edge_case_critical_is_capped_to_medium(make_finding, title, description):
    finding = make_finding(title=title, description=description, severity="CRITICAL")

    [capped] = _cap_routine_edge_case_severity([finding])

    assert capped.severity == Severity.MEDIUM
    assert capped.title == title


def test_keyword_match_is_case_insensitive_and_checks_description(make_finding):
    finding = make_finding(title="Crash risk", description="An UNHANDLED EXCEPTION escapes here.",
                           severity="CRITICAL")
    assert _cap_routine_edge_case_severity([finding])[0].severity == Severity.MEDIUM


@pytest.mark.parametrize("title, description", [
    ("Data corruption on concurrent writes", "Two writers can interleave and corrupt the ledger."),
    ("Global state shared across all requests", "A module-level dict leaks data between users."),
])
def test_real_critical_issue_is_kept(make_finding, title, description):
    finding = make_finding(title=title, description=description, severity="CRITICAL")
    assert _cap_routine_edge_case_severity([finding])[0].severity == Severity.CRITICAL


@pytest.mark.parametrize("severity", ["HIGH", "MEDIUM", "LOW", "INFO"])
def test_non_critical_findings_are_never_changed(make_finding, severity):
    finding = make_finding(title="Division by zero", severity=severity)
    assert _cap_routine_edge_case_severity([finding])[0].severity == Severity(severity)


def test_original_finding_is_not_mutated_and_order_is_kept(make_finding):
    routine = make_finding(title="Division by zero", severity="CRITICAL")
    real = make_finding(title="Data corruption", severity="CRITICAL")

    result = _cap_routine_edge_case_severity([routine, real])

    assert [f.title for f in result] == ["Division by zero", "Data corruption"]
    assert [f.severity for f in result] == [Severity.MEDIUM, Severity.CRITICAL]
    assert routine.severity == Severity.CRITICAL  # model_copy, not in-place
