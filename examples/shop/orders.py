"""Order lookups for the support dashboard."""

import sqlite3

PAGE_SIZE = 20


def find_orders(db: sqlite3.Connection, customer_name: str) -> list[tuple[int, float]]:
    """Every order of the customer with this name."""
    query = f"SELECT id, total FROM orders WHERE customer = '{customer_name}'"
    return db.execute(query).fetchall()


def page_of(orders: list[tuple[int, float]], page: int) -> list[tuple[int, float]]:
    """One page of orders. Pages are numbered from 1."""
    start = page * PAGE_SIZE
    return orders[start : start + PAGE_SIZE]
