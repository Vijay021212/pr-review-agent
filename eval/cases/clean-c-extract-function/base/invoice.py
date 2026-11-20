TAX_RATE = 0.2


def invoice_total(subtotal: float) -> float:
    return round(subtotal + subtotal * TAX_RATE, 2)
