def average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)
