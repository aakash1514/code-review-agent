"""CSV customer import."""
from dataclasses import dataclass
from datetime import date


@dataclass
class Customer:
    email: str
    signup_date: date
    credit_limit_cents: int


def parse_row(row: dict[str, str]) -> Customer:
    """Turn one CSV row into a Customer.

    Raises ValueError with a message naming the bad column when the email is
    missing or has no '@', the signup date isn't YYYY-MM-DD or is in the future,
    or the credit limit isn't a non-negative whole number.
    """
    email = (row.get("email") or "").strip().lower()
    if "@" not in email:
        raise ValueError(f"email: invalid value {email!r}")
    try:
        signup = date.fromisoformat(row.get("signup_date", ""))
    except ValueError:
        raise ValueError(f"signup_date: not YYYY-MM-DD: {row.get('signup_date')!r}") from None
    if signup > date.today():
        raise ValueError("signup_date: in the future")
    raw_limit = (row.get("credit_limit") or "0").replace(",", "")
    if not raw_limit.isdigit():
        raise ValueError(f"credit_limit: not a non-negative integer: {raw_limit!r}")
    return Customer(email=email, signup_date=signup, credit_limit_cents=int(raw_limit) * 100)


def parse_rows(rows: list[dict[str, str]]) -> tuple[list[Customer], list[str]]:
    """Parse every row; returns (customers, errors) where errors are 'line N: message'."""
    customers, errors = [], []
    for n, row in enumerate(rows, start=2):
        try:
            customers.append(parse_row(row))
        except ValueError as e:
            errors.append(f"line {n}: {e}")
    return customers, errors
