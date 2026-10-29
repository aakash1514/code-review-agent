"""Pagination parameters for list endpoints."""

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def page_bounds(page: int, per_page: int | None) -> tuple[int, int]:
    """(offset, limit) for a 1-based page number.

    per_page defaults to DEFAULT_PAGE_SIZE and is clamped to 1..MAX_PAGE_SIZE.
    """
    if page < 1:
        raise ValueError("page must be >= 1")
    limit = per_page or DEFAULT_PAGE_SIZE
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    return (page - 1) * limit, limit
