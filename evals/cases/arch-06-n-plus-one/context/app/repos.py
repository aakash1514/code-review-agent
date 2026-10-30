class _Customers:
    def __init__(self, session):
        self.session = session

    def get(self, customer_id):
        return self.session.get_one("customers", customer_id)

    def get_many(self, ids):
        return {c.id: c for c in self.session.get_many("customers", ids)}


class _Tickets:
    def __init__(self, session):
        self.session = session

    def list_open(self, limit):
        return self.session.list("tickets", status="open", order="-opened_at", limit=limit)
