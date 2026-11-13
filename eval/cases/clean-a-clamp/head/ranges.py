def is_between(value: float, low: float, high: float) -> bool:
    return low <= value <= high


def clamp(value: float, low: float, high: float) -> float:
    """`value` moved inside [low, high]."""
    if low > high:
        raise ValueError("low must not be greater than high")
    return max(low, min(value, high))
