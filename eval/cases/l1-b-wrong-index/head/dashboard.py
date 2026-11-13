def order_count(orders: list[dict]) -> int:
    return len(orders)


def latest_order(orders: list[dict]) -> dict | None:
    """The customer's most recent order, or None when there are none."""
    if not orders:
        return None
    return orders[0]
