"""Text helpers for URLs and display."""
import re
import unicodedata

_NON_WORD = re.compile(r"[^a-z0-9]+")


def slugify(title: str, max_length: int = 60) -> str:
    """URL slug for a title: ASCII lower-case words joined by '-', at most max_length chars.

    Accents are stripped ("Crème brûlée" -> "creme-brulee"); a title with no
    usable characters gives an empty string.
    """
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = _NON_WORD.sub("-", ascii_title.lower()).strip("-")
    return slug[:max_length].rstrip("-")
