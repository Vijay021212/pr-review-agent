def make_order(order_id: int, total: float) -> dict[str, int]:
    return {"id": order_id, "total_cents": round(total * 100)}
