import os

import psycopg


class PostgresSalesRepository:
    def __init__(self, conn):
        self.conn = conn

    @classmethod
    def from_env(cls):
        return cls(psycopg.connect(os.environ["SALES_DSN"]))

    def sales_for_month(self, year, month):
        cur = self.conn.execute(
            "SELECT product, amount_cents FROM sales WHERE extract(year from at) = %s AND extract(month from at) = %s",
            (year, month),
        )
        return cur.fetchall()
