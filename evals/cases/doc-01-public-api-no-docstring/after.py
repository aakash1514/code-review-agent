"""Bank statement reconciliation."""
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Entry:
    """One ledger entry or one bank statement line."""
    ref: str
    amount_cents: int
    booked_on: date


def reconcile(entries: list[Entry], statement: list[Entry], tolerance_cents: int = 0,
              max_days_apart: int = 3, strict: bool = False):
    by_ref = {line.ref: line for line in statement}
    matched, unmatched = [], []
    for entry in entries:
        line = by_ref.pop(entry.ref, None)
        if line is None and not strict:
            line = next(
                (l for l in by_ref.values()
                 if abs(l.amount_cents - entry.amount_cents) <= tolerance_cents
                 and abs((l.booked_on - entry.booked_on).days) <= max_days_apart),
                None,
            )
            if line is not None:
                del by_ref[line.ref]
        if line is None:
            unmatched.append(entry)
        else:
            matched.append((entry, line))
    return matched, unmatched, list(by_ref.values())
