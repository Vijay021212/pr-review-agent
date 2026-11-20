from orders import make_order


def receipt_line(order_id: int, total: float) -> str:
    order = make_order(order_id, total)
    return f"Order {order['id']}: {order['total']:.2f}"
