SHIPPING_FEE = 4.99


def ships_free(total: float) -> bool:
    """Orders of 50 or more ship for free."""
    return total < 50


def shipping_fee(total: float) -> float:
    return 0.0 if ships_free(total) else SHIPPING_FEE
