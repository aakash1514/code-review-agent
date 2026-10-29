"""The golden set: labelled review cases under evals/cases/<case_id>/.

Each case holds after.<ext> (the changed file; label line numbers refer to
it), an optional before.<ext> (missing = the change adds the file), an
optional context/ tree of sibling modules for the knowledge graph, and
labels.yaml (see evals/README.md):

  id, description, file_path, expect_block,
  issues:     [{id, category, lines: [start, end], severity, note?}]
  acceptable: [{category, lines: [start, end], note?}]   # optional
"""
import difflib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from pipeline.file_filter import plan_review_files

EVALS_DIR = Path(__file__).parent
CASES_DIR = EVALS_DIR / "cases"
SMOKE_FILE = EVALS_DIR / "smoke.txt"

CATEGORIES = ("SECURITY", "ARCHITECTURE", "TEST_COVERAGE", "DOCUMENTATION")
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Case:
    id: str
    dir: Path
    labels: dict
    after: str
    before: str | None
    after_name: str  # "after.py", "after.md", ...

    @property
    def file_path(self) -> str:
        return self.labels["file_path"]

    @property
    def expect_block(self) -> bool:
        return self.labels["expect_block"]

    @property
    def issues(self) -> list[dict]:
        return self.labels.get("issues") or []

    @property
    def acceptable(self) -> list[dict]:
        return self.labels.get("acceptable") or []

    @property
    def patch(self) -> str:
        return build_patch(self.before or "", self.after)

    def github_file(self) -> dict:
        """The case as one entry of GitHub's pulls/{n}/files response."""
        return {
            "filename": self.file_path,
            "status": "added" if self.before is None else "modified",
            "patch": self.patch,
        }


def build_patch(before: str, after: str) -> str:
    """GitHub-style patch: the unified diff's hunks only (no ---/+++ file
    headers), as get_pr_files returns it."""
    diff = difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=3)
    return "\n".join(list(diff)[2:])


def visible_new_lines(patch: str) -> list[tuple[int, int]]:
    """(start, end) ranges of after-file lines shown in the patch's hunks."""
    ranges = []
    for line in patch.splitlines():
        m = _HUNK_HEADER.match(line)
        if m:
            start, length = int(m.group(1)), int(m.group(2) or 1)
            if length:
                ranges.append((start, start + length - 1))
    return ranges


def _single(case_dir: Path, stem: str) -> Path | None:
    found = sorted(case_dir.glob(f"{stem}.*"))
    if len(found) > 1:
        raise ValueError(f"{case_dir.name}: more than one {stem}.* file")
    return found[0] if found else None


def load_case(case_dir: Path) -> Case:
    after_path = _single(case_dir, "after")
    if after_path is None:
        raise ValueError(f"{case_dir.name}: no after.* file")
    before_path = _single(case_dir, "before")
    labels = yaml.safe_load((case_dir / "labels.yaml").read_text(encoding="utf-8"))
    return Case(
        id=case_dir.name,
        dir=case_dir,
        labels=labels,
        after=after_path.read_text(encoding="utf-8"),
        before=before_path.read_text(encoding="utf-8") if before_path else None,
        after_name=after_path.name,
    )


def all_case_ids(cases_dir: Path = CASES_DIR) -> list[str]:
    return sorted(p.name for p in cases_dir.iterdir() if (p / "labels.yaml").is_file())


def smoke_case_ids(smoke_file: Path = SMOKE_FILE) -> list[str]:
    lines = smoke_file.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def load_cases(selection: str = "all", cases_dir: Path = CASES_DIR) -> list[Case]:
    """selection: "all", "smoke", or a comma-separated list of case ids."""
    if selection == "all":
        ids = all_case_ids(cases_dir)
    elif selection == "smoke":
        ids = smoke_case_ids()
    else:
        ids = [s.strip() for s in selection.split(",") if s.strip()]
    missing = [i for i in ids if not (cases_dir / i / "labels.yaml").is_file()]
    if missing:
        raise ValueError(f"unknown case id(s): {', '.join(missing)}")
    return [load_case(cases_dir / i) for i in ids]


def _check_lines(where: str, lines, n_lines: int) -> list[str]:
    if (not isinstance(lines, list) or len(lines) != 2
            or not all(isinstance(x, int) and not isinstance(x, bool) for x in lines)):
        return [f"{where}: lines must be [start, end] integers, got {lines!r}"]
    start, end = lines
    if not 1 <= start <= end <= n_lines:
        return [f"{where}: lines {lines} outside 1..{n_lines} or reversed"]
    return []


def validate_case(case: Case) -> list[str]:
    """Problems with the case's files and labels; empty when it's usable."""
    errors = []
    lab = case.labels or {}
    for key in ("id", "description", "file_path", "expect_block", "issues"):
        if key not in lab:
            errors.append(f"missing key {key!r}")
    if errors:
        return errors
    if lab["id"] != case.id:
        errors.append(f"id {lab['id']!r} doesn't match the directory name")
    if not isinstance(lab["expect_block"], bool):
        errors.append("expect_block must be true or false")
    if Path(case.file_path).suffix != Path(case.after_name).suffix:
        errors.append(f"file_path {case.file_path!r} and {case.after_name} have different extensions")

    n_lines = len(case.after.splitlines())
    patch = case.patch
    visible = visible_new_lines(patch)
    if not patch:
        errors.append("before and after are identical: empty patch")

    seen_ids = set()
    for i, issue in enumerate(case.issues):
        where = f"issue {issue.get('id', i)!r}"
        if issue.get("id") in seen_ids or not issue.get("id"):
            errors.append(f"{where}: missing or duplicate id")
        seen_ids.add(issue.get("id"))
        if issue.get("category") not in CATEGORIES:
            errors.append(f"{where}: bad category {issue.get('category')!r}")
        if issue.get("severity") not in SEVERITIES:
            errors.append(f"{where}: bad severity {issue.get('severity')!r}")
        line_errors = _check_lines(where, issue.get("lines"), n_lines)
        errors += line_errors
        if not line_errors:
            start, end = issue["lines"]
            if not any(start <= v_end and v_start <= end for v_start, v_end in visible):
                errors.append(f"{where}: lines {issue['lines']} aren't in the patch, so no agent sees them")
    for i, acc in enumerate(case.acceptable):
        where = f"acceptable[{i}]"
        if acc.get("category") not in CATEGORIES:
            errors.append(f"{where}: bad category {acc.get('category')!r}")
        errors += _check_lines(where, acc.get("lines"), n_lines)

    has_critical = any(issue.get("severity") == "CRITICAL" for issue in case.issues)
    if isinstance(lab["expect_block"], bool) and lab["expect_block"] != has_critical:
        errors.append(f"expect_block is {lab['expect_block']} but the case "
                      f"{'has' if has_critical else 'has no'} CRITICAL issue")

    plan = plan_review_files([case.github_file()])
    if plan.skipped:
        errors.append(f"the file filter skips it: {plan.skipped[0].reason}")
    return errors
