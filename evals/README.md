# Offline evals

Measures the review pipeline against a golden set of labelled changes:
per-category precision and recall, severity agreement, false-CRITICAL rate,
merge-block accuracy, latency, LLM calls and tokens per review.

```bash
# live run: Groq calls only (no GitHub, Qdrant or AWS); cases run one at a time
python -m evals.run_evals run --cases smoke --out evals/runs/smoke-1
# re-score a saved run against the current labels, no LLM calls
python -m evals.run_evals score evals/runs/smoke-1 --md
```

`run` options: `--cases smoke|all|id1,id2` (default `smoke`), `--context kg|none`
(default `kg`: knowledge graph built from the case's files, no vector index),
`--no-severity-cap` (baseline: the architecture agent's CRITICAL cap turned off
in the harness process only), `--repeat N`, `--pause S` between cases. Re-running
with the same `--out` and flags resumes an interrupted run.

Each case is reviewed like one PR file in `api/webhook._run_review_pipeline`:
file plan → context → `run_agents_parallel` (docs-only files get the
documentation agent only) → `build_review_report` → `evaluate_hitl_gate`. The
markdown synthesis call is skipped. Results go to `<out>/<case_id>.json` (raw
and deduplicated findings, agent errors, `should_block`, wall time, LLM calls,
tokens, one entry per repeat), `<out>/logs/`, and `<out>/meta.json` (git SHA,
models, flags). `evals/runs/` is git-ignored.

## Golden cases

`cases/<case_id>/`:

- `after.py` (or `after.md`): the file after the change. Label line numbers refer to it.
- `before.py` (optional): the file before the change; missing means a new file.
- `context/` (optional): sibling modules laid out as repo paths (`context/app/x.py`,
  `context/tests/test_x.py`) so the knowledge graph has callers and callees.
- `labels.yaml`:

```yaml
id: sec-01-sql-injection
description: f-string SQL query built from a public search parameter
file_path: app/directory.py   # the path the agents see
expect_block: true            # true exactly when there's a CRITICAL issue
issues:                       # the seeded problems
  - id: sqli
    category: SECURITY        # SECURITY | ARCHITECTURE | TEST_COVERAGE | DOCUMENTATION
    lines: [19, 23]
    severity: CRITICAL
    note: "..."               # optional
acceptable:                   # optional: real-but-minor, not false positives
  - category: TEST_COVERAGE
    lines: [17, 24]
```

The patch is built with `difflib.unified_diff` and keeps only the hunks, as
GitHub's `pulls/{n}/files` returns them. `tests/test_evals_cases.py` validates
every case (categories, line ranges inside the file and inside the patch,
`expect_block` consistent with the CRITICAL issues, not skipped by the file
filter). `smoke.txt` lists the 8-case subset.

The clean and test-coverage cases put tests in `context/`.

## Scoring

See `scoring.py`. A finding matches an issue with the same category whose lines,
widened by ±3, overlap the finding's lines. Findings are matched most severe
first, and each issue matches at most once. Later findings on a matched issue
are duplicates, and findings on an `acceptable` range don't count. Precision
counts findings of MEDIUM or above. Recall counts an issue as found at any
severity. With `--repeat N`, the report gives the mean with the (min–max) range.
It also lists every unmatched finding of MEDIUM or above for hand triage.

## Limitations

- The test-coverage agent sees no test files, so it flags most new logic. In the
  security, architecture and documentation cases the new code is marked as an
  `acceptable` TEST_COVERAGE range, so those findings aren't counted as false
  positives. The report always shows the acceptable count, so this exclusion is
  visible.
- Modified files whose hunks start mid-function get no KG context, even with
  `--context kg` (`docs/KNOWN_ISSUES.md` #8; affects `sec-02`, `sec-08`,
  `arch-03`, `arch-05`).
- The architecture agent drops any finding that mentions "sql" or "database
  query", and `--no-severity-cap` doesn't turn that off (`docs/KNOWN_ISSUES.md` #9).
