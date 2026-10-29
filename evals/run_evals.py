"""Offline eval harness for the review pipeline.

  python -m evals.run_evals run [--cases smoke|all|id1,id2] [--context none|kg]
                                [--no-severity-cap] [--repeat N] [--pause S]
                                [--out evals/runs/<name>]
  python -m evals.run_evals score <run_dir> [--md]

`run` reviews each golden case the way api/webhook._run_review_pipeline
reviews a PR file (file plan -> context -> agents -> build_review_report ->
HITL gate), minus GitHub, the database, WebSockets and the markdown
synthesis. The only network calls are the agents' Groq calls. Cases run one
at a time (Groq free-tier limits). Results go to <out>/<case_id>.json, one
entry per repeat under "runs"; an interrupted run resumes where it stopped
when started again with the same --out and flags.

`score` re-scores a run directory against the current labels with no LLM
calls (see evals/scoring.py).
"""
import argparse
import asyncio
import io
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from evals import scoring
from evals.golden import CASES_DIR, EVALS_DIR, Case, load_case, load_cases

RUNS_DIR = EVALS_DIR / "runs"


# --- run ---------------------------------------------------------------------

def disable_severity_cap() -> None:
    """Baseline ablation: the architecture agent's deterministic CRITICAL cap
    becomes a no-op in this process only. run_architecture_agent looks the
    function up as a module global on every call, so patching it works."""
    import agents.architecture_agent as architecture_agent
    architecture_agent._cap_routine_edge_case_severity = lambda findings: findings


def build_context(case: Case, patch: str, mode: str) -> tuple[str, dict]:
    """The retrieved-context string for the agents, and a summary of it.
    "kg": knowledge graph built from after + context/ in a temp dir, through
    SmartRetriever with no vector index (no Qdrant). "none": empty string."""
    if mode == "none":
        return "", {"mode": "none"}

    from rag.knowledge_graph import KnowledgeGraph
    from rag.retriever import SmartRetriever
    from tools.code_tools import extract_functions, strip_diff_to_source

    with tempfile.TemporaryDirectory(prefix=f"eval-{case.id}-") as tmp:
        if (case.dir / "context").is_dir():
            shutil.copytree(case.dir / "context", tmp, dirs_exist_ok=True)
        target = Path(tmp) / case.file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(case.after, encoding="utf-8")
        kg = KnowledgeGraph()
        kg.build_from_repo(tmp)

    retriever = SmartRetriever(kg, indexer=None, indexer_error="eval harness: no vector index")
    fn_names = [fn.name for fn in extract_functions(strip_diff_to_source(patch))]
    context = retriever.retrieve_for_diff(repo=f"eval/{case.id}", changed_file=case.file_path,
                                          diff_snippet=patch, changed_fn_names=fn_names)
    text = retriever.build_agent_context_string(context)
    return text, {"mode": "kg", "kg_functions": len(kg.nodes), "changed_functions": fn_names,
                  "call_chain": len(context["call_chain"]), "dependents": len(context["dependents"]),
                  "chars": len(text)}


class _Tee(io.TextIOBase):
    """Writes to the real stdout and keeps a copy (agent threads print too)."""

    def __init__(self, stream):
        self.stream, self.buffer_ = stream, io.StringIO()

    def write(self, s):
        self.stream.write(s)
        self.buffer_.write(s)
        return len(s)

    def flush(self):
        self.stream.flush()


def run_case(case: Case, context_mode: str) -> tuple[dict, str]:
    """Reviews one case like the webhook reviews one PR file. Returns the
    run record saved under "runs" in <case_id>.json, and the printed log."""
    from agents.documentation_agent import run_documentation_agent
    from pipeline.file_filter import plan_review_files
    from pipeline.hitl_gate import evaluate_hitl_gate
    from pipeline.runner import run_agents_parallel
    from pipeline.synthesizer import build_review_report
    from tools.model_client import count_llm_calls

    record = {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "harness_error": None}
    tee = _Tee(sys.stdout)
    started = time.monotonic()
    real_stdout, sys.stdout = sys.stdout, tee
    try:
        plan = plan_review_files([case.github_file()])
        record["skipped"] = [{"filename": s.filename, "reason": s.reason} for s in plan.skipped]
        reports, contexts = [], []
        with count_llm_calls() as counter:
            for target in plan.to_review:
                context_text, context_info = build_context(case, target.patch, context_mode)
                contexts.append(context_info)
                reports += asyncio.run(run_agents_parallel(
                    changed_file=target.filename,
                    diff_snippet=target.patch,
                    retrieved_context=context_text,
                    agent_functions=[run_documentation_agent] if target.docs_only else None,
                ))
        review = build_review_report(pr_number=0, repo=f"eval/{case.id}", commit_sha="eval",
                                     agent_reports=reports)
        gate = evaluate_hitl_gate(review)
        record.update({
            "wall_ms": int((time.monotonic() - started) * 1000),
            "llm_calls": counter.calls,
            "prompt_tokens": counter.prompt_tokens,
            "completion_tokens": counter.completion_tokens,
            "reasoning_tokens": counter.reasoning_tokens,
            "should_block": gate["should_block"],
            "gate_description": gate["description"],
            "context": contexts,
            "agents": [{"agent_name": r.agent_name, "error": r.error, "runtime_ms": r.runtime_ms,
                        "findings": len(r.findings), "summary": r.summary} for r in reports],
            "raw_findings": [{"agent": r.agent_name, **f.model_dump(mode="json")}
                             for r in reports for f in r.findings],
            "findings": [f.model_dump(mode="json") for f in review.all_findings],
        })
    except Exception as e:  # keep going with the other cases; scored as a failed run
        record.update({"wall_ms": int((time.monotonic() - started) * 1000),
                       "harness_error": f"{type(e).__name__}: {e}"})
    finally:
        sys.stdout = real_stdout
    log = tee.buffer_.getvalue()
    record["rate_limit_waits"] = log.count("[groq] rate-limited")
    record["model_fallbacks"] = log.count("[model_client]")
    return record, log


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True,
                              cwd=EVALS_DIR.parent).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def cmd_run(args) -> int:
    from config import settings

    cases = load_cases(args.cases)
    flags = {"cases": args.cases, "context": args.context, "severity_cap": not args.no_severity_cap,
             "repeat": args.repeat}
    out = Path(args.out) if args.out else RUNS_DIR / (
        datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{args.context}" + ("-nocap" if args.no_severity_cap else ""))
    out.mkdir(parents=True, exist_ok=True)
    (out / "logs").mkdir(exist_ok=True)

    meta_path = out / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta["flags"] != flags:
            print(f"{out} holds a run with different flags {meta['flags']}; use another --out")
            return 2
        meta.setdefault("resumed_at", []).append(datetime.now(timezone.utc).isoformat(timespec="seconds"))
    else:
        meta = {
            "git_sha": _git("rev-parse", "HEAD"),
            "git_dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
            "models": {"reasoning": settings.GROQ_MODEL_REASONING, "light": settings.GROQ_MODEL_LIGHT},
            "flags": flags,
            "pause_seconds": args.pause,
            "case_ids": [c.id for c in cases],
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "python": platform.python_version(),
        }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    if args.no_severity_cap:
        disable_severity_cap()

    first = True
    for k in range(args.repeat):
        for case in cases:
            case_path = out / f"{case.id}.json"
            case_json = (json.loads(case_path.read_text(encoding="utf-8")) if case_path.exists()
                         else {"case_id": case.id, "labels": case.labels, "runs": []})
            if len(case_json["runs"]) > k:
                continue  # already done (resumed run)
            if not first:
                time.sleep(args.pause)
            first = False
            print(f"\n=== [{k + 1}/{args.repeat}] {case.id}", flush=True)
            record, log = run_case(case, args.context)
            record["repeat"] = k + 1
            case_json["labels"] = case.labels
            case_json["runs"].append(record)
            case_path.write_text(json.dumps(case_json, indent=2), encoding="utf-8")
            (out / "logs" / f"{case.id}.r{k + 1}.log").write_text(log, encoding="utf-8")
            errors = [f"{a['agent_name']}: {a['error']}" for a in record.get("agents", []) if a["error"]]
            print(f"=== {case.id}: block={record.get('should_block')} findings={len(record.get('findings', []))} "
                  f"calls={record.get('llm_calls')} {record['wall_ms'] / 1000:.1f}s"
                  + (f" HARNESS ERROR {record['harness_error']}" if record["harness_error"] else "")
                  + (f" agent errors: {errors}" if errors else ""), flush=True)

    meta["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nresults in {out}; score with: python -m evals.run_evals score {out.as_posix()} --md")
    return 0


# --- score -------------------------------------------------------------------

def load_run(run_dir: Path) -> tuple[dict, list[tuple[dict, dict]]]:
    """(meta, [(labels, case_json)]) with labels from evals/cases when the
    case still exists there, else the snapshot saved with the run."""
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    cases = []
    for path in sorted(run_dir.glob("*.json")):
        if path.name == "meta.json":
            continue
        case_json = json.loads(path.read_text(encoding="utf-8"))
        case_dir = CASES_DIR / case_json["case_id"]
        labels = load_case(case_dir).labels if (case_dir / "labels.yaml").is_file() else case_json["labels"]
        cases.append((labels, case_json))
    return meta, cases


def _fmt(value, kind: str) -> str:
    if value is None:
        return "–"
    if kind == "pct":
        return f"{value * 100:.0f}%"
    if kind == "s":
        return f"{value / 1000:.1f}s"
    if kind == "int":
        return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"
    return f"{value:.2f}"


def _cell(agg: dict, key: str, kind: str) -> str:
    a = agg[key]
    text = _fmt(a["mean"], kind)
    if a["n"] > 1 and a["min"] != a["max"]:
        text += f" ({_fmt(a['min'], kind)}–{_fmt(a['max'], kind)})"
    return text


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    return (["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
            + ["| " + " | ".join(r) + " |" for r in rows])


def render_report(run_dir: Path, meta: dict, cases: list[tuple[dict, dict]]) -> str:
    passes = scoring.results_by_repeat(cases)
    per_repeat = [scoring.compute_metrics(p) for p in passes if p]
    if not per_repeat:
        return f"# Eval report: {run_dir.name}\n\nNo completed runs.\n"
    agg = scoring.aggregate(per_repeat)
    flags = meta.get("flags", {})
    failed = sum(1 for _, cj in cases for r in cj["runs"] if r.get("harness_error"))

    out = [f"# Eval report: {run_dir.name}", ""]
    out.append(f"- Commit `{meta.get('git_sha', '?')[:10]}`{' (dirty)' if meta.get('git_dirty') else ''}, "
               f"models {meta.get('models', {}).get('reasoning', '?')} / {meta.get('models', {}).get('light', '?')}")
    out.append(f"- Flags: context=`{flags.get('context')}`, severity cap {'on' if flags.get('severity_cap', True) else 'OFF'}, "
               f"cases=`{flags.get('cases')}`; {len(cases)} cases x {len(per_repeat)} repeat(s)"
               + (f"; {failed} run(s) crashed in the harness (excluded)" if failed else ""))
    if len(per_repeat) > 1:
        out.append("- Values are the mean over repeats, with (min–max) when they differ.")
    out.append("- Precision counts findings >= MEDIUM; recall counts an issue found at any severity; "
               f"line match tolerance ±{scoring.LINE_TOLERANCE}.")
    out.append(f"- **Acceptable findings >= MEDIUM (excluded from FP): {_cell(agg, 'ALL.acceptable', 'int')}** "
               "(mostly TEST_COVERAGE on new code; the agent sees no test files).")

    out += ["", "## Per category", ""]
    rows = []
    for cat in (*scoring.CATEGORIES, "ALL"):
        rows.append([cat, _cell(agg, f"{cat}.issues", "int"), _cell(agg, f"{cat}.recall", "pct"),
                     _cell(agg, f"{cat}.precision", "pct"), _cell(agg, f"{cat}.tp", "int"),
                     _cell(agg, f"{cat}.fp", "int"), _cell(agg, f"{cat}.dup", "int"),
                     _cell(agg, f"{cat}.acceptable", "int"), _cell(agg, f"{cat}.low", "int")])
    out += _table(["Category", "Issues", "Recall", "Precision", "TP", "FP", "Dup", "Acceptable", "LOW/INFO"], rows)

    out += ["", "## Severity, blocking", ""]
    out += _table(["Metric", "Value"], [
        ["Matched issues with exact severity", _cell(agg, "severity.exact", "pct")],
        ["Matched issues within one level", _cell(agg, "severity.within_one", "pct")],
        ["CRITICAL findings", _cell(agg, "critical.total", "int")],
        ["False-CRITICAL rate", _cell(agg, "critical.false_rate", "pct")],
        ["Clean cases with any CRITICAL", _cell(agg, "critical.clean_cases_with_critical", "int")],
        ["Block accuracy", _cell(agg, "block.accuracy", "pct")],
        ["False blocks", _cell(agg, "block.false_blocks", "int")],
        ["Missed blocks", _cell(agg, "block.missed_blocks", "int")],
    ])

    out += ["", "## Ops (per case)", ""]
    out += _table(["Metric", "Value"], [
        ["Latency mean", _cell(agg, "ops.latency_ms_mean", "s")],
        ["Latency p50", _cell(agg, "ops.latency_ms_p50", "s")],
        ["Latency p95", _cell(agg, "ops.latency_ms_p95", "s")],
        ["LLM calls", _cell(agg, "ops.llm_calls_mean", "int")],
        ["Prompt tokens", _cell(agg, "ops.prompt_tokens_mean", "int")],
        ["Completion tokens (incl. reasoning)", _cell(agg, "ops.completion_tokens_mean", "int")],
        ["Reasoning tokens", _cell(agg, "ops.reasoning_tokens_mean", "int")],
        ["Runs with an agent error", _cell(agg, "ops.runs_with_agent_errors", "int")],
    ])

    out += ["", "## Per case", ""]
    rows = []
    for labels, cj in cases:
        runs = [r for r in cj["runs"] if not r.get("harness_error")]
        if not runs:
            rows.append([labels["id"], str(labels["expect_block"]).lower(), "crashed", "", "", "", "", "", ""])
            continue
        found, fps = [], []
        for r in runs:
            classified = scoring.classify_findings(labels, r["findings"])
            found.append(sum(c["status"] == scoring.MATCH for c in classified))
            fps.append(sum(c["status"] == scoring.UNMATCHED and scoring.counts_for_precision(c["finding"]["severity"])
                           for c in classified))
        errors = sorted({f"{a['agent_name']}: {a['error']}"[:60] for r in runs for a in r["agents"] if a["error"]})
        n = len(runs)
        rows.append([
            labels["id"], str(labels["expect_block"]).lower(),
            "/".join(str(r["should_block"]).lower() for r in runs),
            "/".join(f"{f}/{len(labels.get('issues') or [])}" for f in found),
            "/".join(map(str, fps)),
            f"{sum(r['wall_ms'] for r in runs) / n / 1000:.1f}s",
            f"{sum(r['llm_calls'] for r in runs) / n:.1f}",
            f"{sum(r['prompt_tokens'] + r['completion_tokens'] for r in runs) / n:,.0f}",
            "; ".join(errors) + (f" (429 waits: {sum(r.get('rate_limit_waits', 0) for r in runs)})"
                                 if any(r.get("rate_limit_waits") for r in runs) else ""),
        ])
    out += _table(["Case", "Expect block", "Blocked", "Issues found", "FP >= MED", "Wall", "Calls",
                   "Tokens", "Errors"], rows)

    unmatched = scoring.unmatched_findings(cases)
    out += ["", f"## Unmatched findings >= MEDIUM ({len(unmatched)})", "",
            "False positives, or real issues the labels miss; triage by hand. "
            "`acceptable` ones match an acceptable entry and aren't counted as FP.", ""]
    out += _table(["Case", "Rep", "Category", "Severity", "Lines", "Title", "Acceptable"], [
        [u["case"], str(u["repeat"]), u["category"], u["severity"],
         "-" if u["lines"] is None else f"{u['lines'][0]}-{u['lines'][1]}",
         u["title"].replace("|", "\\|"), "yes" if u["acceptable"] else ""]
        for u in unmatched])
    return "\n".join(out) + "\n"


def cmd_score(args) -> int:
    run_dir = Path(args.run_dir)
    meta, cases = load_run(run_dir)
    if not cases:
        print(f"no case results in {run_dir}")
        return 2
    report = render_report(run_dir, meta, cases)
    print(report)
    if args.md:
        (run_dir / "report.md").write_text(report, encoding="utf-8")
        print(f"wrote {run_dir / 'report.md'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run_evals", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="review the golden cases with the live agents")
    run.add_argument("--cases", default="smoke", help="smoke | all | comma-separated case ids (default: smoke)")
    run.add_argument("--context", choices=("none", "kg"), default="kg")
    run.add_argument("--no-severity-cap", action="store_true",
                     help="baseline: turn off the architecture agent's CRITICAL cap")
    run.add_argument("--repeat", type=int, default=1)
    run.add_argument("--pause", type=float, default=10.0, help="seconds between cases (default 10)")
    run.add_argument("--out", help="run directory (default evals/runs/<timestamp>-<context>)")
    run.set_defaults(func=cmd_run)

    score = sub.add_parser("score", help="score a saved run (no LLM calls)")
    score.add_argument("run_dir")
    score.add_argument("--md", action="store_true", help="also write <run_dir>/report.md")
    score.set_defaults(func=cmd_score)

    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
