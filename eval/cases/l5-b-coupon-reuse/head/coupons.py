from dataclasses import dataclass


@dataclass
class Coupon:
    amount: float
    used: bool = False


COUPONS: dict[str, Coupon] = {}


def is_valid(code: str) -> bool:
    coupon = COUPONS.get(code)
    return coupon is not None and not coupon.used


def redeem(code: str, total: float) -> float:
    """The total after the coupon; the coupon can be used once."""
    if not is_valid(code):
        raise ValueError("invalid coupon")
    return max(total - COUPONS[code].amount, 0.0)
