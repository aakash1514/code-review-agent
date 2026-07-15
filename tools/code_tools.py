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