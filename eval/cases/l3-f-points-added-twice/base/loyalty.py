POINTS: dict[str, int] = {}


def checkout(customer: str, total: float) -> float:
    POINTS[customer] = POINTS.get(customer, 0) + int(total)
    return total
