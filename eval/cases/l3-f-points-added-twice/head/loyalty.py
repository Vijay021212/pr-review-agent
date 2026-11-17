POINTS: dict[str, int] = {}


def award_points(customer: str, total: float) -> None:
    POINTS.setdefault(customer, 0)
    POINTS[customer] += int(total)


def checkout(customer: str, total: float) -> float:
    award_points(customer, total)
    POINTS[customer] = POINTS.get(customer, 0) + int(total)
    return total
