"""Scores saved eval runs against the golden labels. Pure functions, no I/O
and no LLM calls, so a run can be re-scored after the labels change.

Matching: a finding matches a labelled issue when the category is the same
and the finding's line range overlaps the issue's lines widened by
LINE_TOLERANCE on each side. Findings are matched most severe first; each
issue takes at most one finding, and further findings on an already-matched
issue are duplicates. An unmatched finding that overlaps an `acceptable`
entry of its category is "acceptable" (a real-but-minor issue), not a false
positive.

Precision counts only findings of severity >= MEDIUM; recall counts an issue
as found when any finding matched it, whatever its severity.

Snippet-anchored scoring is a second, secondary metric: agents' reported
line numbers are often off (docs/KNOWN_ISSUES.md #10), so anchor_run moves
each finding to where its snippet sits in the file after the change (first
whitespace-normalized match), keeping the reported lines when the snippet is
empty or not found. The same metrics are then computed on the anchored runs.
"""
import copy
import math
from statistics import mean

SEVERITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITY_ORDER)}
CATEGORIES = ("SECURITY", "ARCHITECTURE", "TEST_COVERAGE", "DOCUMENTATION")
LINE_TOLERANCE = 3

MATCH, DUPLICATE, ACCEPTABLE, UNMATCHED = "match", "duplicate", "acceptable", "unmatched"


def counts_for_precision(severity: str) -> bool:
    return SEVERITY_RANK[severity] <= SEVERITY_RANK["MEDIUM"]


def finding_lines(finding: dict) -> tuple[int, int] | None:
    loc = finding.get("location") or {}
    start, end = loc.get("line_start", 0), loc.get("line_end", 0)
    if start <= 0 and end <= 0:
        return None  # the agent gave no line
    if start <= 0:
        start = end
    if end < start:
        end = start
    return start, end


def overlaps(finding: dict, lines: list[int], tolerance: int = LINE_TOLERANCE) -> bool:
    span = finding_lines(finding)
    if span is None:
        return False
    return span[0] <= lines[1] + tolerance and lines[0] - tolerance <= span[1]


def snippet_span(snippet: str, source: str) -> tuple[int, int] | None:
    """(first, last) line of the first occurrence of snippet in source, with
    whitespace normalized (compared as whitespace-separated tokens), or None."""
    want = snippet.split()
    if not want:
        return None
    tokens = [(tok, n) for n, line in enumerate(source.splitlines(), start=1) for tok in line.split()]
    for i in range(len(tokens) - len(want) + 1):
        if all(tokens[i + j][0] == want[j] for j in range(len(want))):
            return tokens[i][1], tokens[i + len(want) - 1][1]
    return None


def anchor_run(run: dict, source: str | None) -> dict:
    """Copy of run whose findings take their lines from their snippet's place
    in source; findings with no snippet, or one not found, keep their lines.
    Adds run["anchored"] = number of findings moved or confirmed by the snippet."""
    out = copy.deepcopy(run)
    out["anchored"] = 0
    for f in out.get("findings", []):
        loc = f.get("location") or {}
        span = snippet_span(loc.get("snippet") or "", source) if source else None
        if span:
            loc["line_start"], loc["line_end"] = span
            out["anchored"] += 1
    return out


def anchor_cases(cases: list[tuple[dict, dict]], sources: dict[str, str]) -> list[tuple[dict, dict]]:
    """(labels, case_json) pairs with every run anchored to sources[case id]."""
    return [(labels, {**cj, "runs": [r if r.get("harness_error") else anchor_run(r, sources.get(labels["id"]))
                                      for r in cj["runs"]]})
            for labels, cj in cases]


def classify_findings(labels: dict, findings: list[dict]) -> list[dict]:
    """One entry per finding, in the original order:
    {"finding", "status": match|duplicate|acceptable|unmatched, "issue": id|None}."""
    issues = labels.get("issues") or []
    acceptable = labels.get("acceptable") or []
    result = [None] * len(findings)
    matched: set[str] = set()
    order = sorted(range(len(findings)), key=lambda i: SEVERITY_RANK[findings[i]["severity"]])
    for i in order:
        f = findings[i]
        hits = [iss for iss in issues if iss["category"] == f["category"] and overlaps(f, iss["lines"])]
        free = [iss for iss in hits if iss["id"] not in matched]
        if free:
            matched.add(free[0]["id"])
            result[i] = {"finding": f, "status": MATCH, "issue": free[0]["id"]}
        elif hits:
            result[i] = {"finding": f, "status": DUPLICATE, "issue": hits[0]["id"]}
        elif any(a["category"] == f["category"] and overlaps(f, a["lines"]) for a in acceptable):
            result[i] = {"finding": f, "status": ACCEPTABLE, "issue": None}
        else:
            result[i] = {"finding": f, "status": UNMATCHED, "issue": None}
    return result


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def _percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def compute_metrics(results: list[tuple[dict, dict]]) -> dict:
    """Metrics for one pass over the cases. results: (labels, run) pairs,
    where run is one entry of a case JSON's "runs". Returns a flat dict of
    name -> number (None when undefined, e.g. precision with no findings)."""
    m: dict[str, float | None] = {}
    per_cat = {c: {"issues": 0, "matched": 0, "tp": 0, "fp": 0, "dup": 0, "acceptable": 0, "low": 0}
               for c in CATEGORIES}
    sev_exact = sev_within_one = sev_total = 0
    critical_total = critical_false = clean_with_critical = 0
    block_correct = false_blocks = missed_blocks = blocks = expected_blocks = caught_blocks = 0
    runs_with_agent_errors = 0
    latencies, calls, prompt_toks, completion_toks, reasoning_toks = [], [], [], [], []

    for labels, run in results:
        findings = run["findings"]
        issues = labels.get("issues") or []
        classified = classify_findings(labels, findings)
        issue_by_id = {iss["id"]: iss for iss in issues}

        for iss in issues:
            per_cat[iss["category"]]["issues"] += 1
        for c in classified:
            f, cat = c["finding"], c["finding"]["category"]
            if c["status"] == MATCH:
                per_cat[cat]["matched"] += 1
                label_sev = issue_by_id[c["issue"]]["severity"]
                diff = abs(SEVERITY_RANK[f["severity"]] - SEVERITY_RANK[label_sev])
                sev_total += 1
                sev_exact += diff == 0
                sev_within_one += diff <= 1
            if not counts_for_precision(f["severity"]):
                per_cat[cat]["low"] += 1
            elif c["status"] == MATCH:
                per_cat[cat]["tp"] += 1
            elif c["status"] == DUPLICATE:
                per_cat[cat]["dup"] += 1
            elif c["status"] == ACCEPTABLE:
                per_cat[cat]["acceptable"] += 1
            else:
                per_cat[cat]["fp"] += 1

        criticals = [f for f in findings if f["severity"] == "CRITICAL"]
        critical_total += len(criticals)
        critical_false += sum(
            1 for f in criticals
            if not any(iss["severity"] == "CRITICAL" and iss["category"] == f["category"]
                       and overlaps(f, iss["lines"]) for iss in issues))
        if not issues and criticals:
            clean_with_critical += 1

        expect, got = labels["expect_block"], run["should_block"]
        block_correct += expect == got
        false_blocks += got and not expect
        missed_blocks += expect and not got
        blocks += got
        expected_blocks += expect
        caught_blocks += expect and got

        runs_with_agent_errors += any(a.get("error") for a in run.get("agents", []))
        latencies.append(run["wall_ms"])
        calls.append(run["llm_calls"])
        prompt_toks.append(run["prompt_tokens"])
        completion_toks.append(run["completion_tokens"])
        reasoning_toks.append(run["reasoning_tokens"])

    totals = {k: sum(per_cat[c][k] for c in CATEGORIES) for k in per_cat[CATEGORIES[0]]}
    for name, d in [*per_cat.items(), ("ALL", totals)]:
        for k, v in d.items():
            m[f"{name}.{k}"] = v
        m[f"{name}.precision"] = _ratio(d["tp"], d["tp"] + d["fp"])
        m[f"{name}.recall"] = _ratio(d["matched"], d["issues"])

    m["severity.matched"] = sev_total
    m["severity.exact"] = _ratio(sev_exact, sev_total)
    m["severity.within_one"] = _ratio(sev_within_one, sev_total)
    m["critical.total"] = critical_total
    m["critical.false"] = critical_false
    m["critical.false_rate"] = _ratio(critical_false, critical_total)
    m["critical.clean_cases_with_critical"] = clean_with_critical
    m["block.cases"] = len(results)
    m["block.accuracy"] = _ratio(block_correct, len(results))
    m["block.false_blocks"] = false_blocks
    m["block.missed_blocks"] = missed_blocks
    m["block.precision"] = _ratio(caught_blocks, blocks)        # blocked cases that should block
    m["block.recall"] = _ratio(caught_blocks, expected_blocks)  # should-block cases that were blocked
    m["ops.runs_with_agent_errors"] = runs_with_agent_errors
    m["ops.latency_ms_mean"] = mean(latencies) if latencies else None
    m["ops.latency_ms_p50"] = _percentile(latencies, 0.50)
    m["ops.latency_ms_p95"] = _percentile(latencies, 0.95)
    m["ops.llm_calls_mean"] = mean(calls) if calls else None
    m["ops.prompt_tokens_mean"] = mean(prompt_toks) if prompt_toks else None
    m["ops.completion_tokens_mean"] = mean(completion_toks) if completion_toks else None
    m["ops.reasoning_tokens_mean"] = mean(reasoning_toks) if reasoning_toks else None
    return m


def aggregate(per_repeat: list[dict]) -> dict:
    """name -> {"mean", "min", "max", "n"} across repeats, skipping None values."""
    out = {}
    for key in per_repeat[0]:
        values = [m[key] for m in per_repeat if m.get(key) is not None]
        out[key] = ({"mean": mean(values), "min": min(values), "max": max(values), "n": len(values)}
                    if values else {"mean": None, "min": None, "max": None, "n": 0})
    return out


def results_by_repeat(cases: list[tuple[dict, dict]]) -> list[list[tuple[dict, dict]]]:
    """cases: (labels, case_json) pairs. Returns one (labels, run) list per
    repeat; runs that crashed in the harness (harness_error set) are left out."""
    n = max((len(cj["runs"]) for _, cj in cases), default=0)
    passes = []
    for k in range(n):
        passes.append([(labels, cj["runs"][k]) for labels, cj in cases
                       if k < len(cj["runs"]) and not cj["runs"][k].get("harness_error")])
    return passes


def unmatched_findings(cases: list[tuple[dict, dict]]) -> list[dict]:
    """Every unmatched or acceptable finding of severity >= MEDIUM, for hand triage."""
    rows = []
    for labels, cj in cases:
        for k, run in enumerate(cj["runs"]):
            if run.get("harness_error"):
                continue
            for c in classify_findings(labels, run["findings"]):
                f = c["finding"]
                if c["status"] in (UNMATCHED, ACCEPTABLE) and counts_for_precision(f["severity"]):
                    rows.append({
                        "case": labels["id"], "repeat": k + 1, "category": f["category"],
                        "severity": f["severity"], "title": f["title"],
                        "lines": finding_lines(f), "acceptable": c["status"] == ACCEPTABLE,
                    })
    return rows
