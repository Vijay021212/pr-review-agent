import sqlite3

COLUMNS = ("id", "customer", "total")


def export_rows(db: sqlite3.Connection, order_ids: list[int]) -> list[tuple]:
    rows = []
    for order_id in order_ids:
        row = db.execute("SELECT id, customer, total FROM orders WHERE id = ?", (order_id,))
        rows.append(row.fetchone())
    return rows
