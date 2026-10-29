from app.billing import invoice_total


def test_invoice_total_sums_lines():
    assert invoice_total([{"qty": 2, "unit_cents": 150}, {"qty": 1, "unit_cents": 99}]) == 399


def test_invoice_total_empty():
    assert invoice_total([]) == 0
