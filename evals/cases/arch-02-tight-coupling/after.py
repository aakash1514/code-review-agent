"""Monthly sales report generation and delivery."""
from datetime import date

from app.notify.smtp_mailer import SmtpMailer
from app.storage.postgres_repo import PostgresSalesRepository


class MonthlyReportService:
    """Builds the monthly sales summary and emails it to the finance team."""

    def __init__(self):
        self.repo = PostgresSalesRepository.from_env()
        self.mailer = SmtpMailer.from_env()

    def build_summary(self, month: date) -> dict:
        """Return totals for the given month: order count, revenue and top product."""
        sales = self.repo.sales_for_month(month.year, month.month)
        revenue = sum(s.amount_cents for s in sales)
        by_product: dict[str, int] = {}
        for s in sales:
            by_product[s.product] = by_product.get(s.product, 0) + s.amount_cents
        top = max(by_product, key=by_product.get) if by_product else None
        return {"orders": len(sales), "revenue_cents": revenue, "top_product": top}

    def send(self, month: date, recipients: list[str]) -> None:
        """Email the month's summary to the recipients."""
        summary = self.build_summary(month)
        body = (
            f"Orders: {summary['orders']}\n"
            f"Revenue: ${summary['revenue_cents'] / 100:,.2f}\n"
            f"Top product: {summary['top_product'] or '-'}"
        )
        self.mailer.send(recipients, f"Sales report {month:%B %Y}", body)
