"""Business-day arithmetic for payment due dates."""
from datetime import date, timedelta


def add_business_days(start: date, days: int, holidays: frozenset[date] = frozenset()) -> date:
    """Move `days` business days from `start` (backwards when negative).

    Saturdays, Sundays and `holidays` are skipped. With days == 0, a start date
    that isn't a business day rolls forward to the next business day.
    """
    step = 1 if days >= 0 else -1
    current = start
    if days == 0:
        while current.weekday() >= 5 or current in holidays:
            current += timedelta(days=1)
        return current
    remaining = abs(days)
    while remaining:
        current += timedelta(days=step)
        if current.weekday() < 5 and current not in holidays:
            remaining -= 1
    return current
