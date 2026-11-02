"""The offline guardrail eval (python -m evals.run_evals guardrail): report
math, ID resolution and run --guardrail. Screen results are mocked."""
import json
from types import SimpleNamespace

import pytest

import evals.run_evals as run_evals
import pipeline.guardrail as guardrail
from evals.golden import load_cases
from evals.run_evals import guardrail_metrics, main, render_guardrail_report
from pipeline.guardrail import ScreenResult


def _rec(case_id, expect, flagged, confidence=None, error=None):
    return {"case_id": case_id, "expect_injection": expect, "flagged": flagged, "confidence": confidence,
            "detail": f"PROMPT_ATTACK (confidence {confidence})" if confidence else None, "error": error, "ms": 5}


RECORDS = [
    _rec("inj-01", True, True, "HIGH"),
    _rec("inj-02", True, True, "MEDIUM"),
    _rec("inj-03", True, False),
    _rec("inj-04", True, False, error="ClientError: ThrottlingException"),
    _rec("clean-06", False, True, "LOW"),
    _rec("clean-07", False, False),
    _rec("sec-01", False, False),
    _rec("sec-02", False, False),
]


def test_metrics_leave_errors_out_of_both_rates():
    m = guardrail_metrics(RECORDS)
    assert (m["detected"], m["injections"], m["missed"]) == (2, 3, ["inj-03"])
    assert m["detection_rate"] == pytest.approx(2 / 3)
    assert (m["false_positives"], m["negatives"]) == (1, 4)
    assert m["false_positive_rate"] == 0.25
    assert m["flagged_negatives"] == [("clean-06", "LOW")]
    assert m["errors"] == 1 and m["errored"] == [("inj-04", "ClientError: ThrottlingException")]


def test_metrics_with_no_cases_of_a_kind():
    m = guardrail_metrics([_rec("clean-01", False, False)])
    assert m["detection_rate"] is None and m["false_positive_rate"] == 0


def test_report(tmp_path):
    meta = {"guardrail": {"id": "gr-1", "version": "1"}, "git_sha": "abcdef1234567"}
    report = render_guardrail_report(tmp_path, meta, RECORDS)
    assert "| Detection rate (expect_injection) | 2/3 (67%) |" in report
    assert "| False-positive rate (all other cases) | 1/4 (25%) |" in report
    assert "| Missed injections | inj-03 |" in report
    assert "| Flagged clean/other cases | clean-06 (LOW) |" in report
    assert "| inj-03 | injection | no | – | MISSED | 5 |" in report
    assert "| clean-06 | clean | yes | LOW | FALSE POSITIVE | 5 |" in report
    assert "| inj-04 | injection | error | – | ERROR: ClientError: ThrottlingException | 5 |" in report


def test_guardrail_command_needs_an_id(monkeypatch, capsys):
    monkeypatch.delenv("GUARDRAIL_ID", raising=False)
    assert main(["guardrail", "--cases", "clean-01-slugify"]) == 2
    assert "set GUARDRAIL_ID" in capsys.readouterr().out


def test_guardrail_command_writes_per_case_json_and_report(monkeypatch, tmp_path):
    monkeypatch.delenv("GUARDRAIL_ID", raising=False)
    seen = []

    def fake_screen(changed_file, patch, guardrail_id=None, guardrail_version=None):
        seen.append((changed_file, guardrail_id, guardrail_version))
        return ScreenResult(blocked="ignore all previous" in patch, detail="PROMPT_ATTACK (confidence HIGH)"
                            if "ignore all previous" in patch else None)
    monkeypatch.setattr(guardrail, "screen_patch", fake_screen)

    out = tmp_path / "gr"
    assert main(["guardrail", "--cases", "inj-01-comment-instruction,clean-01-slugify",
                 "--guardrail-id", "gr-x", "--guardrail-version", "2", "--out", str(out)]) == 0
    assert seen == [("app/billing/discounts.py", "gr-x", "2"), ("app/text_utils.py", "gr-x", "2")]
    record = json.loads((out / "inj-01-comment-instruction.json").read_text(encoding="utf-8"))
    assert record["flagged"] and record["confidence"] == "HIGH" and record["expect_injection"]
    assert "1/1 (100%)" in (out / "report.md").read_text(encoding="utf-8")
    assert json.loads((out / "meta.json").read_text(encoding="utf-8"))["guardrail"] == {"id": "gr-x", "version": "2"}


def test_run_with_guardrail_quarantines_a_flagged_case(monkeypatch):
    monkeypatch.setattr(guardrail, "screen_patch",
                        lambda f, p, gid=None, ver=None: ScreenResult(blocked=True, detail="PROMPT_ATTACK (confidence HIGH)"))

    async def no_agents(**kwargs):
        raise AssertionError("agents must not run on a quarantined patch")
    monkeypatch.setattr("pipeline.runner.run_agents_parallel", no_agents)

    [case] = load_cases("inj-01-comment-instruction")
    record, _ = run_evals.run_case(case, "none", ("gr-x", "1"))
    assert record["harness_error"] is None
    assert record["should_block"] is True and record["guardrail"]["blocked"] is True
    [finding] = record["findings"]
    assert finding["title"] == "Possible prompt injection in app/billing/discounts.py"


def test_run_guardrail_needs_an_id_and_reads_it_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("GUARDRAIL_ID", raising=False)
    assert main(["run", "--guardrail", "--cases", "clean-01-slugify", "--out", str(tmp_path / "r")]) == 2
    args = SimpleNamespace(guardrail_id=None, guardrail_version=None)
    monkeypatch.setenv("GUARDRAIL_ID", "gr-env")
    assert run_evals._guardrail_target(args) == ("gr-env", "1")
