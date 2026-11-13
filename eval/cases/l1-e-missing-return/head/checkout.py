SHIPPING = 4.99


def apply_discount(total: float, percent: float) -> float:
    """The total after a percentage discount."""
    discounted = total * (1 - percent / 100)


def checkout_total(total: float, percent: float = 0.0) -> float:
    return apply_discount(total, percent) + SHIPPING
