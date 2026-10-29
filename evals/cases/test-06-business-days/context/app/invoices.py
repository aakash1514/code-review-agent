from app.calendar_utils import add_business_days


def due_date(issued_on, terms_days, holidays):
    return add_business_days(issued_on, terms_days, holidays)
