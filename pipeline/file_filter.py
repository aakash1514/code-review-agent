"""Decides which changed files of a PR get reviewed, by which agents, and in
what order, before any LLM call is made. Every file a review leaves out is
reported with its reason, so a skipped file is never silently unreviewed.

Settings (all optional; the defaults below apply when config.py lacks them):
  REVIEW_SKIP_PATTERNS  globs for lockfiles, generated and vendored paths.
                        "dir/" matches that directory anywhere in the path;
                        anything else is matched against the file name and
                        the full path.
  MAX_PATCH_CHARS       larger patches are skipped (keeps prompts under
                        Groq's request size limit).
  MAX_FILES_PER_REVIEW  reviewable files beyond this are listed as
                        "not reviewed (limit)", lowest priority first.
"""
import os
from dataclasses import dataclass
from fnmatch import fnmatch

from config import settings

DEFAULT_SKIP_PATTERNS = [
    # lockfiles
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "*.lock",
    # generated / vendored
    "dist/", "build/", "node_modules/", "*.min.js", "*.map",
    # licence text
    "LICENSE", "LICENSE.*",
]
DEFAULT_MAX_PATCH_CHARS = 20000
DEFAULT_MAX_FILES_PER_REVIEW = 25

DOCS_EXTENSIONS = (".md", ".txt", ".rst")
CONFIG_EXTENSIONS = (".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env", ".lock", ".xml")
CONFIG_NAMES = ("Dockerfile", "Makefile", "Procfile", ".gitignore", ".dockerignore", ".gitattributes")

# Review order: Python first (what the agents and the call graph are built
# for), then other source code, then config, then docs.
PRIORITY_PYTHON, PRIORITY_SOURCE, PRIORITY_CONFIG, PRIORITY_DOCS = range(4)

REASON_LIMIT = "not reviewed (limit)"


@dataclass
class ReviewTarget:
    filename: str
    patch: str
    docs_only: bool  # documentation agent only, not all four


@dataclass
class SkippedFile:
    filename: str
    reason: str


@dataclass
class FilePlan:
    to_review: list[ReviewTarget]
    skipped: list[SkippedFile]


def _setting(name: str, default):
    value = getattr(settings, name, None)
    return default if value is None else value


def _matching_skip_pattern(path: str, patterns: list[str]) -> str | None:
    parts = path.split("/")
    name = parts[-1]
    for pattern in patterns:
        if pattern.endswith("/"):
            if pattern.rstrip("/") in parts[:-1]:
                return pattern
        elif fnmatch(name, pattern) or fnmatch(path, pattern):
            return pattern
    return None


def file_priority(path: str) -> int:
    name = path.rsplit("/", 1)[-1]
    ext = os.path.splitext(name)[1].lower()
    if ext in DOCS_EXTENSIONS:
        return PRIORITY_DOCS
    if ext in CONFIG_EXTENSIONS or name in CONFIG_NAMES:
        return PRIORITY_CONFIG
    if ext == ".py":
        return PRIORITY_PYTHON
    return PRIORITY_SOURCE


def is_docs_file(path: str) -> bool:
    return path.lower().endswith(DOCS_EXTENSIONS)


def skip_reason(f: dict, patterns: list[str], max_patch_chars: int) -> str | None:
    """Why this GitHub PR file entry shouldn't be reviewed, or None."""
    path = f["filename"]
    if f.get("status") == "removed":
        return "removed"
    pattern = _matching_skip_pattern(path, patterns)
    if pattern:
        return f"skip pattern {pattern!r}"
    patch = f.get("patch")
    if not patch:
        # GitHub omits the patch for binary files (and for diffs too big to show).
        return "binary or no diff"
    if len(patch) > max_patch_chars:
        return f"patch too large ({len(patch):,} chars > {max_patch_chars:,})"
    return None


def plan_review_files(files: list[dict]) -> FilePlan:
    """Splits a PR's files (GitHub's pulls/{n}/files entries) into the ones
    to review, highest priority first and capped at MAX_FILES_PER_REVIEW,
    and the skipped ones with their reasons, in the PR's own order."""
    patterns = list(_setting("REVIEW_SKIP_PATTERNS", DEFAULT_SKIP_PATTERNS))
    max_patch_chars = int(_setting("MAX_PATCH_CHARS", DEFAULT_MAX_PATCH_CHARS))
    max_files = int(_setting("MAX_FILES_PER_REVIEW", DEFAULT_MAX_FILES_PER_REVIEW))

    reviewable, skipped = [], []
    for f in files:
        reason = skip_reason(f, patterns, max_patch_chars)
        if reason:
            skipped.append(SkippedFile(f["filename"], reason))
        else:
            reviewable.append(f)

    reviewable.sort(key=lambda f: file_priority(f["filename"]))  # stable: PR order within a tier
    to_review = [
        ReviewTarget(f["filename"], f["patch"], docs_only=is_docs_file(f["filename"]))
        for f in reviewable[:max_files]
    ]
    skipped.extend(SkippedFile(f["filename"], REASON_LIMIT) for f in reviewable[max_files:])
    return FilePlan(to_review=to_review, skipped=skipped)
