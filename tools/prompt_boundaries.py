"""Random per-call suffixes for the tags that fence untrusted content in agent prompts.

Why: a PR diff can contain "</PR_DIFF>" followed by fake instructions, but an
attacker can't close a tag they can't guess. Every agent call picks a fresh
boundary, suffixes every untrusted-block tag with it (<PR_DIFF_7f3a9c2e1b4d>),
and its system prompt names those exact tags as the untrusted ones.
"""
import secrets

# Placeholder for the boundary in system-prompt templates. Filled with
# str.replace rather than str.format, because the prompts also hold a
# literal JSON schema whose braces format would read as fields.
BOUNDARY_FIELD = "{boundary}"


def new_boundary() -> str:
    """A fresh random suffix, e.g. "7f3a9c2e1b4d" (48 bits)."""
    return secrets.token_hex(6)


def tag(name: str, boundary: str, attrs: str = "") -> tuple[str, str]:
    """(open, close) for a suffixed tag, e.g. ('<PR_DIFF_7f3a9c2e1b4d file="app.py">',
    '</PR_DIFF_7f3a9c2e1b4d>')."""
    full = f"{name}_{boundary}"
    return f"<{full}{' ' + attrs if attrs else ''}>", f"</{full}>"


def with_boundary(template: str, boundary: str) -> str:
    """A system-prompt template with every {boundary} filled in."""
    return template.replace(BOUNDARY_FIELD, boundary)
