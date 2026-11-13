RATE = 0.10


def gets_discount(is_member: bool, total: float) -> bool:
    """Members get the discount, and so does anyone spending over 100."""
    return is_member and total > 100


def price_after_discount(is_member: bool, total: float) -> float:
    return total * (1 - RATE) if gets_discount(is_member, total) else total
