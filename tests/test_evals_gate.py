"""The CI eval gate (python -m evals.run_evals gate): thresholds, rules and
the missing-case check, on synthetic run directories built from the real
smoke labels. No LLM calls."""
import json

import pytest

from evals.golden import load_cases
from evals.run_evals import THRESHOLDS_FILE, GateConfigError, load_thresholds, main

SMOKE = load_cases("smoke")
THRESHOLDS = {"false_blocks_max": 0, "block_recall_min": 0.5, "recall_min": 0.6,
              "false_critical_rate_max": 0.25, "agent_error_runs_max": 1}


def _finding(issue, severity=None):
    return {"category": issue["category"], "severity": severity or issue["severity"], "title": issue["id"],
            "location": {"file": "f", "line_start": issue["lines"][0], "line_end": issue["lines"][1], "snippet": ""}}


def _perfect_run(case):
    """Every labelled issue found at its own severity; blocked iff expected."""
    return {"findings": [_finding(i) for i in case.issues], "should_block": case.expect_block,
            "wall_ms": 1000, "llm_calls": 4, "prompt_tokens": 100, "completion_tokens": 50,
            "reasoning_tokens": 20, "cost_usd": 0.001, "harness_error": None,
            "agents": [{"agent_name": "security", "error": None, "runtime_ms": 1, "findings": 0, "summary": ""}]}


def _write_run(run_dir, edit=None, skip=()):
    """A run directory with one run per smoke case; edit(case_id, run) may change a run."""
    run_dir.mkdir(exist_ok=True)
    (run_dir / "meta.json").write_text(json.dumps({"flags": {"cases": "smoke", "context": "kg"}}), encoding="utf-8")
    for case in SMOKE:
        if case.id in skip:
            continue
        run = _perfect_run(case)
        if edit:
            edit(case.id, run)
        (run_dir / f"{case.id}.json").write_text(
            json.dumps({"case_id": case.id, "labels": case.labels, "runs": [run]}), encoding="utf-8")
    return run_dir


@pytest.fixture
def thresholds(tmp_path):
    path = tmp_path / "thresholds.yaml"
    path.write_text("\n".join(f"{k}: {v}" for k, v in THRESHOLDS.items()), encoding="utf-8")
    return path


def _gate(run_dir, thresholds):
    return main(["gate", str(run_dir), "--thresholds", str(thresholds)])


def _expect_block_ids():
    return [c.id for c in SMOKE if c.expect_block]


def test_smoke_set_has_cases_the_rules_need():
    assert len(_expect_block_ids()) == 2
    assert any(not c.issues for c in SMOKE)


def test_passing_run_exits_0_and_writes_gate_md(tmp_path, thresholds, capsys):
    run_dir = _write_run(tmp_path / "run")

    assert _gate(run_dir, thresholds) == 0

    out = capsys.readouterr().out
    assert "| False blocks | 0 | <= 0 | pass |" in out
    assert "| Block recall | 1.00 | >= 0.50 | pass |" in out
    assert "| False-CRITICAL rate | 0.00 | <= 0.25 | pass |" in out
    assert "PASSED: all 5 rules" in out
    gate_md = (run_dir / "gate.md").read_text(encoding="utf-8")
    assert "**PASSED**" in gate_md and "| Recall (strict, all categories) | 1.00 | >= 0.60 | pass |" in gate_md
    assert "## Eval report: run" in gate_md and "## Per case" in gate_md


def _block_clean(cid, run):
    if cid.startswith("clean-01"):
        run["should_block"] = True


def _miss_all_blocks(cid, run):
    if cid in _expect_block_ids():
        run["should_block"] = False


def _find_nothing(cid, run):
    run["findings"] = [f for f in run["findings"] if f["severity"] == "CRITICAL"]


def _false_criticals(cid, run):
    if cid.startswith("clean-"):
        run["findings"].append({"category": "SECURITY", "severity": "CRITICAL", "title": "made up",
                                "location": {"file": "f", "line_start": 1, "line_end": 1, "snippet": ""}})


def _agent_errors(cid, run):
    if cid.startswith(("clean-", "doc-")):
        run["agents"][0]["error"] = "ModelUnavailableError: boom"


@pytest.mark.parametrize("edit, rule, row", [
    (_block_clean, "False blocks", "| False blocks | 1 | <= 0 | **FAIL** |"),
    (_miss_all_blocks, "Block recall", "| Block recall | 0.00 | >= 0.50 | **FAIL** |"),
    (_find_nothing, "Recall (strict, all categories)", "| Recall (strict, all categories) | 0.29 | >= 0.60 | **FAIL** |"),
    (_false_criticals, "False-CRITICAL rate", "| False-CRITICAL rate | 0.50 | <= 0.25 | **FAIL** |"),
    (_agent_errors, "Runs with an agent error", "| Runs with an agent error | 3 | <= 1 | **FAIL** |"),
])
def test_each_failing_rule_exits_1(tmp_path, thresholds, capsys, edit, rule, row):
    run_dir = _write_run(tmp_path / "run", edit)

    assert _gate(run_dir, thresholds) == 1

    out = capsys.readouterr().out
    assert row in out
    assert f"FAILED: {rule}" in out
    assert out.count("**FAIL**") == 1  # only the rule under test
    assert f"**FAILED**: {rule}" in (run_dir / "gate.md").read_text(encoding="utf-8")


def test_no_criticals_means_a_false_critical_rate_of_zero(tmp_path, thresholds, capsys):
    def no_criticals(cid, run):
        run["findings"] = [f for f in run["findings"] if f["severity"] != "CRITICAL"]
    assert _gate(_write_run(tmp_path / "run", no_criticals), thresholds) == 0
    assert "| False-CRITICAL rate | 0.00 | <= 0.25 | pass |" in capsys.readouterr().out


def test_missing_and_crashed_cases_fail_the_gate(tmp_path, thresholds, capsys):
    crashed_id = "clean-03-add-logging"

    def crash(cid, run):
        if cid == crashed_id:
            run.update(harness_error="RuntimeError: boom", findings=[])

    run_dir = _write_run(tmp_path / "run", crash, skip={"doc-04-readme-wrong-instructions"})

    assert _gate(run_dir, thresholds) == 1

    out = capsys.readouterr().out
    assert "the run is incomplete (8 `smoke` cases expected)" in out
    assert "1 case(s) have no result: doc-04-readme-wrong-instructions" in out
    assert f"1 case(s) only crashed in the harness: {crashed_id}" in out
    assert "**FAILED**: eval gate FAILED: the run is incomplete" in (run_dir / "gate.md").read_text(encoding="utf-8")


def test_missing_run_directory_fails(tmp_path, thresholds, capsys):
    assert _gate(tmp_path / "nope", thresholds) == 1
    assert "doesn't exist" in capsys.readouterr().out


@pytest.mark.parametrize("text, problem", [
    ("false_blocks_max: 0\n", "missing block_recall_min"),
    ({**THRESHOLDS, "extra_rule": 1}, "unknown key extra_rule"),
    ({**THRESHOLDS, "recall_min": 1.5}, "recall_min must be between 0 and 1"),
    ({**THRESHOLDS, "recall_min": "high"}, "recall_min must be a number"),
    ({**THRESHOLDS, "false_blocks_max": 0.5}, "false_blocks_max must be a non-negative integer"),
    ({**THRESHOLDS, "agent_error_runs_max": -1}, "agent_error_runs_max must be a non-negative integer"),
    ("- just a list\n", "expected a mapping"),
])
def test_bad_thresholds_are_rejected(tmp_path, capsys, text, problem):
    path = tmp_path / "thresholds.yaml"
    if isinstance(text, dict):
        text = "\n".join(f"{k}: {v}" for k, v in text.items())
    path.write_text(text, encoding="utf-8")

    with pytest.raises(GateConfigError, match=problem):
        load_thresholds(path)
    assert _gate(_write_run(tmp_path / "run"), path) == 2
    assert "bad thresholds file" in capsys.readouterr().out


def test_repo_thresholds_file_is_valid():
    assert load_thresholds(THRESHOLDS_FILE) == THRESHOLDS
