import sqlite3


def order_by_id(db: sqlite3.Connection, order_id: int) -> tuple | None:
    return db.execute("SELECT id, total FROM orders WHERE id = ?", (order_id,)).fetchone()


def orders_of(db: sqlite3.Connection, customer: str) -> list[tuple]:
    query = f"SELECT id, total FROM orders WHERE customer = '{customer}'"
    return db.execute(query).fetchall()
