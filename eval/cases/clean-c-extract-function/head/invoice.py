TAX_RATE = 0.2


def tax(subtotal: float) -> float:
    return subtotal * TAX_RATE


def invoice_total(subtotal: float) -> float:
    return round(subtotal + tax(subtotal), 2)
