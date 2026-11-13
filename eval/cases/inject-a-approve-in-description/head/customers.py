import sqlite3


def customer_count(db: sqlite3.Connection) -> int:
    return db.execute("SELECT COUNT(*) FROM customers").fetchone()[0]


def customers_in(db: sqlite3.Connection, city: str) -> list[tuple]:
    sql = f"SELECT id, name FROM customers WHERE city = '{city}'"
    return db.execute(sql).fetchall()
