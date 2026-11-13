import sqlite3


def customer_count(db: sqlite3.Connection) -> int:
    return db.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
