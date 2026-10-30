from datetime import date

from app.importer import parse_row


def test_parse_row_happy_path():
    c = parse_row({"email": " Ann@Example.com ", "signup_date": "2024-02-01", "credit_limit": "1,500"})
    assert c.email == "ann@example.com"
    assert c.signup_date == date(2024, 2, 1)
    assert c.credit_limit_cents == 150000
