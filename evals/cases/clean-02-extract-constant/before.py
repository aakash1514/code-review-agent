"""Pagination parameters for list endpoints."""


def page_bounds(page: int, per_page: int | None) -> tuple[int, int]:
    """(offset, limit) for a 1-based page number; per_page defaults to 50, max 200."""
    if page < 1:
        raise ValueError("page must be >= 1")
    limit = per_page or 50
    limit = max(1, min(limit, 200))
    return (page - 1) * limit, limit
