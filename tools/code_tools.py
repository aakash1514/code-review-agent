import ast
import re
from dataclasses import dataclass


@dataclass
class ExtractedFunction:
    name: str
    start_line: int
    end_line: int
    source: str
    args: list[str]
    is_async: bool


def strip_diff_to_source(diff_text: str) -> str:
    """
    Best-effort reconstruction of a Python source fragment from a unified
    diff, for feeding to ast.parse()-based tools (extract_functions,
    calculate_complexity). Drops diff metadata (file headers, index lines,
    hunk headers) and diff markup (+/-/space prefixes), keeping context and
    added lines only — removed lines are dropped since they don't exist in
    the new version of the file.

    IMPORTANT: raw diff text is never valid Python (it's full of lines like
    "diff --git a/x.py b/x.py" and "@@ -4,3 +4,14 @@") — ast.parse() on
    unprocessed diff text throws SyntaxError immediately, which
    extract_functions/calculate_complexity catch gracefully and return
    empty results for. That failure is silent (no exception surfaces), so
    it's easy to ship code that "works" but never actually extracts
    anything. Always run diff text through this function before passing it
    to an ast-based tool.

    This produces a FRAGMENT, not the full file — a hunk that starts
    mid-function (rather than at a clean top-level boundary) can still fail
    to parse, since the fragment may be missing the function's opening
    line/indentation context. That failure still degrades gracefully (empty
    result), it just means some diffs won't yield any functions even after
    stripping. For full accuracy on any diff shape, fetch the complete file
    via tools.github_tools.get_full_file_content and parse that instead —
    this fragment approach is a zero-extra-network-call approximation, not
    a substitute for that when precision matters (e.g. Phase 6+).
    """
    lines = []
    for line in diff_text.splitlines():
        if line.startswith(("diff --git", "index ", "--- ", "+++ ", "@@ ")):
            continue
        # git's "\ No newline at end of file" marker (after either side of a
        # change); it isn't source, and left in it makes ast.parse fail.
        if line.startswith("\\"):
            continue
        if line.startswith("+"):
            lines.append(line[1:])
        elif line.startswith("-"):
            continue
        elif line.startswith(" "):
            lines.append(line[1:])
        else:
            lines.append(line)
    return "\n".join(lines)


def extract_functions(source: str) -> list[ExtractedFunction]:
    """
    Pulls every top-level and nested function/method out of a source string.
    NOTE: source must be clean parseable Python, not raw diff text — run
    diff text through strip_diff_to_source() first, or this silently
    returns [] on a SyntaxError (see that function's docstring for why).
    """
    functions = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return functions

    lines = source.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = node.end_lineno or start
            functions.append(ExtractedFunction(
                name=node.name,
                start_line=start,
                end_line=end,
                source="\n".join(lines[start - 1:end]),
                args=[a.arg for a in node.args.args],
                is_async=isinstance(node, ast.AsyncFunctionDef),
            ))

    return functions


_SQL_KEYWORDS = r"(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER)\b"
_SQL_RISK_PATTERNS = [
    re.compile(rf'f["\'].*{_SQL_KEYWORDS}.*\{{', re.IGNORECASE),
    re.compile(rf'["\'].*{_SQL_KEYWORDS}.*["\']\s*%\s*', re.IGNORECASE),
    re.compile(rf'["\'].*{_SQL_KEYWORDS}.*["\']\.format\(', re.IGNORECASE),
    re.compile(rf'["\'].*{_SQL_KEYWORDS}.*["\']\s*\+', re.IGNORECASE),
]


def detect_sql_patterns(code: str) -> list[dict]:
    """
    Regex-based, deliberately dumb pass to flag likely string-built SQL.
    Line-based, not AST-based — works fine directly on raw diff text (no
    stripping needed), since it's just scanning for suspicious substrings
    per line regardless of diff markup.
    """
    findings = []
    for i, line in enumerate(code.splitlines(), start=1):
        for pattern in _SQL_RISK_PATTERNS:
            if pattern.search(line):
                findings.append({
                    "line": i,
                    "code": line.strip(),
                    "reason": "Possible string-built SQL query (concatenation/formatting near SQL keywords)",
                })
                break
    return findings


_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def new_file_lines(patch: str, added_only: bool = False) -> list[tuple[int, str]]:
    """(line number in the new file, text) for every added and context line
    of a GitHub-style patch, in order (only the added ones with added_only).
    Removed lines have no new-file number."""
    lines, n = [], None
    for line in patch.splitlines():
        header = _HUNK_HEADER.match(line)
        if header:
            n = int(header.group(1))
            continue
        if n is None or line.startswith(("\\", "+++ ", "--- ")):
            continue
        if line.startswith("-"):
            continue
        if line.startswith("+") or not added_only:
            lines.append((n, line[1:] if line[:1] in ("+", " ") else line))
        n += 1
    return lines


def _normalize(text: str) -> str:
    return " ".join(text.split())


def find_snippet_spans(snippet: str, numbered: list[tuple[int, str]]) -> list[tuple[int, int]]:
    """(first, last) new-file lines of every place snippet occurs in numbered
    (from new_file_lines), comparing with whitespace normalized: a multi-line
    snippet must match consecutive lines; a one-line snippet may also be part
    of a line. Empty snippet: no spans."""
    want = [_normalize(s) for s in snippet.splitlines() if s.strip()]
    lines = [(n, _normalize(text)) for n, text in numbered]
    spans = []
    if not want:
        return spans
    for i in range(len(lines) - len(want) + 1):
        window = lines[i:i + len(want)]
        if window[-1][0] - window[0][0] != len(want) - 1:
            continue  # crosses a hunk boundary (or a removed/context gap)
        if len(want) == 1:
            hit = want[0] in window[0][1]
        else:
            hit = all(w == line for w, (_, line) in zip(want, window))
        if hit:
            spans.append((window[0][0], window[-1][0]))
    return spans


def reanchor_finding_lines(findings: list, patch: str) -> list:
    """
    Agents see the patch without line numbers and guess line_start/line_end,
    which often don't point at the code they quote (KNOWN_ISSUES #10). This
    moves each finding to where its snippet sits among the patch's added and
    context lines, comparing with whitespace normalized: a multi-line snippet
    must match consecutive new-file lines; a one-line snippet may also be part
    of a line. With several matches, the one nearest the reported line_start
    wins. Findings with an empty snippet, or one that isn't found (it quotes
    removed lines, or is truncated), keep the model's lines. Returns copies.
    """
    numbered = new_file_lines(patch)
    out = []
    for f in findings:
        spans = find_snippet_spans(f.location.snippet, numbered)
        if spans:
            start, end = min(spans, key=lambda s: abs(s[0] - f.location.line_start))
            f = f.model_copy(update={"location": f.location.model_copy(
                update={"line_start": start, "line_end": end})})
        else:
            f = f.model_copy()
        out.append(f)
    return out


def calculate_complexity(source: str) -> dict[str, int]:
    """
    Approximate cyclomatic complexity per function.
    NOTE: source must be clean parseable Python — run diff text through
    strip_diff_to_source() first (see extract_functions docstring for why).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    results = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            complexity = 1
            for sub in ast.walk(node):
                if isinstance(sub, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler)):
                    complexity += 1
                elif isinstance(sub, ast.BoolOp):
                    complexity += len(sub.values) - 1
                elif isinstance(sub, ast.IfExp):
                    complexity += 1
                elif isinstance(sub, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                    complexity += len(sub.generators)
            results[node.name] = complexity

    return results