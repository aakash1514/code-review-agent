"""Data for the support team's open-tickets dashboard."""
from app.repos import customers, tickets


def open_ticket_rows(limit: int = 500) -> list[dict]:
    """One row per open ticket with the customer's name and plan, newest first."""
    rows = []
    for ticket in tickets.list_open(limit=limit):
        customer = customers.get(ticket.customer_id)
        rows.append({
            "ticket_id": ticket.id,
            "subject": ticket.subject,
            "opened_at": ticket.opened_at.isoformat(),
            "customer": customer.name,
            "plan": customer.plan,
        })
    return rows
